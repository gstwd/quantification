"""CLI 命令行工具，提供因子定义初始化、回测和数据维护等功能。"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any

from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.config.settings import get_settings
from quant_etf_api.factors.registry import build_default_factor_registry
from quant_etf_api.infra.clients.akshare_index import _PE_PB_NAME_MAP, _calc_percentile
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.db.models.core import IndexValuationModel
from quant_etf_api.infra.time import today_cn
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.schemas.strategy import StrategyConfigCreate, StrategyConfigUpdate
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.factor_admin_service import FactorAdminService
from quant_etf_api.services.optimization_service import OptimizationService
from quant_etf_api.services.research_batch_service import ResearchBatchService
from quant_etf_api.services.robustness_service import SCAN_PRESETS, RobustnessService
from quant_etf_api.services.strategy_lifecycle_service import StrategyLifecycleService
from quant_etf_api.services.strategy_service import StrategyService

logger = logging.getLogger(__name__)


def init_factors() -> None:
    """将代码中的因子元数据（指数 + 行业）同步到数据库。

    同步策略：
    - 代码中有、DB 中没有 → INSERT（新因子）
    - 代码和 DB 都有 → 仅更新代码管控字段（version/required_data/四轴元数据）
    - DB 中有、代码中没有 → 设为 is_active=False
    """
    setup_logging()
    db = SessionLocal()
    try:
        registry = build_default_factor_registry()
        svc = FactorAdminService(db, registry)
        result = svc.sync_factor_definitions()
        print(
            f"因子定义同步完成: 新增={result['new']} 更新={result['updated']} 停用={result['deactivated']}"
        )
    except Exception:
        logger.error("因子定义同步失败", exc_info=True)
        print("因子定义同步失败，请查看日志", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def recompute_valuation_percentiles() -> None:
    """按统一口径重算 index_valuation 历史百分位并回填 source。

    B8 修复的一次性数据修复命令（幂等，可重复执行）：
    - 百分位改用 rank / (n - 1) * 100 的统一算法（含当日、最高值可达 100）；
    - source 回填为真实来源（legulegu / csindex）。
    仅基于已入库的 pe/pb 原始值重算，不重新拉取外部数据。
    更新使用 bulk_update_mappings 分批提交，避免远程库逐行 ORM flush 过慢。
    """
    setup_logging()
    db = SessionLocal()
    try:
        rows = (
            db.query(IndexValuationModel)
            .order_by(
                IndexValuationModel.index_code,
                IndexValuationModel.trade_date,
            )
            .all()
        )
        by_code: dict[str, list[IndexValuationModel]] = {}
        for row in rows:
            by_code.setdefault(row.index_code, []).append(row)

        mappings: list[dict[str, Any]] = []
        for index_code, code_rows in by_code.items():
            source = "legulegu" if index_code in _PE_PB_NAME_MAP else "csindex"
            pe_series: list[tuple[date, float]] = []
            pb_series: list[tuple[date, float]] = []
            for r in code_rows:
                if r.pe is not None:
                    pe_series.append((r.trade_date, r.pe))
                if r.pb is not None:
                    pb_series.append((r.trade_date, r.pb))
            pe_map = _calc_percentile(pe_series)
            pb_map = _calc_percentile(pb_series)
            for row in code_rows:
                new_pe_pct = pe_map.get(row.trade_date)
                new_pb_pct = pb_map.get(row.trade_date)
                if (
                    row.pe_percentile != new_pe_pct
                    or row.pb_percentile != new_pb_pct
                    or row.source != source
                ):
                    mappings.append(
                        {
                            "id": row.id,
                            "pe_percentile": new_pe_pct,
                            "pb_percentile": new_pb_pct,
                            "source": source,
                        }
                    )

        batch_size = 5000
        for i in range(0, len(mappings), batch_size):
            db.bulk_update_mappings(IndexValuationModel, mappings[i : i + batch_size])
        db.commit()
        print(f"估值百分位重算完成: 指数={len(by_code)} 更新行={len(mappings)}")
    except Exception:
        db.rollback()
        logger.error("估值百分位重算失败", exc_info=True)
        print("估值百分位重算失败，请查看日志", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


def _fail(message: str) -> None:
    """输出错误信息到 stderr 并以非零码退出。"""
    print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


def _emit(data: Any, as_json: bool) -> None:
    """输出命令结果，默认 JSON，`--no-json` 时输出可读文本。

    Args:
        data: 输出数据。
        as_json: True 时 JSON 输出。
    """
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    else:
        print(data)


def _parse_cost_ladder(raw: str | None) -> list[float] | None:
    """解析逗号分隔的成本档位参数（C3）。

    Args:
        raw: 形如 "0,10,20,30,50" 的字符串；为空时返回 None。

    Returns:
        成本档位列表（基点）；未提供时返回 None。
    """
    if not raw:
        return None
    values: list[float] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            values.append(float(text))
        except ValueError:
            _fail(f"--cost-ladder 含非法数字: {text}")
    if not values:
        _fail("--cost-ladder 不能为空")
    return values


def _read_json_file(path: str) -> dict[str, Any]:
    """读取 UTF-8 JSON 配置文件。

    Args:
        path: 文件路径。

    Returns:
        解析后的字典。
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_json_any(path: str) -> Any:
    """读取 UTF-8 JSON 文件（不限制顶层结构，可能是列表）。

    Args:
        path: 文件路径。

    Returns:
        解析后的 JSON 对象。
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _resolve_knobs(args: argparse.Namespace) -> list[str] | None:
    """汇总 ``--knobs`` 与 ``--knobs-file`` 给出的关键旋钮清单（D-2）。

    关键旋钮清单让"最可疑的拟合参数"优先被扫描，而不是被
    ``--max-knobs`` 按业务重要性截断后遗漏。

    Args:
        args: argparse 解析结果（可能不含 knobs 相关字段）。

    Returns:
        去重后的旋钮路径列表；两者都未提供时返回 None。

    Raises:
        ValueError: 清单文件结构非法时抛出。
    """
    knobs: list[str] = []
    raw = getattr(args, "knobs", None)
    if raw:
        knobs.extend(item.strip() for item in raw.split(",") if item.strip())
    knobs_file = getattr(args, "knobs_file", None)
    if knobs_file:
        payload = _read_json_any(knobs_file)
        if isinstance(payload, dict):
            payload = payload.get("knobs")
        if not isinstance(payload, list) or not payload:
            raise ValueError("关键旋钮清单文件必须是路径列表，或含 knobs 列表的对象")
        knobs.extend(str(item).strip() for item in payload if str(item).strip())
    if not knobs:
        return None
    return list(dict.fromkeys(knobs))


def _add_json_flag(parser: argparse.ArgumentParser) -> None:
    """给子命令添加 --no-json 开关（默认 JSON 输出）。"""
    parser.add_argument("--no-json", action="store_true", help="以人类可读文本输出（默认 JSON）")


def _json_diff(
    config_a: dict[str, Any],
    config_b: dict[str, Any],
    label_a: str,
    label_b: str,
) -> str:
    """生成两个配置 JSON 的 unified diff 文本。"""
    text_a = json.dumps(config_a, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    text_b = json.dumps(config_b, ensure_ascii=False, indent=2, sort_keys=True).splitlines()
    return "\n".join(
        difflib.unified_diff(
            text_a,
            text_b,
            fromfile=label_a,
            tofile=label_b,
            lineterm="",
        )
    )


def _build_strategy_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 strategy 命令组（AI 优化用的策略配置工具）。"""
    group = subparsers.add_parser("strategy", help="策略配置工具（AI 优化用）")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("list", help="列出所有启用策略")
    _add_json_flag(p)

    p = sub.add_parser("show", help="查看策略详情（含完整 config_json）")
    p.add_argument("strategy_id")
    _add_json_flag(p)

    p = sub.add_parser("validate", help="校验候选配置 JSON")
    p.add_argument("--file", required=True)
    _add_json_flag(p)

    p = sub.add_parser("create", help="创建策略")
    p.add_argument("--id", dest="strategy_id", required=True)
    p.add_argument("--name", dest="display_name", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--version", default="1.0.0")
    p.add_argument("--description", default="")
    p.add_argument("--frequency", default="daily")
    p.add_argument("--draft", action="store_true", help="以草稿状态创建（优化候选）")
    _add_json_flag(p)

    p = sub.add_parser("update", help="更新策略（文件、状态、名称等）")
    p.add_argument("strategy_id")
    p.add_argument("--file", help="新的 config_json 文件")
    p.add_argument("--status", choices=["active", "draft", "disabled"])
    p.add_argument("--name", dest="display_name")
    p.add_argument("--version")
    p.add_argument("--description")
    _add_json_flag(p)

    p = sub.add_parser("diff", help="对比两个策略的配置差异")
    p.add_argument("strategy_a")
    p.add_argument("strategy_b")
    _add_json_flag(p)

    p = sub.add_parser(
        "prune-variants",
        help="清理稳健性验证派生的变体草稿策略（D-4，默认预演）",
    )
    p.add_argument("--batch", dest="batch_id", required=True, help="稳健性验证批次 ID")
    p.add_argument("--apply", action="store_true", help="真正删除（默认只预演）")
    p.add_argument(
        "--force",
        action="store_true",
        help="变体仍被回测引用时，连带删除这些回测记录",
    )
    _add_json_flag(p)

    p = sub.add_parser(
        "consume-validation",
        help="标记该策略的验证期数据已被消费（D-5，人工备注用）",
    )
    p.add_argument("strategy_id")
    p.add_argument("--note", help="消费说明（如「人工查看 2026 段表现」）")
    _add_json_flag(p)


def _build_backtest_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 backtest 命令组（AI 优化用的回测工具）。"""
    group = subparsers.add_parser("backtest", help="回测工具（AI 优化用）")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("run", help="创建并执行回测")
    p.add_argument("--strategy", dest="strategy_id", required=True)
    p.add_argument(
        "--start",
        type=date.fromisoformat,
        help="起始日期；支持任意跨度（研究期内 1 个月~10 年均可单次回测，无需分段），"
        "不传时默认末端前 2 年",
    )
    p.add_argument(
        "--end",
        type=date.fromisoformat,
        help="截止日期；purpose=research 时默认研究期末端（2025-12-31）",
    )
    p.add_argument("--universe", choices=["all", "subset"], default="all")
    p.add_argument("--index-codes", dest="index_codes", help="逗号分隔的指数代码")
    p.add_argument("--benchmark", dest="benchmark_index", default="000300")
    p.add_argument("--no-benchmark", action="store_true")
    p.add_argument(
        "--purpose",
        choices=["research", "validation", "monitor"],
        default="research",
        help="回测用途：research=研究期研究（不得越过研究期末端），"
        "validation=验证期验收，monitor=上线后监控",
    )
    p.add_argument("--purpose-reason", help="用途说明，写入留痕记录")
    p.add_argument("--cost-bps", type=float, help="净口径指标的单边成本（基点），默认 10")
    p.add_argument("--async", dest="async_mode", action="store_true", help="入队后台执行")
    p.add_argument(
        "--priority",
        type=int,
        default=0,
        help="队列优先级（越大越先执行），仅 --async 入队时生效",
    )
    _add_json_flag(p)

    p = sub.add_parser("list", help="列出回测（支持状态/用途/时间范围/日历来源过滤）")
    p.add_argument("--strategy", dest="strategy_id", help="按策略 ID 过滤")
    p.add_argument(
        "--status",
        choices=["pending", "running", "success", "failed", "cancelled"],
        help="按状态过滤",
    )
    p.add_argument("--purpose", choices=["research", "validation", "monitor"], help="按用途过滤")
    p.add_argument(
        "--calendar-source",
        dest="calendar_source",
        choices=["upstream", "database", "not_required"],
        help="按调仓日历来源过滤（C1）：审计同一配置是否跑在两套日历上",
    )
    p.add_argument("--created-from", type=date.fromisoformat, help="创建日期起点（含）")
    p.add_argument("--created-to", type=date.fromisoformat, help="创建日期终点（含）")
    p.add_argument(
        "--order-by",
        choices=["created_at", "started_at", "finished_at"],
        default="created_at",
        help="排序字段",
    )
    p.add_argument("--asc", action="store_true", help="升序（默认倒序）")
    p.add_argument("--limit", type=int, default=50, help="返回条数上限（最大 200）")
    _add_json_flag(p)

    p = sub.add_parser("cancel", help="请求取消回测（未开始直接取消，运行中协作退出）")
    p.add_argument("backtest_id")
    _add_json_flag(p)

    p = sub.add_parser("status", help="查看回测状态")
    p.add_argument("backtest_id")
    p.add_argument("--wait", action="store_true", help="轮询等待至终态")
    p.add_argument("--timeout", type=float, default=600.0, help="等待超时秒数")
    _add_json_flag(p)

    p = sub.add_parser("show", help="查看回测详情（含配置快照、口径指纹与多档成本）")
    p.add_argument("backtest_id")
    p.add_argument(
        "--cost-bps",
        type=float,
        help="净口径成本覆盖（基点，C3）；留空使用回测固化的成本",
    )
    p.add_argument(
        "--cost-ladder",
        help="多档成本覆盖，逗号分隔（如 0,10,20,30,50）；0 表示毛口径",
    )
    _add_json_flag(p)

    p = sub.add_parser("pool", help="查看有效候选池逐日时间线与剔除区间（C6）")
    p.add_argument("backtest_id")
    _add_json_flag(p)

    p = sub.add_parser("orphans", help="审计指向已删除回测的悬挂引用（C5）")
    p.add_argument("--limit", type=int, default=200, help="明细条数上限")
    _add_json_flag(p)

    p = sub.add_parser("delete", help="删除回测记录（C5，存在 JSONB 引用时需 --force）")
    p.add_argument("backtest_id")
    p.add_argument("--force", action="store_true", help="存在 JSONB 引用时仍强制删除")
    _add_json_flag(p)

    p = sub.add_parser(
        "prune-dangling-refs",
        help="清理 JSONB 中的悬挂回测引用（C5，默认预演）",
    )
    p.add_argument("--apply", action="store_true", help="实际写库（默认仅预演）")
    _add_json_flag(p)

    p = sub.add_parser("results", help="查看回测明细结果")
    p.add_argument("backtest_id")
    p.add_argument("--daily", action="store_true", help="包含每日组合绩效")
    p.add_argument("--index", action="store_true", help="包含每指数信号与收益")
    _add_json_flag(p)


def _build_lifecycle_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 lifecycle 命令组（上线后监控与诊断）。"""
    group = subparsers.add_parser("lifecycle", help="策略生命周期（上线后监控与诊断）")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("list", help="列出全部上线策略的生命周期摘要")
    _add_json_flag(p)

    p = sub.add_parser("show", help="查看单策略生命周期详情与最近的体检记录")
    p.add_argument("strategy_id")
    _add_json_flag(p)

    p = sub.add_parser("online", help="标记上线（冻结配置并生成研究期分布）")
    p.add_argument("strategy_id")
    p.add_argument("--live-at", type=date.fromisoformat, help="上线日期，默认今天")
    p.add_argument("--note", help="上线备注（研究结论、上线依据）")
    p.add_argument("--cost-bps", type=float, help="净口径成本（基点），缺省取系统默认值")
    _add_json_flag(p)

    p = sub.add_parser("status", help="变更生命周期状态（仅人工触发）")
    p.add_argument("strategy_id")
    p.add_argument(
        "--set", dest="target_status", choices=["LIVE", "SUSPENDED", "RETIRED"], required=True
    )
    p.add_argument("--note", help="变更说明")
    _add_json_flag(p)

    p = sub.add_parser("refresh", help="刷新健康快照（同步执行一次监控区间回测）")
    p.add_argument("strategy_id")
    p.add_argument("--cost-bps", type=float, help="净口径成本（基点），缺省取系统默认值")
    _add_json_flag(p)


def _build_robustness_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 robustness 命令组（候选集级别的过拟合风险检验）。"""
    group = subparsers.add_parser(
        "robustness", help="稳健性验证（参数邻域 / 因子消融 / 资产池扰动 / 统计显著性）"
    )
    sub = group.add_subparsers(dest="subcommand", required=True)

    for name, help_text in (
        ("scan", "单旋钮邻域扰动：检查参数是否处于平台而非尖峰"),
        ("ablate", "因子消融：逐个移除评分因子与过滤条件"),
        ("pool", "资产池扰动：随机子池、剔除常持、剔除后上市指数"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--strategy", dest="strategy_id", required=True, help="基线策略 ID")
        p.add_argument(
            "--windows",
            type=int,
            default=None,
            help="研究期内切分的验证窗口数（缺省取预设：quick=2 / standard=4）",
        )
        if name == "scan":
            p.add_argument(
                "--max-knobs",
                type=int,
                default=None,
                help="单旋钮扰动数量上限（缺省取预设：quick=8 / standard=30）",
            )
            p.add_argument(
                "--preset",
                choices=sorted(SCAN_PRESETS),
                help="扫描预设：quick=轻量体检（2 窗口/8 旋钮），standard=完整（4/30）",
            )
            p.add_argument(
                "--knobs",
                help="关键旋钮清单（逗号分隔的配置路径，如 timing.thresholds.offensive_score）",
            )
            p.add_argument(
                "--knobs-file",
                help="关键旋钮清单文件（JSON 列表，或含 knobs 列表的对象）",
            )
        if name == "pool":
            p.add_argument("--samples", type=int, default=8, help="随机子池抽样次数")
        p.add_argument("--sync", dest="sync_mode", action="store_true", help="同步执行（默认入队）")
        p.add_argument(
            "--priority",
            type=int,
            default=0,
            help="队列优先级（越大越先执行），仅入队模式生效",
        )
        _add_json_flag(p)

    p = sub.add_parser("collect", help="等待并汇总批次结果")
    p.add_argument("robustness_id")
    p.add_argument("--wait", action="store_true", help="轮询等待至终态")
    p.add_argument("--timeout", type=float, default=3600.0, help="等待超时秒数")
    p.add_argument(
        "--allow-partial",
        action="store_true",
        help="允许按已完成窗口部分汇总（跳过未完成/失败窗口并标记 coverage）",
    )
    _add_json_flag(p)

    p = sub.add_parser("cancel", help="取消整批稳健性回测（运行中的在安全检查点退出）")
    p.add_argument("robustness_id")
    _add_json_flag(p)

    p = sub.add_parser("pause", help="暂停批次中尚未开始的任务")
    p.add_argument("robustness_id")
    _add_json_flag(p)

    p = sub.add_parser("resume", help="恢复批次中被暂停的任务")
    p.add_argument("robustness_id")
    _add_json_flag(p)

    p = sub.add_parser("stats", help="计算 CSCV-PBO / Deflated Sharpe / 自助法置信区间")
    p.add_argument("robustness_id")
    p.add_argument("--n-trials", type=int, help="计入多重检验的试验次数，缺省取台账值")
    p.add_argument("--cost-bps", type=float, help="净口径成本（基点），缺省取系统默认值")
    p.add_argument("--block", type=int, default=20, help="自助法块长度（交易日）")
    p.add_argument("--bootstrap", type=int, default=2000, help="自助抽样次数")
    _add_json_flag(p)

    p = sub.add_parser("show", help="查看批次详情")
    p.add_argument("robustness_id")
    _add_json_flag(p)

    p = sub.add_parser("list", help="列出最近的批次")
    p.add_argument("--limit", type=int, default=50)
    _add_json_flag(p)


def _build_research_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 research 命令组（研究批量评估，D-1）。"""
    group = subparsers.add_parser(
        "research", help="研究批量评估（变体 × 窗口的秒级离线评估，不落库）"
    )
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser(
        "batch",
        help="批量评估变体（复用平台执行路径但不落库；验收仍须走 backtest run）",
    )
    p.add_argument("--strategy", dest="strategy_id", required=True, help="基线策略 ID")
    p.add_argument(
        "--variants",
        required=True,
        help="变体文件（JSON 列表，或含 variants 列表的对象；每项给 config 或 patch）",
    )
    p.add_argument("--windows", type=int, default=5, help="研究期切分窗口数（默认 5 段）")
    p.add_argument("--cost-bps", type=float, help="净口径成本（基点），缺省取系统默认值")
    p.add_argument(
        "--cost-ladder",
        help="多档成本档位，逗号分隔（默认取系统配置，如 0,10,20,30,50）",
    )
    p.add_argument(
        "--no-baseline",
        action="store_true",
        help="不额外评估基线配置（默认总是把基线一起评估作为对照）",
    )
    _add_json_flag(p)


def _build_queue_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 queue 命令组（后台任务队列可观测性，B3）。"""
    group = subparsers.add_parser("queue", help="后台任务队列状态与任务明细")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("stats", help="查看积压/吞吐/运行中任务与并发预算")
    p.add_argument("--window-hours", type=float, default=1.0, help="吞吐统计窗口（小时）")
    _add_json_flag(p)

    p = sub.add_parser("jobs", help="查看最近的后台任务明细")
    p.add_argument(
        "--status",
        choices=["pending", "running", "success", "failed", "cancelled", "paused"],
        help="按状态过滤",
    )
    p.add_argument("--job-type", dest="job_type", help="按任务类型过滤")
    p.add_argument("--limit", type=int, default=50, help="返回条数上限")
    _add_json_flag(p)

    p = sub.add_parser("worker", help="以独立进程运行队列 worker（前台阻塞）")
    p.add_argument(
        "--recover-stuck",
        action="store_true",
        help="启动前把卡在 running 的任务恢复为失败（进程重启场景）",
    )


def _build_optimization_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 optimization 命令组（AI 自动优化闭环）。"""
    group = subparsers.add_parser("optimization", help="策略优化会话（AI 自动优化闭环）")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("start", help="开始优化会话（创建草稿候选 + 会话记录）")
    p.add_argument("--strategy", dest="strategy_id", required=True, help="基线策略 ID")
    p.add_argument("--candidate-file", required=True, help="候选配置 JSON 文件")
    p.add_argument("--hypothesis", required=True, help="本轮优化的假设")
    p.add_argument("--start", type=date.fromisoformat, help="评估起始日期，默认今天往前 2 年")
    p.add_argument("--end", type=date.fromisoformat, help="评估截止日期，默认今天")
    p.add_argument("--folds", type=int, default=4, help="验证窗口数量，默认 4")
    p.add_argument("--candidate-id", dest="candidate_strategy_id", help="候选策略 ID")
    p.add_argument("--version", dest="candidate_version", help="候选版本，默认继承基线")
    _add_json_flag(p)

    p = sub.add_parser("evaluate", help="运行全区间与逐折回测并汇总指标")
    p.add_argument("optimization_id")
    p.add_argument("--folds", type=int, help="验证窗口数量，缺省复用会话配置")
    p.add_argument("--async", dest="async_mode", action="store_true", help="入队后台执行")
    _add_json_flag(p)

    p = sub.add_parser("report", help="生成优化报告 Markdown 骨架")
    p.add_argument("optimization_id")
    p.add_argument("--file", help="写入报告文件路径")

    p = sub.add_parser("finish", help="结束会话并记录结论")
    p.add_argument("optimization_id")
    p.add_argument("--verdict", required=True, choices=["accept", "reject"])
    p.add_argument("--report-file", help="最终报告 Markdown 文件")
    p.add_argument("--promote", action="store_true", help="accept 时把候选配置写回基线")
    p.add_argument("--strict", action="store_true", help="强制验收清单全部通过")
    _add_json_flag(p)

    p = sub.add_parser("show", help="查看会话详情")
    p.add_argument("optimization_id")
    _add_json_flag(p)

    p = sub.add_parser("list", help="列出优化会话")
    p.add_argument("--strategy", dest="strategy_id")
    p.add_argument("--limit", type=int, default=50)
    _add_json_flag(p)


def _build_industry_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 industry 命令组（申万行业轮动子系统工具）。"""
    group = subparsers.add_parser("industry", help="申万行业 RRG/扩散子系统")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("init-universe", help="同步 31 个申万一级行业目录")
    _add_json_flag(p)

    p = sub.add_parser("backfill-bars", help="全量回填行业指数日线")
    p.add_argument("--codes", dest="industry_codes", help="逗号分隔的行业代码，默认全部")
    _add_json_flag(p)

    p = sub.add_parser("quality", help="批量重算并落库行业日线质量快照")
    p.add_argument("--all", action="store_true", help="全部行业（默认）")
    p.add_argument("--codes", dest="industry_codes", help="逗号分隔的行业代码")
    _add_json_flag(p)

    p = sub.add_parser("fill", help="批量补全行业日线到最近交易日")
    p.add_argument("--all", action="store_true", help="全部行业（默认）")
    p.add_argument("--codes", dest="industry_codes", help="逗号分隔的行业代码")
    _add_json_flag(p)

    p = sub.add_parser("rebuild", help="批量全量重拉行业日线（先拉取成功后清空旧行）")
    p.add_argument("--codes", dest="industry_codes", required=True, help="逗号分隔的行业代码")
    _add_json_flag(p)

    p = sub.add_parser("backfill-membership", help="同步申万行业成分事件")
    p.add_argument("--force", action="store_true", help="忽略最近刷新时间强制重取")
    _add_json_flag(p)

    p = sub.add_parser("backfill-stock-close", help="回填成分股历史收盘价")
    p.add_argument("--start", default="20130101", help="起始日 YYYYMMDD，默认 20130101")
    p.add_argument("--codes", dest="stock_codes", help="逗号分隔的股票代码，默认全部成分股")
    _add_json_flag(p)


def _run_industry(args: argparse.Namespace) -> None:
    """执行 industry 命令组。"""
    db = SessionLocal()
    try:
        from quant_etf_api.services.industry_data_service import (  # noqa: PLC0415
            IndustryDataService,
        )

        if args.subcommand == "init-universe":
            result = IndustryDataService(db).sync_universe()
            _emit(result, not args.no_json)
            return
        if args.subcommand == "backfill-bars":
            codes = _split_codes(args.industry_codes)
            result = IndustryDataService(db).backfill_industry_bars(codes)
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
        if args.subcommand == "quality":
            codes = _split_codes(args.industry_codes)
            if args.all:
                codes = None
            result = IndustryDataService(db).bulk_quality(codes)
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
        if args.subcommand in ("fill", "rebuild"):
            codes = _split_codes(args.industry_codes)
            if args.all or (args.subcommand == "fill" and codes is None):
                codes = None
            if args.subcommand == "rebuild" and not codes:
                _fail("rebuild 必须通过 --codes 指定要重拉的行业")
            if codes is None:
                from quant_etf_api.infra.db.repositories.industry import (  # noqa: PLC0415
                    IndustryUniverseRepository,
                )

                codes = IndustryUniverseRepository(db).find_active_codes()
            service = IndustryDataService(db)
            errors: list[str] = []
            items: list[dict[str, Any]] = []
            for code in codes:
                try:
                    items.append(
                        service.fill_industry(code)
                        if args.subcommand == "fill"
                        else service.rebuild_industry(code)
                    )
                except Exception as exc:
                    errors.append(f"{code}: {type(exc).__name__}: {exc}")
                    logger.warning("行业 %s %s 失败: %s", code, args.subcommand, exc)
            summary = {
                "codes": len(codes),
                "items": items,
                "errors": errors,
            }
            _emit(summary, not args.no_json)
            if errors:
                sys.exit(1)
            return
        if args.subcommand == "backfill-membership":
            result = IndustryDataService(db).refresh_membership(force=args.force)
            _emit(result, not args.no_json)
            return
        if args.subcommand == "backfill-stock-close":
            codes = _split_codes(args.stock_codes)
            from quant_etf_api.services.stock_data_service import (  # noqa: PLC0415
                StockDataService,
            )

            start_date = (
                datetime.strptime(args.start, "%Y%m%d").date()
                if isinstance(args.start, str)
                else args.start
            )
            result = StockDataService(db).bulk_fill(
                codes=codes,
                start_date=start_date,
                only_missing=False,
                rebuild=False,
            )
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
    except ValueError as exc:
        _fail(str(exc))
    finally:
        db.close()


def _build_index_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 index 命令组（指数成分数据管理）。"""
    group = subparsers.add_parser("index", help="指数成分数据（PIT/当前快照）")
    sub = group.add_subparsers(dest="subcommand", required=True)
    members = sub.add_parser("members", help="指数成分管理")
    msub = members.add_subparsers(dest="member_action", required=True)

    p = msub.add_parser("refresh", help="拉取并替换指数当前成分/权重快照")
    p.add_argument("--index-codes", dest="index_codes", help="逗号分隔指数代码，默认全部启用指数")
    _add_json_flag(p)

    p = msub.add_parser(
        "backfill-pit", help="按月末取样回填历史 PIT 成分（Tushare 优先/baostock 兜底）"
    )
    p.add_argument("--index-codes", dest="index_codes", default="000300,000905,000016")
    p.add_argument("--start", type=date.fromisoformat, default="2013-01-01")
    p.add_argument("--end", type=date.fromisoformat, default=None)
    _add_json_flag(p)

    p = msub.add_parser("status", help="查看各指数成分事件覆盖状态")
    p.add_argument("--index-codes", dest="index_codes", help="逗号分隔指数代码，默认全部启用指数")
    _add_json_flag(p)


def _run_index(args: argparse.Namespace) -> None:
    """执行 index 命令组。"""
    db = SessionLocal()
    try:
        from quant_etf_api.infra.db.repositories.benchmark_index import (  # noqa: PLC0415
            BenchmarkIndexRepository,
        )
        from quant_etf_api.services.index_membership_data_service import (  # noqa: PLC0415
            IndexMembershipDataService,
        )

        codes = _split_codes(args.index_codes)
        if codes is None:
            codes = [row.index_code for row in BenchmarkIndexRepository(db).find_active()]
        service = IndexMembershipDataService(db)
        if args.member_action == "refresh":
            result = service.refresh_current_snapshots(codes)
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
        if args.member_action == "backfill-pit":
            end = args.end or date.today()
            if args.start > end:
                _fail("--start 不能晚于 --end")
            result = service.backfill_pit(codes, args.start, end)
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
        if args.member_action == "status":
            _emit(service.status(codes), not args.no_json)
            return
    finally:
        db.close()


def _build_stock_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 stock 命令组（个股元数据/质量/补全，批量操作仅限 CLI）。"""
    group = subparsers.add_parser("stock", help="个股数据管理（质量快照与批量补全）")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("init-universe", help="同步 Tushare 沪深 A 股全量目录")
    _add_json_flag(p)

    p = sub.add_parser("quality", help="批量重算并落库个股数据质量快照")
    p.add_argument("--all", action="store_true", help="全部股票（默认）")
    p.add_argument("--codes", dest="stock_codes", help="逗号分隔股票代码")
    _add_json_flag(p)

    p = sub.add_parser("fill", help="批量补全个股日线到最近交易日")
    p.add_argument("--all", action="store_true", help="全部股票（默认）")
    p.add_argument("--codes", dest="stock_codes", help="逗号分隔股票代码")
    p.add_argument("--only-missing", action="store_true", help="仅处理缺失>0或无数据的股票")
    p.add_argument("--start", default="20130101", help="起始日 YYYYMMDD，默认 20130101")
    p.add_argument(
        "--datasets",
        default="daily,basic,moneyflow",
        help="逗号分隔数据集：daily/basic/moneyflow",
    )
    _add_json_flag(p)

    p = sub.add_parser("rebuild", help="批量全量重拉个股日线（先拉取成功后清空旧行）")
    p.add_argument("--codes", dest="stock_codes", required=True, help="逗号分隔股票代码")
    p.add_argument("--start", default="20130101", help="起始日 YYYYMMDD，默认 20130101")
    p.add_argument(
        "--datasets",
        default="daily,basic,moneyflow",
        help="逗号分隔数据集：daily/basic/moneyflow",
    )
    _add_json_flag(p)

    p = sub.add_parser("backfill-tushare", help="按交易日回填 Tushare 个股明细")
    p.add_argument("--start", default="20130101", help="起始日 YYYYMMDD，默认 20130101")
    p.add_argument("--end", default=None, help="结束日 YYYYMMDD，默认最近交易日")
    p.add_argument(
        "--datasets",
        default="daily,basic,moneyflow",
        help="逗号分隔数据集：daily/basic/moneyflow",
    )
    p.add_argument("--force", action="store_true", help="忽略已完成判断重新抓取")
    _add_json_flag(p)

    p = sub.add_parser("sync-day", help="同步指定交易日的 Tushare 个股明细")
    p.add_argument("--date", required=True, help="交易日 YYYYMMDD")
    p.add_argument(
        "--datasets",
        default="daily,basic,moneyflow",
        help="逗号分隔数据集：daily/basic/moneyflow",
    )
    _add_json_flag(p)

    p = sub.add_parser("prune-legacy", help="删除 2013 年前旧 close-only 行（默认预演）")
    p.add_argument("--before", default="20130101", help="截止日 YYYYMMDD（不含）")
    p.add_argument("--confirm-token", default=None, help="删除确认令牌")
    _add_json_flag(p)

    p = sub.add_parser("prune-non-tushare", help="删除非 Tushare 或非沪深 A 股旧行（默认预演）")
    p.add_argument("--start", default="20130101", help="起始日 YYYYMMDD（含）")
    p.add_argument("--confirm-token", default=None, help="删除确认令牌")
    _add_json_flag(p)


def _run_stock(args: argparse.Namespace) -> None:
    """执行 stock 命令组。"""
    db = SessionLocal()
    try:
        from quant_etf_api.services.stock_data_service import (  # noqa: PLC0415
            StockDataService,
        )

        service = StockDataService(db)
        if args.subcommand == "init-universe":
            result = service.sync_universe()
            _emit(result, not args.no_json)
            return

        if args.subcommand == "quality":
            codes = _split_codes(args.stock_codes)
            if args.all:
                codes = None
            result = service.bulk_quality(codes)
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
        if args.subcommand in ("fill", "rebuild"):
            codes = _split_codes(args.stock_codes)
            start_date = datetime.strptime(args.start, "%Y%m%d").date()
            if args.subcommand == "rebuild" and codes is None:
                _fail("rebuild 必须通过 --codes 指定要重拉的股票")
            if args.all:
                codes = None
            datasets = _split_codes(args.datasets)
            result = service.bulk_fill(
                codes=codes,
                start_date=start_date,
                only_missing=args.only_missing if args.subcommand == "fill" else False,
                rebuild=args.subcommand == "rebuild",
                datasets=datasets,
            )
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
        if args.subcommand == "backfill-tushare":
            start_date = datetime.strptime(args.start, "%Y%m%d").date()
            end_date = (
                datetime.strptime(args.end, "%Y%m%d").date()
                if args.end
                else service.latest_trading_day()
            )
            result = service.sync_range(
                start_date,
                end_date,
                datasets=_split_codes(args.datasets),
                force=args.force,
            )
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
        if args.subcommand == "sync-day":
            trade_date = datetime.strptime(args.date, "%Y%m%d").date()
            result = service.sync_trade_date(trade_date, datasets=_split_codes(args.datasets))
            _emit(result, not args.no_json)
            if result["errors"]:
                sys.exit(1)
            return
        if args.subcommand == "prune-legacy":
            before = datetime.strptime(args.before, "%Y%m%d").date()
            expected = f"PRUNE:stock_daily_close:BEFORE:{args.before}"
            dry_run = args.confirm_token != expected
            result = service.prune_before(before, dry_run=dry_run)
            if dry_run:
                result["confirm_token"] = expected
            _emit(result, not args.no_json)
            return
        if args.subcommand == "prune-non-tushare":
            start_date = datetime.strptime(args.start, "%Y%m%d").date()
            expected = f"PRUNE:stock_daily_close:NON_TUSHARE:{args.start}"
            dry_run = args.confirm_token != expected
            result = service.prune_non_tushare(start_date, dry_run=dry_run)
            if dry_run:
                result["confirm_token"] = expected
            _emit(result, not args.no_json)
            return
    finally:
        db.close()


def _split_codes(raw: str | None) -> list[str] | None:
    """把逗号分隔代码串转列表；为空返回 None。"""
    if not raw:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


def _run_strategy(args: argparse.Namespace) -> None:
    """执行 strategy 命令组。"""
    db = SessionLocal()
    try:
        svc = StrategyService(db)
        if args.subcommand == "list":
            rows = [r.model_dump() for r in svc.list_strategies()]
            _emit(rows, not args.no_json)
        elif args.subcommand == "show":
            detail = svc.get_strategy(args.strategy_id)
            if detail is None:
                _fail(f"策略 {args.strategy_id} 不存在")
            _emit(detail.model_dump(), not args.no_json)
        elif args.subcommand == "validate":
            config = _read_json_file(args.file)
            result = svc.validate_config(config)
            _emit(result.model_dump(), not args.no_json)
            if not result.valid:
                sys.exit(1)
        elif args.subcommand == "create":
            config = _read_json_file(args.file)
            req = StrategyConfigCreate(
                strategy_id=args.strategy_id,
                display_name=args.display_name,
                version=args.version,
                description=args.description,
                frequency=args.frequency,
                config_json=config,
                status="draft" if args.draft else "active",
            )
            detail = svc.create_config(req)
            _emit(detail.model_dump(), not args.no_json)
        elif args.subcommand == "update":
            update: dict[str, Any] = {}
            if args.file:
                update["config_json"] = _read_json_file(args.file)
            if args.status:
                update["status"] = args.status
            if args.display_name:
                update["display_name"] = args.display_name
            if args.version:
                update["version"] = args.version
            if args.description is not None:
                update["description"] = args.description
            detail = svc.update_config(args.strategy_id, StrategyConfigUpdate(**update))
            if detail is None:
                _fail(f"策略 {args.strategy_id} 不存在")
            _emit(detail.model_dump(), not args.no_json)
        elif args.subcommand == "diff":
            detail_a = svc.get_strategy(args.strategy_a)
            detail_b = svc.get_strategy(args.strategy_b)
            if detail_a is None or detail_b is None:
                _fail("对比策略不存在")
            diff = _json_diff(
                detail_a.config_json,
                detail_b.config_json,
                args.strategy_a,
                args.strategy_b,
            )
            if args.no_json:
                print(diff)
            else:
                _emit({"diff": diff}, True)
        elif args.subcommand == "prune-variants":
            result = svc.prune_variants(
                args.batch_id,
                dry_run=not args.apply,
                force=args.force,
            )
            _emit(result, not args.no_json)
        elif args.subcommand == "consume-validation":
            if not svc.mark_validation_consumed(args.strategy_id, args.note):
                _fail(f"策略 {args.strategy_id} 不存在")
            _emit(
                {
                    "strategy_id": args.strategy_id,
                    "validation_consumed": True,
                    "note": args.note,
                },
                not args.no_json,
            )
    except ValueError as exc:
        _fail(str(exc))
    finally:
        db.close()


def _run_backtest(args: argparse.Namespace) -> None:
    """执行 backtest 命令组。"""
    db = SessionLocal()
    try:
        svc = BacktestService(db)
        if args.subcommand == "run":
            # 研究类回测默认落在研究期末端：这样"不带日期"的调用天然处于研究期，
            # 需要看验证期数据时必须显式声明用途，从而留下审计痕迹
            settings = get_settings()
            research_start = date.fromisoformat(settings.research_period_start)
            research_end = date.fromisoformat(settings.research_period_end)
            purpose = getattr(args, "purpose", "research")
            end = args.end or (research_end if purpose == "research" else today_cn())
            start = args.start or (end - timedelta(days=730))
            if start < research_start:
                start = research_start
            if args.start and args.end and args.start > args.end:
                _fail("--start 不能晚于 --end")
            index_codes = [
                code.strip() for code in (args.index_codes or "").split(",") if code.strip()
            ]
            req = BacktestCreateRequest(
                strategy_id=args.strategy_id,
                start_date=start,
                end_date=end,
                universe_mode=args.universe,
                index_codes=index_codes,
                enable_benchmark=not args.no_benchmark,
                benchmark_index_code=args.benchmark_index,
                purpose=purpose,
                purpose_reason=getattr(args, "purpose_reason", None),
                cost_bps=getattr(args, "cost_bps", None),
            )
            summary = svc.create_backtest(req)
            if args.async_mode:
                from quant_etf_api.infra.job_queue.queue import (
                    backtest_job_key,
                    get_job_queue,
                )

                get_job_queue().enqueue(
                    "backtest",
                    {"backtest_id": summary.backtest_id},
                    job_key=backtest_job_key(summary.backtest_id),
                    priority=getattr(args, "priority", 0),
                )
                _emit(summary.model_dump(), not args.no_json)
                return
            svc.run_backtest(summary.backtest_id)
            detail = svc.get_backtest(summary.backtest_id)
            if detail is None:
                _fail("回测执行后详情不可用")
            _emit(detail.model_dump(), not args.no_json)
            if detail.status != "success":
                sys.exit(1)
        elif args.subcommand == "list":
            items, total = svc.list_backtests(
                offset=0,
                limit=min(max(1, args.limit), 200),
                strategy_id=args.strategy_id,
                created_from=args.created_from,
                created_to=args.created_to,
                status=args.status,
                purpose=args.purpose,
                order_by=args.order_by,
                descending=not args.asc,
                calendar_source=args.calendar_source,
            )
            _emit(
                {
                    "total": total,
                    "items": [item.model_dump() for item in items],
                },
                not args.no_json,
            )
        elif args.subcommand == "cancel":
            _emit(svc.cancel_backtest(args.backtest_id), not args.no_json)
        elif args.subcommand == "status":
            detail = svc.get_backtest(args.backtest_id)
            if detail is None:
                _fail(f"回测 {args.backtest_id} 不存在")
            if args.wait:
                deadline = time.monotonic() + args.timeout
                while detail.status in ("pending", "running"):
                    if time.monotonic() >= deadline:
                        _emit(detail.model_dump(), not args.no_json)
                        sys.exit(2)
                    time.sleep(2)
                    detail = svc.get_backtest(args.backtest_id)
            _emit(detail.model_dump(), not args.no_json)
            if detail.status == "failed":
                sys.exit(1)
        elif args.subcommand == "show":
            detail = svc.get_backtest(
                args.backtest_id,
                cost_bps=getattr(args, "cost_bps", None),
                cost_ladder=_parse_cost_ladder(getattr(args, "cost_ladder", None)),
            )
            if detail is None:
                _fail(f"回测 {args.backtest_id} 不存在")
            _emit(detail.model_dump(), not args.no_json)
        elif args.subcommand == "pool":
            detail = svc.get_backtest(args.backtest_id)
            if detail is None:
                _fail(f"回测 {args.backtest_id} 不存在")
            pool = detail.stability.candidate_pool if detail.stability else None
            if pool is None:
                _fail(
                    f"回测 {args.backtest_id} 无候选池时间线"
                    "（存量回测早于 C6 字段，或回测未成功完成）"
                )
            _emit(
                {
                    "backtest_id": args.backtest_id,
                    "base_size": pool.base_size,
                    "min_size": pool.min_size,
                    "max_size": pool.max_size,
                    "median_size": pool.median_size,
                    "pool_coverage_ratio": pool.pool_coverage_ratio,
                    "truncated_exclusions": pool.truncated_exclusions,
                    "segments": [seg.model_dump() for seg in pool.segments],
                    "exclusions": [exc.model_dump() for exc in pool.exclusions],
                },
                not args.no_json,
            )
        elif args.subcommand == "orphans":
            report = svc.find_dangling_references(limit=args.limit)
            _emit(report.model_dump(), not args.no_json)
            if report.total:
                sys.exit(1)
        elif args.subcommand == "delete":
            result = svc.delete_backtest(args.backtest_id, force=args.force)
            _emit(result.model_dump(), not args.no_json)
        elif args.subcommand == "prune-dangling-refs":
            result = svc.prune_dangling_references(dry_run=not args.apply)
            _emit(result.model_dump(), not args.no_json)
        elif args.subcommand == "results":
            detail = svc.get_backtest(args.backtest_id)
            if detail is None:
                _fail(f"回测 {args.backtest_id} 不存在")
            data: dict[str, Any] = {"summary": detail.model_dump()}
            if args.daily:
                data["daily"] = [r.model_dump() for r in svc.get_daily_results(args.backtest_id)]
            if args.index:
                data["index_results"] = [
                    r.model_dump() for r in svc.get_index_results(args.backtest_id)
                ]
            _emit(data, not args.no_json)
    except ValueError as exc:
        _fail(str(exc))
    finally:
        db.close()


def _run_lifecycle(args: argparse.Namespace) -> None:
    """执行 lifecycle 命令组（上线后监控与诊断）。"""
    from quant_etf_api.schemas.lifecycle import (
        LifecycleOnlineRequest,
        LifecycleRefreshRequest,
        LifecycleStatusRequest,
    )

    db = SessionLocal()
    try:
        svc = StrategyLifecycleService(db)
        if args.subcommand == "list":
            _emit([item.model_dump() for item in svc.list_lifecycles()], not args.no_json)
        elif args.subcommand == "show":
            detail = svc.get_lifecycle(args.strategy_id)
            if detail is None:
                _fail(f"策略 {args.strategy_id} 尚未标记上线")
            _emit(detail.model_dump(), not args.no_json)
        elif args.subcommand == "online":
            result = svc.online(
                args.strategy_id,
                LifecycleOnlineRequest(
                    live_at=args.live_at, note=args.note, cost_bps=args.cost_bps
                ),
            )
            _emit(result.model_dump(), not args.no_json)
        elif args.subcommand == "status":
            result = svc.update_status(
                args.strategy_id,
                LifecycleStatusRequest(status=args.target_status, note=args.note),
            )
            _emit(result.model_dump(), not args.no_json)
        elif args.subcommand == "refresh":
            result = svc.refresh(args.strategy_id, LifecycleRefreshRequest(cost_bps=args.cost_bps))
            _emit(result.model_dump(), not args.no_json)
    except ValueError as exc:
        _fail(str(exc))
    finally:
        db.close()


def _run_research(args: argparse.Namespace) -> None:
    """执行 research 命令组（研究批量评估，D-1）。"""
    db = SessionLocal()
    try:
        if args.subcommand != "batch":
            raise ValueError(f"未知的 research 子命令: {args.subcommand}")
        payload = _read_json_any(args.variants)
        svc = ResearchBatchService(db)
        variants = ResearchBatchService.parse_variants(
            svc.baseline_config(args.strategy_id), payload
        )
        result = svc.run(
            args.strategy_id,
            variants,
            windows=args.windows,
            cost_bps=args.cost_bps,
            cost_ladder=_parse_cost_ladder(args.cost_ladder),
            include_baseline=not args.no_baseline,
        )
        _emit(result.to_dict(), not args.no_json)
    except ValueError as exc:
        _fail(str(exc))
    finally:
        db.close()


def _run_robustness(args: argparse.Namespace) -> None:
    """执行 robustness 命令组。"""
    db = SessionLocal()
    try:
        svc = RobustnessService(db)
        if args.subcommand in ("scan", "ablate", "pool"):
            result = svc.create(
                strategy_id=args.strategy_id,
                kind=args.subcommand,
                windows=getattr(args, "windows", None),
                pool_samples=getattr(args, "samples", 8),
                max_knobs=getattr(args, "max_knobs", None),
                async_mode=not getattr(args, "sync_mode", False),
                priority=getattr(args, "priority", 0),
                knobs=_resolve_knobs(args),
                preset=getattr(args, "preset", None),
            )
            _emit(result, not args.no_json)
        elif args.subcommand == "collect":
            deadline = time.monotonic() + args.timeout
            while True:
                result = svc.collect(args.robustness_id, allow_partial=args.allow_partial)
                if not args.wait or result.get("status") != "running":
                    break
                if time.monotonic() >= deadline:
                    result["timeout"] = True
                    break
                time.sleep(5)
            _emit(result, not args.no_json)
            if result.get("status") == "failed":
                sys.exit(1)
        elif args.subcommand == "cancel":
            _emit(svc.cancel(args.robustness_id), not args.no_json)
        elif args.subcommand == "pause":
            _emit(svc.pause(args.robustness_id), not args.no_json)
        elif args.subcommand == "resume":
            _emit(svc.resume(args.robustness_id), not args.no_json)
        elif args.subcommand == "stats":
            result = svc.compute_statistics(
                args.robustness_id,
                n_trials=args.n_trials,
                cost_bps=args.cost_bps,
                block=args.block,
                n_bootstrap=args.bootstrap,
            )
            _emit(result, not args.no_json)
        elif args.subcommand == "show":
            detail = svc.get_run(args.robustness_id)
            if detail is None:
                _fail(f"稳健性验证批次 {args.robustness_id} 不存在")
            _emit(detail.model_dump(), not args.no_json)
        elif args.subcommand == "list":
            result = svc.list_runs(limit=args.limit)
            _emit(result.model_dump(), not args.no_json)
    except ValueError as exc:
        _fail(str(exc))
    finally:
        db.close()


def _run_optimization(args: argparse.Namespace) -> None:
    """执行 optimization 命令组。"""
    db = SessionLocal()
    try:
        svc = OptimizationService(db)
        if args.subcommand == "start":
            # 优化属于研究行为：默认截止在研究期末端，不得触碰验证期数据
            research_end = date.fromisoformat(get_settings().research_period_end)
            end = args.end or research_end
            start = args.start or (end - timedelta(days=730))
            candidate = _read_json_file(args.candidate_file)
            result = svc.start(
                strategy_id=args.strategy_id,
                candidate_config=candidate,
                hypothesis=args.hypothesis,
                start_date=start,
                end_date=end,
                folds=args.folds,
                candidate_strategy_id=args.candidate_strategy_id,
                candidate_version=args.candidate_version,
            )
            _emit(result, not args.no_json)
        elif args.subcommand == "evaluate":
            result = svc.evaluate(
                args.optimization_id,
                folds=args.folds,
                async_mode=args.async_mode,
            )
            _emit(result, not args.no_json)
        elif args.subcommand == "report":
            markdown = svc.generate_report(args.optimization_id)
            if args.file:
                with open(args.file, "w", encoding="utf-8") as f:
                    f.write(markdown)
                print(f"# 报告已写入: {args.file}")
            else:
                print(markdown)
        elif args.subcommand == "finish":
            report_text = None
            if args.report_file:
                with open(args.report_file, encoding="utf-8") as f:
                    report_text = f.read()
            result = svc.finish(
                args.optimization_id,
                args.verdict,
                report_text=report_text,
                promote=args.promote,
                strict=args.strict,
            )
            _emit(result, not args.no_json)
        elif args.subcommand == "show":
            result = svc.show(args.optimization_id)
            if result is None:
                _fail(f"优化会话 {args.optimization_id} 不存在")
            _emit(result, not args.no_json)
        elif args.subcommand == "list":
            result = svc.list(strategy_id=args.strategy_id, limit=args.limit)
            _emit(result, not args.no_json)
    except ValueError as exc:
        _fail(str(exc))
    finally:
        db.close()


def _run_queue(args: argparse.Namespace) -> None:
    """执行 queue 命令组（后台任务队列可观测性与独立 worker，B3）。"""
    if args.subcommand == "worker":
        from quant_etf_api.worker import main as worker_main

        worker_main()
        return
    from quant_etf_api.infra.job_queue.queue import get_job_queue, lane_for
    from quant_etf_api.infra.time import utcnow_aware

    queue = get_job_queue()
    if args.subcommand == "stats":
        _emit(queue.stats(window_hours=args.window_hours), not args.no_json)
        return
    rows = queue.list_jobs(
        statuses=[args.status] if args.status else None,
        job_types=[args.job_type] if args.job_type else None,
        limit=min(max(1, args.limit), 500),
    )
    now = utcnow_aware()
    items: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {
            "job_id": row.job_id,
            "job_type": row.job_type,
            "lane": lane_for(row.job_type),
            "batch_id": row.batch_id,
            "status": row.status,
            "priority": row.priority,
            "attempts": row.attempts,
            "max_attempts": row.max_attempts,
            "error_message": row.error_message,
            "cancel_requested": row.cancel_requested,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "heartbeat_at": row.heartbeat_at,
            "finished_at": row.finished_at,
        }
        if row.status == "pending" and row.created_at is not None:
            item["queued_seconds"] = round(max(0.0, (now - row.created_at).total_seconds()), 1)
        if row.status == "running" and row.started_at is not None:
            item["elapsed_seconds"] = round(max(0.0, (now - row.started_at).total_seconds()), 1)
        items.append(item)
    _emit({"total": len(items), "items": items}, not args.no_json)


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="量化研究平台 CLI 工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    subparsers.add_parser("init-factors", help="将代码中的因子元数据同步到数据库")
    subparsers.add_parser(
        "recompute-valuation-percentiles",
        help="按统一口径重算 index_valuation 历史百分位并回填 source",
    )
    _build_strategy_group(subparsers)
    _build_backtest_group(subparsers)
    _build_lifecycle_group(subparsers)
    _build_robustness_group(subparsers)
    _build_research_group(subparsers)
    _build_optimization_group(subparsers)
    _build_queue_group(subparsers)
    _build_industry_group(subparsers)
    _build_index_group(subparsers)
    _build_stock_group(subparsers)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    # 严格口径（C1）：交易日历不可用时统一转为可读错误（退出码 1），
    # 不打印 traceback，也不允许任何"按星期近似"的降级结果流出
    from quant_etf_api.domain.common.trading_calendar import (  # noqa: PLC0415
        TradingCalendarUnavailableError,
    )

    try:
        _dispatch(args)
    except TradingCalendarUnavailableError as exc:
        _fail(str(exc))


def _dispatch(args: argparse.Namespace) -> None:
    """按 command 分发到各命令组（供 main 统一兜底异常）。

    Args:
        args: argparse 解析结果。
    """
    if args.command == "strategy":
        _run_strategy(args)
    elif args.command == "backtest":
        _run_backtest(args)
    elif args.command == "lifecycle":
        _run_lifecycle(args)
    elif args.command == "robustness":
        _run_robustness(args)
    elif args.command == "research":
        _run_research(args)
    elif args.command == "optimization":
        _run_optimization(args)
    elif args.command == "queue":
        _run_queue(args)
    elif args.command == "industry":
        _run_industry(args)
    elif args.command == "index":
        _run_index(args)
    elif args.command == "stock":
        _run_stock(args)
    elif args.command == "init-factors":
        init_factors()
    elif args.command == "recompute-valuation-percentiles":
        recompute_valuation_percentiles()
    else:
        raise ValueError(f"未知命令: {args.command}")


if __name__ == "__main__":
    main()
