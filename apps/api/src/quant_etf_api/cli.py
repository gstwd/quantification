"""CLI 命令行工具，提供因子定义初始化、回测和数据维护等功能。"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import sys
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, NoReturn

from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.common.enums import (
    EXECUTION_MODELS,
    RESEARCH_DEFAULT_EXECUTION_MODEL,
)
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.infra.clients.akshare_index import _PE_PB_NAME_MAP, _calc_percentile
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.db.models.core import IndexValuationModel
from quant_etf_api.infra.time import today_cn
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.domain.research.campaign import DEFAULT_MAX_DIAGNOSTICS
from quant_etf_api.domain.research.ledger import ALL_STATUSES as HYPOTHESIS_STATUSES
from quant_etf_api.domain.research.ledger import CLOSED_STATUSES as HYPOTHESIS_VERDICTS
from quant_etf_api.domain.research.ledger import HYPOTHESIS_CATEGORIES
from quant_etf_api.engine.config import FactorAliasConfig
from quant_etf_api.schemas.strategy import StrategyConfigCreate, StrategyConfigUpdate
from quant_etf_api.services.backtest_reconcile_service import BacktestReconcileService
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.dsr_calibration_service import DsrCalibrationService
from quant_etf_api.services.dsr_calibration_service import render_markdown as render_dsr_markdown
from quant_etf_api.services.factor_admin_service import FactorAdminService
from quant_etf_api.services.optimization_service import OptimizationService
from quant_etf_api.services.research_batch_service import ResearchBatchService
from quant_etf_api.services.research_diagnostics_service import ResearchDiagnosticsService
from quant_etf_api.services.research_campaign_service import ResearchCampaignService
from quant_etf_api.services.research_ledger_service import ResearchLedgerService
from quant_etf_api.services.robustness_service import SCAN_PRESETS, RobustnessService
from quant_etf_api.services.strategy_config_service import compute_config_hash
from quant_etf_api.services.strategy_lifecycle_service import StrategyLifecycleService
from quant_etf_api.services.strategy_service import StrategyService

logger = logging.getLogger(__name__)


def init_factors() -> None:
    """将代码中的因子模板元数据同步到数据库。

    同步策略：
    - 代码中有、DB 中没有 → INSERT（新模板）
    - 代码和 DB 都有 → 仅更新代码管控字段（version/required_data/参数模式等）
    - DB 中有、代码中没有 → 设为 is_active=False
    """
    setup_logging()
    db = SessionLocal()
    try:
        registry = build_default_registry()
        svc = FactorAdminService(db, registry)
        result = svc.sync_factor_definitions()
        print(
            f"因子模板同步完成: 新增={result['new']} 更新={result['updated']} 停用={result['deactivated']}"
        )
    except Exception:
        logger.error("因子模板同步失败", exc_info=True)
        print("因子模板同步失败，请查看日志", file=sys.stderr)
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


def _fail(message: str) -> NoReturn:
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
    """解析逗号分隔的成本档位参数。

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
    """汇总 ``--knobs`` 与 ``--knobs-file`` 给出的关键旋钮清单。

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


def _execution_model(args: argparse.Namespace) -> str:
    """读取命令的执行模型参数，缺省取研究缺省口径（t_plus_1_close）。"""
    return getattr(args, "execution_model", None) or RESEARCH_DEFAULT_EXECUTION_MODEL


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
    p.add_argument(
        "--lineage",
        help="归入已有策略所在的研究线（给研究线中任一策略 ID）；N、试验预算与验证期留痕按研究线汇总",
    )
    _add_json_flag(p)

    p = sub.add_parser("update", help="更新策略（文件、状态、名称等）")
    p.add_argument("strategy_id")
    p.add_argument("--file", help="新的 config_json 文件")
    p.add_argument("--status", choices=["active", "draft", "disabled"])
    p.add_argument("--name", dest="display_name")
    p.add_argument("--version")
    p.add_argument("--description")
    p.add_argument("--lineage", help="改归已有策略所在的研究线（研究线根仍有成员时拒绝）")
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
    p.add_argument(
        "--benchmark-mix",
        dest="benchmark_mix",
        help="混合基准，如 000300:0.6,000905:0.2（权重和不足 1 的部分为现金）；给出时覆盖 --benchmark",
    )
    p.add_argument("--no-benchmark", action="store_true")
    p.add_argument(
        "--purpose",
        choices=["research", "validation", "monitor"],
        default="research",
        help="回测用途：research=研究期研究（不得越过研究期末端），"
        "validation=验证期验收，monitor=上线后监控",
    )
    p.add_argument("--purpose-reason", help="用途说明，写入留痕记录")
    p.add_argument(
        "--cost-bps",
        type=float,
        help="净口径指标的往返交易成本（基点，按单边换手折算），默认取系统配置",
    )
    p.add_argument(
        "--execution-model",
        choices=EXECUTION_MODELS,
        default=RESEARCH_DEFAULT_EXECUTION_MODEL,
        help="回测执行模型（默认 t_plus_1_close）：t_plus_1_open=T 日信号 T+1 开盘成交，"
        "t_plus_1_close=T 日信号 T+1 收盘成交；两种口径的逐日收益归属不同，指标不可互比",
    )
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
        help="按调仓日历来源过滤：审计同一配置是否跑在两套日历上",
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

    p = sub.add_parser(
        "execute",
        help="执行一条已落库的回测（本地子进程入口，并行池按此命令派生）",
    )
    p.add_argument("backtest_id")
    p.add_argument(
        "--only-pending",
        action="store_true",
        help="仅当回测仍是 pending 时执行；已是终态则跳过（并行池防重复执行的保护）",
    )
    _add_json_flag(p)

    p = sub.add_parser(
        "batch",
        help="本地多进程并行执行一批已落库回测（只用 CLI + 数据库，无需后端服务）",
    )
    p.add_argument(
        "--ids",
        action="append",
        default=None,
        help="回测 ID，可重复传入或用逗号分隔（与 --pending 二选一）",
    )
    p.add_argument(
        "--pending",
        action="store_true",
        help="自动挑选全部 pending 回测（可配合 --strategy / --created-from 收窄）",
    )
    p.add_argument("--strategy", dest="strategy_id", help="按策略 ID 过滤（配合 --pending）")
    p.add_argument(
        "--created-from",
        type=date.fromisoformat,
        help="按创建日期起点过滤（含，配合 --pending）",
    )
    p.add_argument("--purpose", choices=["research", "validation", "monitor"], help="按用途过滤")
    p.add_argument(
        "--workers",
        type=int,
        default=None,
        help="并发进程数；留空按 CPU 与内存自动推导（上限 8，可用环境变量 "
        "QUANT_ETF_CLI_WORKERS 覆盖）",
    )
    p.add_argument("--retries", type=int, default=0, help="失败任务的重试轮数")
    p.add_argument("--log-dir", help="把每个子进程输出写入该目录下的独立日志文件")
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

    p = sub.add_parser("pool", help="查看有效候选池逐日时间线与剔除区间")
    p.add_argument("backtest_id")
    _add_json_flag(p)

    p = sub.add_parser(
        "verify",
        help="独立对账：按记账规格用原始行情复算逐日收益、换手、基准与汇总指标并比对"
        "（只读，有差异时退出码 1）",
    )
    p.add_argument("backtest_id")
    _add_json_flag(p)

    p = sub.add_parser("orphans", help="审计指向已删除回测的悬挂引用")
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
                help="关键旋钮清单（逗号分隔的配置路径，如 timing.thresholds.offensive）",
            )
            p.add_argument(
                "--knobs-file",
                help="关键旋钮清单文件（JSON 列表，或含 knobs 列表的对象）",
            )
        if name == "pool":
            p.add_argument("--samples", type=int, default=8, help="随机子池抽样次数")
        p.add_argument(
            "--sync",
            dest="sync_mode",
            action="store_true",
            help="同步执行（默认入队，需要服务端 worker 消费；只跑 CLI 时请用 --workers）",
        )
        p.add_argument(
            "--workers",
            type=int,
            default=1,
            help="本地多进程并行执行（只用 CLI + 数据库，不依赖后台队列与 API 服务）："
            "1=串行（默认）；N=并发 N 条回测（上限受 CPU/内存约束，可用环境变量 "
            "QUANT_ETF_CLI_WORKERS 覆盖）；0=按 CPU 与内存自动推导",
        )
        p.add_argument(
            "--priority",
            type=int,
            default=0,
            help="队列优先级（越大越先执行），仅入队模式生效",
        )
        p.add_argument(
            "--execution-model",
            choices=EXECUTION_MODELS,
            default=RESEARCH_DEFAULT_EXECUTION_MODEL,
            help="本批次回测执行模型（默认 t_plus_1_close）；验收的邻域证据须与会话同口径",
        )
        _add_hypothesis_id_flag(p)
        _add_over_budget_flag(p)
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

    p = sub.add_parser(
        "abandon",
        help="作废批次（把长期 running 的批次显式收口，保留全部证据与试验台账）",
    )
    p.add_argument("robustness_id")
    p.add_argument("--reason", help="作废原因，写入批次 error_message")
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
        help="变体文件（JSON 列表，或含 variants 列表的对象；每项给 config 或 patch）；"
        "仅做择时归因（--timing-attribution）时可省略",
    )
    p.add_argument(
        "--timing-attribution",
        action="store_true",
        help="择时归因：为每个带择时的变体追加「关闭择时、总仓位固定为其回测期平均实际仓位」"
        "的对照变体，并输出择时贡献差值（有择时 - 关闭择时）",
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
    p.add_argument(
        "--summary",
        action="store_true",
        help="只输出「变体 × 指标」排名表（避免几十个变体的完整 JSON 刷屏）",
    )
    p.add_argument(
        "--execution-model",
        choices=EXECUTION_MODELS,
        default=RESEARCH_DEFAULT_EXECUTION_MODEL,
        help="探索口径的执行模型（默认 t_plus_1_close）；须与后续正式回测/优化会话同口径，"
        "否则探索结论无法迁移",
    )
    _add_hypothesis_id_flag(p)
    _add_over_budget_flag(p)
    _add_json_flag(p)

    _build_signal_lab_parsers(sub)
    _build_ledger_parsers(sub)
    _build_campaign_parsers(sub)
    _build_factor_diagnostic_parsers(sub)
    _build_dsr_calibration_parser(sub)


def _add_research_window(parser: argparse.ArgumentParser) -> None:
    """给研究诊断子命令添加 --start/--end（缺省研究期全段，越过研究期末端拒绝）。"""
    parser.add_argument(
        "--start", type=date.fromisoformat, help="起始日期（缺省研究期起点 2016-01-01）"
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        help="截止日期（缺省研究期末端 2025-12-31；越过研究期末端直接拒绝）",
    )


def _add_summary_flag(parser: argparse.ArgumentParser) -> None:
    """给研究诊断子命令添加 --summary（去掉逐日序列，只保留指标与表格）。"""
    parser.add_argument(
        "--summary", action="store_true", help="精简输出：去掉逐日 / 逐期序列，只保留统计指标"
    )


def _add_factor_target(parser: argparse.ArgumentParser) -> None:
    """给因子诊断子命令添加诊断对象参数：模板 + 参数，或策略中的因子引用。"""
    parser.add_argument(
        "--factor", dest="factor_id", help="因子模板 ID（与 --strategy/--ref 二选一）"
    )
    parser.add_argument("--params", help="模板参数 JSON 对象，如 '{\"period\": 20}'")
    parser.add_argument(
        "--strategy", dest="strategy_id", help="按该策略的 factor_aliases 解析 --ref"
    )
    parser.add_argument("--ref", help="策略中的因子引用名（别名或模板 ID）")
    parser.add_argument(
        "--aliases",
        help="派生因子（--factor expr）依赖的额外别名声明 JSON，如 "
        "'{\"dev60\": {\"template_id\": \"ma_deviation\", \"params\": {\"period\": 60}}}'；"
        "与 --strategy 的 factor_aliases 叠加",
    )


def _build_signal_lab_parsers(sub: argparse._SubParsersAction) -> None:
    """注册 research signal-lab 子命令组（研究台诊断，只读，复用 SignalLabService）。"""
    group = sub.add_parser(
        "signal-lab", help="研究台诊断：研报择时信号 / 市场级信号 / 截面因子（只读，不落库）"
    )
    lab_sub = group.add_subparsers(dest="lab_command", required=True)

    p = lab_sub.add_parser("catalog", help="研究台目录：可用信号 / 截面因子 / 策略择时 / 指数")
    _add_json_flag(p)

    p = lab_sub.add_parser(
        "timing",
        help="单指数择时信号评估（IC fwd20/past20、翻转频率、持仓收益、分年）；"
        "existing_composite + --strategy 时按该策略 timing 块走引擎口径",
    )
    p.add_argument("--index", dest="index_code", required=True, help="评估指数代码")
    p.add_argument("--signal", required=True, help="择时信号键（见 catalog.timing_signals）")
    p.add_argument(
        "--strategy",
        dest="strategy_id",
        help="按该策略 timing 块计算（仅 existing_composite，不接受 --params）",
    )
    p.add_argument("--params", help="信号参数 JSON 对象")
    _add_research_window(p)
    _add_summary_flag(p)
    _add_json_flag(p)

    p = lab_sub.add_parser("market", help="市场级信号（GSISI）在代理指数上的评估")
    p.add_argument("--signal", default="gsisi", help="市场级信号键（默认 gsisi）")
    p.add_argument("--proxy", dest="proxy_code", default="000300", help="代理指数（默认 000300）")
    p.add_argument("--params", help="信号参数 JSON 对象")
    _add_research_window(p)
    _add_summary_flag(p)
    _add_json_flag(p)

    p = lab_sub.add_parser(
        "cross-section", help="研报截面因子的周频 Rank IC、三分组收益与现有评分因子相关性"
    )
    p.add_argument("--factor", dest="factor_key", required=True, help="截面因子键（见 catalog）")
    p.add_argument("--horizon", type=int, default=5, help="未来收益窗口（交易日，1~60，默认 5）")
    p.add_argument("--index-codes", dest="index_codes", help="逗号分隔指数代码，默认全部活跃指数")
    p.add_argument("--params", help="因子参数 JSON 对象")
    _add_research_window(p)
    _add_summary_flag(p)
    _add_json_flag(p)


def _build_factor_diagnostic_parsers(sub: argparse._SubParsersAction) -> None:
    """注册 research factor-ic / buckets（因子诊断，只读）。"""
    p = sub.add_parser(
        "factor-ic",
        help="因子截面 Rank IC（与因子中心同一实现）：汇总、分年与逐日序列",
    )
    _add_factor_target(p)
    p.add_argument("--forward-days", type=int, default=1, help="前瞻交易日数（1~20，默认 1）")
    p.add_argument(
        "--min-n", type=int, default=None, help="有效 IC 的最小横截面指数数量（默认 20）"
    )
    _add_research_window(p)
    _add_summary_flag(p)
    _add_json_flag(p)

    p = sub.add_parser(
        "buckets",
        help="按因子值分桶统计未来 N 日收益（均值/胜率/t 值/样本数，可选前后两段一致性）",
    )
    _add_factor_target(p)
    p.add_argument(
        "--index",
        dest="index_code",
        help="计算因子值的指数；--strategy 且 --ref 为择时因子时缺省取其代理指数",
    )
    p.add_argument("--return-index", dest="return_index", help="计算收益的指数（缺省同 --index）")
    p.add_argument("--horizons", default="1,5,20", help="持有交易日数，逗号分隔（默认 1,5,20）")
    p.add_argument(
        "--basis",
        choices=["t_plus_1_open", "t_plus_1_close", "close_to_close"],
        default=RESEARCH_DEFAULT_EXECUTION_MODEL,
        help="收益口径：t_plus_1_close（默认，与研究缺省执行模型一致）/ t_plus_1_open / "
        "close_to_close（与因子 IC 同口径）",
    )
    bucketing = p.add_mutually_exclusive_group(required=True)
    bucketing.add_argument("--edges", help="分桶内部切点，逗号分隔（自动补 ±inf），如 0,0.5,1,1.5")
    bucketing.add_argument("--quantiles", type=int, help="按全样本分位数等分的桶数")
    split = p.add_mutually_exclusive_group()
    split.add_argument("--split-halves", action="store_true", help="按样本对半切分，输出两段一致性")
    split.add_argument(
        "--split-date", type=date.fromisoformat, help="分段日（后段首日），输出两段一致性"
    )
    _add_research_window(p)
    _add_summary_flag(p)
    _add_json_flag(p)


def _build_dsr_calibration_parser(sub: argparse._SubParsersAction) -> None:
    """注册 research dsr-calibrate（验收第 8 项 Deflated Sharpe 门槛校准，只读）。"""
    p = sub.add_parser(
        "dsr-calibrate",
        help="用已评估优化会话与稳健性批次的超额序列做零假设模拟，"
        "输出各门槛的假阳性率与功效（只读，不改门槛）",
    )
    p.add_argument(
        "--strategy", dest="strategy_id", help="只校准该策略所在研究线（缺省全部研究记录）"
    )
    p.add_argument("--sims", type=int, default=1000, help="零假设模拟次数（默认 1000）")
    p.add_argument(
        "--power-sims", type=int, default=500, help="功效曲线每个注入值的模拟次数（默认 500）"
    )
    p.add_argument("--block", type=int, default=20, help="循环块长度（交易日，默认 20）")
    p.add_argument("--seed", type=int, default=20261024, help="随机种子（默认固定值）")
    p.add_argument("--file", help="把 markdown 报告写入该文件（缺省打印）")
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


def _add_hypothesis_id_flag(parser: argparse.ArgumentParser) -> None:
    """为会产生试验的命令添加 --hypothesis-id（把本次检验挂到台账假设下）。"""
    parser.add_argument(
        "--hypothesis-id",
        help="研究台账假设 ID（research hypothesis add 返回），把本次检验挂到该假设下；"
        "假设已关闭时拒绝",
    )


def _add_over_budget_flag(parser: argparse.ArgumentParser) -> None:
    """为会产生试验的命令添加 --over-budget（研究线超出试验预算时显式放行）。"""
    parser.add_argument(
        "--over-budget",
        dest="over_budget",
        metavar="REASON",
        help="研究线试验次数已达预算时仍要执行的理由（写入研究台账，超出的试验照常计入 N）",
    )


def _build_campaign_parsers(sub: argparse._SubParsersAction) -> None:
    """注册研究战役子命令（自动研究循环）：start/run/next/status/list/step-done/review/stop/report。"""
    camp = sub.add_parser("campaign", help="研究战役：headless Claude Code 自动研究循环")
    csub = camp.add_subparsers(dest="campaign_action", required=True)

    p = csub.add_parser("start", help="开启战役（需要已存在的种子基线策略）")
    p.add_argument("--objective", required=True, help="研究目标")
    p.add_argument("--strategy", dest="strategy_id", required=True, help="种子基线策略 ID")
    p.add_argument("--index-codes", help="研究范围指数，逗号分隔")
    p.add_argument("--max-trials", type=int, default=30, help="本战役最多新增试验次数（默认 30）")
    p.add_argument("--max-hours", type=float, default=6.0, help="墙钟时长上限（默认 6 小时）")
    p.add_argument("--max-rounds", type=int, default=5, help="研究员轮数上限（默认 5）")
    p.add_argument("--max-rejects", type=int, default=3, help="连续否决假设数上限（默认 3）")
    p.add_argument(
        "--max-diagnostics",
        type=int,
        default=DEFAULT_MAX_DIAGNOSTICS,
        help=f"诊断命令（factor-ic/buckets/signal-lab）次数上限（默认 {DEFAULT_MAX_DIAGNOSTICS}）",
    )
    p.add_argument("--max-step-usd", type=float, help="单步 agent 花费上限（美元）")
    p.add_argument("--model", help="agent 模型（缺省用 Claude Code 默认模型）")
    p.add_argument("--by", dest="created_by", help="发起者标识")
    _add_json_flag(p)

    p = csub.add_parser("run", help="驱动战役：逐步启动全新的 headless claude 会话，直到停止")
    p.add_argument("campaign_id")
    p.add_argument("--max-steps", type=int, help="本次最多执行的步数（缺省直到战役停止）")
    p.add_argument("--workers", type=int, default=4, help="传给 agent 的并行回测进程数（默认 4）")
    p.add_argument(
        "--step-timeout-hours", type=float, default=3.0, help="单步 agent 超时（默认 3 小时）"
    )
    p.add_argument("--claude-bin", default="claude", help="claude 可执行文件")
    p.add_argument(
        "--recover",
        action="store_true",
        help="上次中断遗留运行中步骤时将其标记为失败后继续（确认没有 agent 仍在运行）",
    )
    _add_json_flag(p)

    p = csub.add_parser(
        "begin-step",
        help="桌面编排模式：取下一张任务卡并登记步骤，输出给新桌面会话的提示（prompt）",
    )
    p.add_argument("campaign_id")
    p.add_argument(
        "--orchestrator-session",
        help="编排会话 ID（缺省取 CLAUDE_CODE_SESSION_ID）；步骤运行期间只有该会话不受战役模式约束",
    )
    _add_json_flag(p)

    p = csub.add_parser(
        "finalize-step", help="桌面编排模式：子会话结束后收口最近一步（未回报 step-done 判失败）"
    )
    p.add_argument("campaign_id")
    p.add_argument("--agent-session", help="执行本步的桌面会话 ID（记录用）")
    p.add_argument("--error", help="编排侧观察到的错误（给出即判本步失败，除非已回报）")
    _add_json_flag(p)

    p = csub.add_parser("next", help="预览下一步任务卡（触顶时会直接停止战役）")
    p.add_argument("campaign_id")
    _add_json_flag(p)

    p = csub.add_parser("status", help="战役详情：进度、步骤、假设、花费")
    p.add_argument("campaign_id")
    _add_json_flag(p)

    p = csub.add_parser("list", help="列出战役")
    p.add_argument("--status", choices=["running", "stopped", "completed"])
    p.add_argument("--limit", type=int, default=50)
    _add_json_flag(p)

    p = csub.add_parser("step-done", help="agent 回报本步完成（研究员给 --outcome）")
    p.add_argument("campaign_id")
    p.add_argument(
        "--outcome",
        choices=["review_requested", "rejected", "blocked", "no_idea"],
        help="研究员回报结果",
    )
    p.add_argument("--summary", required=True, help="本步结论摘要")
    p.add_argument("--hypothesis-id")
    p.add_argument("--optimization-id")
    _add_json_flag(p)

    p = csub.add_parser("review", help="评审员记录评审结论")
    p.add_argument("campaign_id")
    p.add_argument("--optimization-id", required=True)
    p.add_argument(
        "--verdict", required=True, choices=["recommend_accept", "reject", "needs_more"]
    )
    p.add_argument("--reason", required=True, help="评审理由（逐项引用证据）")
    _add_json_flag(p)

    p = csub.add_parser("stop", help="人工停止战役")
    p.add_argument("campaign_id")
    p.add_argument("--reason", required=True)
    _add_json_flag(p)

    p = csub.add_parser("report", help="生成战役报告 Markdown")
    p.add_argument("campaign_id")
    p.add_argument("--file", help="写入文件路径")


def _build_ledger_parsers(sub: argparse._SubParsersAction) -> None:
    """注册研究台账子命令：hypothesis add|list|show|close|reopen、trial add、ledger。"""
    hyp = sub.add_parser("hypothesis", help="研究假设台账（登记 → 检验 → 结论）")
    hsub = hyp.add_subparsers(dest="hypothesis_action", required=True)

    p = hsub.add_parser("add", help="登记假设（必须事先写定判定标准）")
    p.add_argument("--statement", required=True, help="假设陈述")
    p.add_argument("--category", required=True, choices=HYPOTHESIS_CATEGORIES, help="改动类别")
    p.add_argument(
        "--criteria",
        required=True,
        help="事先写定的判定标准（主要指标与通过阈值），检验后不得改写",
    )
    p.add_argument("--strategy", dest="strategy_id", help="关联策略 ID（指数级假设可省略）")
    p.add_argument("--index-codes", help="涉及的指数代码，逗号分隔（如 000852,000905）")
    p.add_argument("--tags", help="标签，逗号分隔（如 pv_resonance,exit）")
    p.add_argument("--rationale", help="提出依据（诊断证据、前序假设结论等）")
    p.add_argument(
        "--execution-models",
        help="必须通过的执行模型，逗号分隔（如 t_plus_1_open,t_plus_1_close）",
    )
    p.add_argument("--parent", dest="parent_id", help="父假设 ID（在已有结论上细化时填写）")
    p.add_argument("--by", dest="created_by", help="登记者标识，如 claude / codex / human")
    _add_json_flag(p)

    p = hsub.add_parser("list", help="按策略 / 指数 / 标签 / 状态列出假设")
    p.add_argument("--strategy", dest="strategy_id")
    p.add_argument("--index", dest="index_code", help="指数代码")
    p.add_argument("--tag")
    p.add_argument("--status", help=f"状态，逗号分隔（{', '.join(HYPOTHESIS_STATUSES)}）")
    p.add_argument("--limit", type=int, default=200)
    _add_json_flag(p)

    p = hsub.add_parser("show", help="查看假设详情（含全部试验与子假设）")
    p.add_argument("hypothesis_id")
    _add_json_flag(p)

    p = hsub.add_parser("close", help="关闭假设并写入结论")
    p.add_argument("hypothesis_id")
    p.add_argument("--verdict", required=True, choices=HYPOTHESIS_VERDICTS)
    p.add_argument("--reason", required=True, help="结论原因（数据支撑、失败点）")
    _add_json_flag(p)

    p = hsub.add_parser("reopen", help="重新打开受 schema 限制而搁置的假设")
    p.add_argument("hypothesis_id")
    p.add_argument("--reason", required=True, help="重开原因（如 schema 已支持）")
    _add_json_flag(p)

    trial = sub.add_parser("trial", help="研究试验台账（人工 / 历史补录）")
    tsub = trial.add_subparsers(dest="trial_action", required=True)
    p = tsub.add_parser(
        "add",
        help="记录平台外的人工 / 原型对比，或补录历史试验次数（计入 Deflated Sharpe 的 N）",
    )
    p.add_argument("--strategy", dest="strategy_id", required=True, help="N 归属的策略 ID")
    p.add_argument("--count", type=int, required=True, help="试验次数（独立变体数）")
    p.add_argument("--note", required=True, help="依据说明（来源报告、变体清单）")
    p.add_argument("--hypothesis-id", help="所属假设 ID")
    p.add_argument("--execution-model", choices=EXECUTION_MODELS)
    p.add_argument("--estimated", action="store_true", help="标记为历史估算值")
    _add_json_flag(p)

    p = sub.add_parser("ledger", help="台账总览：开工前先读（进行中假设、近期结论、试验次数 N）")
    p.add_argument("--strategy", dest="strategy_id", help="策略 ID（同时给出 N 拆分）")
    p.add_argument("--index", dest="index_code", help="指数代码")
    p.add_argument("--tag")
    p.add_argument("--recent", type=int, default=20, help="返回最近关闭的假设条数")
    _add_json_flag(p)


def _build_optimization_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 optimization 命令组（AI 自动优化闭环）。"""
    group = subparsers.add_parser("optimization", help="策略优化会话（AI 自动优化闭环）")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("start", help="开始优化会话（创建草稿候选 + 会话记录）")
    p.add_argument("--strategy", dest="strategy_id", required=True, help="基线策略 ID")
    p.add_argument("--candidate-file", required=True, help="候选配置 JSON 文件")
    p.add_argument(
        "--hypothesis",
        help="本轮优化的假设；给了 --hypothesis-id 时可省略（取台账中的假设陈述）",
    )
    _add_hypothesis_id_flag(p)
    _add_over_budget_flag(p)
    p.add_argument(
        "--start", type=date.fromisoformat, help="评估起始日期（缺省研究期起点 2016-01-01）"
    )
    p.add_argument(
        "--end",
        type=date.fromisoformat,
        help="评估截止日期（缺省研究期末端 2025-12-31；越过研究期末端直接拒绝）",
    )
    p.add_argument("--folds", type=int, default=4, help="验证窗口数量，默认 4")
    p.add_argument("--candidate-id", dest="candidate_strategy_id", help="候选策略 ID")
    p.add_argument("--version", dest="candidate_version", help="候选版本，默认继承基线")
    p.add_argument(
        "--execution-model",
        choices=EXECUTION_MODELS,
        default=RESEARCH_DEFAULT_EXECUTION_MODEL,
        help="本次会话的回测执行模型（默认 t_plus_1_close）；写入会话后 evaluate 复用，"
        "基线与候选两侧必然同口径",
    )
    _add_json_flag(p)

    p = sub.add_parser("evaluate", help="运行全区间与逐折回测并汇总指标")
    p.add_argument("optimization_id")
    p.add_argument("--folds", type=int, help="验证窗口数量，缺省复用会话配置")
    p.add_argument("--async", dest="async_mode", action="store_true", help="入队后台执行")
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="本地多进程并行执行 2+2K 条回测（只用 CLI + 数据库，不依赖后台队列）："
        "1=串行（默认）；N=并发 N 条；0=按 CPU 与内存自动推导",
    )
    p.add_argument("--log-dir", help="并发模式下把每个子进程输出写入该目录下的独立日志文件")
    _add_json_flag(p)

    p = sub.add_parser("report", help="生成优化报告 Markdown 骨架")
    p.add_argument("optimization_id")
    p.add_argument("--file", help="写入报告文件路径")

    p = sub.add_parser("finish", help="结束会话并记录结论")
    p.add_argument("optimization_id")
    p.add_argument("--verdict", required=True, choices=["accept", "reject"])
    p.add_argument("--report-file", help="最终报告 Markdown 文件")
    p.add_argument("--promote", action="store_true", help="accept 时把候选配置写回基线")
    p.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        default=True,
        help="强制验收清单全部通过（默认开启）",
    )
    p.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="跳过验收清单硬校验（不推荐；accept 时必须同时给 --no-strict-reason）",
    )
    p.add_argument(
        "--no-strict-reason",
        help="非严格接受的理由（连同未通过项写入研究台账）",
    )
    _add_json_flag(p)

    p = sub.add_parser("show", help="查看会话详情")
    p.add_argument("optimization_id")
    _add_json_flag(p)

    p = sub.add_parser("list", help="列出优化会话")
    p.add_argument("--strategy", dest="strategy_id")
    p.add_argument("--limit", type=int, default=50)
    _add_json_flag(p)


def _build_industry_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 industry 命令组（申万行业轮动子系统工具）。

    说明：行业目录/日线/成分的同步、检查、补缺口与全量重拉统一走
    `POST /api/data-management/operations`（`DataManagementService` 编排），
    因此原有的 init-universe / backfill-bars / quality / fill / rebuild /
    backfill-membership 子命令已删除，仅保留 DMS 无法表达的操作。
    """
    group = subparsers.add_parser("industry", help="申万行业 RRG/扩散子系统")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("backfill-stock-close", help="回填成分股历史收盘价")
    p.add_argument("--start", default="20130101", help="起始日 YYYYMMDD，默认 20130101")
    p.add_argument("--codes", dest="stock_codes", help="逗号分隔的股票代码，默认全部成分股")
    _add_json_flag(p)


def _run_industry(args: argparse.Namespace) -> None:
    """执行 industry 命令组。"""
    db = SessionLocal()
    try:
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
    """注册 index 命令组（指数成分数据管理）。

    说明：指数当前成分快照的同步走 `POST /api/data-management/operations`
    （dataset=index_membership），故 `members refresh` 子命令已删除；
    这里保留任意区间 PIT 回填与覆盖状态查询。
    """
    group = subparsers.add_parser("index", help="指数成分数据（PIT/当前快照）")
    sub = group.add_subparsers(dest="subcommand", required=True)
    members = sub.add_parser("members", help="指数成分管理")
    msub = members.add_subparsers(dest="member_action", required=True)

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
    """注册 stock 命令组（批量补全与清洗）。

    说明：个股目录/日线/指标的同步、检查、补缺口与全量重拉统一走
    `POST /api/data-management/operations`，故 init-universe / quality /
    rebuild 子命令已删除；这里只保留 DMS 无法表达的批量选择器、区间回填、
    指定交易日抓取与破坏性清洗。
    """
    group = subparsers.add_parser("stock", help="个股数据管理（批量补全与清洗）")
    sub = group.add_subparsers(dest="subcommand", required=True)

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
        if args.subcommand == "fill":
            codes = _split_codes(args.stock_codes)
            start_date = datetime.strptime(args.start, "%Y%m%d").date()
            if args.all:
                codes = None
            datasets = _split_codes(args.datasets)
            result = service.bulk_fill(
                codes=codes,
                start_date=start_date,
                only_missing=args.only_missing,
                rebuild=False,
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
                lineage_id=getattr(args, "lineage", None),
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
            if getattr(args, "lineage", None):
                update["lineage_id"] = args.lineage
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


def _parallel_backtest_runner(workers: int, log_dir: str | None) -> Any:
    """构造"并行执行一批回测"的回调（优化评估用）。

    返回的回调接收回测 ID 列表，用本地多进程池（CLI 子进程 + 数据库）执行完再返回。
    这样优化评估的 2+2K 条回测不再依赖 uvicorn / 独立 queue worker。

    Args:
        workers: 并发进程数；0 表示按 CPU 与内存自动推导。
        log_dir: 子进程日志目录；None 时输出直通当前终端。

    Returns:
        形如 ``f(backtest_ids: list[str]) -> PoolOutcome`` 的回调。
    """
    from quant_etf_api.cli_pool import PoolOutcome, run_parallel

    def _runner(backtest_ids: list[str]) -> PoolOutcome:
        """并行执行给定回测，失败的条目留在库里由 collect 暴露。"""
        return run_parallel(
            (
                (bid, ["backtest", "execute", bid, "--only-pending"])
                for bid in backtest_ids
            ),
            workers=workers,
            log_dir=log_dir,
        )

    return _runner


def _run_backtest_batch(args: argparse.Namespace) -> None:
    """并行执行一批已落库回测（``backtest batch``）。

    这是"只用 CLI + 数据库"的批量执行入口：先按 ``--ids`` 或 ``--pending`` 选出
    回测，再交给本地多进程池（``cli_pool.run_parallel``）执行；失败的条目按
    ``--retries`` 轮次重试。所有进度与结果都落在数据库，父进程中断后重跑
    ``--pending`` 即可续跑，不会重复执行已完成的回测（子进程带 ``--only-pending``）。

    Args:
        args: 已解析的命令行参数。
    """
    from quant_etf_api.cli_pool import resolve_workers, run_parallel

    db = SessionLocal()
    try:
        svc = BacktestService(db)
        ids: list[str] = []
        if args.ids:
            for chunk in args.ids:
                ids.extend(part.strip() for part in chunk.split(",") if part.strip())
        if args.pending:
            items, _ = svc.list_backtests(
                offset=0,
                limit=200,
                strategy_id=getattr(args, "strategy_id", None),
                created_from=getattr(args, "created_from", None),
                status="pending",
                purpose=getattr(args, "purpose", None),
                order_by="created_at",
                descending=False,
                include_net=False,
            )
            ids.extend(item.backtest_id for item in items)
        # 保序去重
        ids = list(dict.fromkeys(ids))
        if not ids:
            _fail("没有可执行的回测：请用 --ids 指定，或确认 --pending 过滤条件下确实有 pending 回测")
        workers = resolve_workers(args.workers if args.workers > 0 else None, len(ids))
        pending = ids
        outcome = None
        for attempt in range(max(0, args.retries) + 1):
            outcome = run_parallel(
                ((bid, ["backtest", "execute", bid, "--only-pending"]) for bid in pending),
                workers=workers,
                log_dir=args.log_dir,
            )
            failed_ids = [bid for bid, _ in outcome.failed]
            if not failed_ids:
                break
            print(
                f"# 第 {attempt + 1} 轮：{len(failed_ids)} 条回测失败，"
                f"剩余重试轮数 {max(0, args.retries) - attempt}",
                file=sys.stderr,
            )
            pending = failed_ids
        assert outcome is not None  # 循环至少执行一次
        result = {
            "requested": len(ids),
            "succeeded": outcome.succeeded,
            "failed": [{"backtest_id": bid, "exit_code": code} for bid, code in outcome.failed],
            "workers": workers,
        }
        _emit(result, not args.no_json)
        if outcome.failed:
            sys.exit(1)
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
                benchmark_mix=_parse_benchmark_mix(getattr(args, "benchmark_mix", None)),
                purpose=purpose,
                purpose_reason=getattr(args, "purpose_reason", None),
                cost_bps=getattr(args, "cost_bps", None),
                execution_model=_execution_model(args),
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
        elif args.subcommand == "execute":
            detail = svc.get_backtest(args.backtest_id)
            if detail is None:
                _fail(f"回测 {args.backtest_id} 不存在")
            if args.only_pending and detail.status != "pending":
                # 并行池的重复执行保护：已是终态就跳过（幂等重跑）
                _emit(
                    {
                        "backtest_id": args.backtest_id,
                        "status": detail.status,
                        "executed": False,
                        "reason": "回测不是 pending，按 --only-pending 跳过",
                    },
                    not args.no_json,
                )
                return
            svc.run_backtest(args.backtest_id, require_pending=args.only_pending)
            detail = svc.get_backtest(args.backtest_id)
            if detail is None:
                _fail("回测执行后详情不可用")
            _emit(
                {
                    "backtest_id": args.backtest_id,
                    "status": detail.status,
                    "executed": True,
                    "metrics": detail.metrics.model_dump() if detail.metrics else None,
                },
                not args.no_json,
            )
            if detail.status != "success":
                sys.exit(1)
        elif args.subcommand == "batch":
            _run_backtest_batch(args)
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
        elif args.subcommand == "verify":
            try:
                verified = BacktestReconcileService(db).verify(args.backtest_id)
            except LookupError as exc:
                _fail(str(exc))
            _emit(verified, not args.no_json)
            if not verified["ok"]:
                sys.exit(1)
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


def _parse_json_object(raw: str | None, flag: str = "--params") -> dict[str, Any]:
    """解析命令行给出的 JSON 对象参数。

    Args:
        raw: JSON 文本；None 或空串返回空字典。
        flag: 参数名（用于错误提示）。

    Returns:
        参数字典。

    Raises:
        ValueError: 不是合法 JSON 对象时抛出。
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{flag} 必须是合法 JSON 对象：{exc}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{flag} 必须是 JSON 对象")
    return data


def _parse_number_list(raw: str, flag: str, cast: type[float] | type[int] = float) -> list[Any]:
    """解析逗号分隔的数字列表。

    Raises:
        ValueError: 含非法数字或为空时抛出。
    """
    values: list[Any] = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            values.append(cast(text))
        except ValueError:
            raise ValueError(f"{flag} 含非法数字：{text}") from None
    if not values:
        raise ValueError(f"{flag} 不能为空")
    return values


# 研究台 --summary 去掉的逐日序列字段
_TIMING_SERIES_KEYS = ("dates", "close", "value", "on", "regime", "strategy_nav", "benchmark_nav")
_CROSS_SECTION_SERIES_KEYS = ("dates", "ic", "cum_ic", "n_assets")


def _summarize_signal_lab(data: dict[str, Any], kind: str) -> dict[str, Any]:
    """研究台结果的精简视图：去掉逐日序列与长文本定义，保留指标、分年、覆盖与提示。

    Args:
        data: 研究台响应（JSON dict）。
        kind: ``timing``（含 market）或 ``cross_section``。

    Returns:
        精简后的新字典（不修改入参）。
    """
    drop = _TIMING_SERIES_KEYS if kind == "timing" else _CROSS_SECTION_SERIES_KEYS
    out = {k: v for k, v in data.items() if k not in drop}
    for key in ("signal", "factor"):
        if isinstance(out.get(key), dict):
            definition = out[key]
            out[key] = {k: definition.get(k) for k in ("key", "label", "source", "formula")}
    if kind == "timing":
        out["components"] = [c.get("key") for c in data.get("components", [])]
    else:
        # 截面覆盖只保留被剔除的指数，最新截面只保留前后各 3 名
        out["coverage"] = [c for c in data.get("coverage", []) if not c.get("used", True)]
        latest = data.get("latest", [])
        out["latest"] = latest[:3] + latest[-3:] if len(latest) > 6 else latest
    return out


def _summarize_buckets(data: dict[str, Any]) -> dict[str, Any]:
    """分桶诊断的精简视图：每桶只保留核心字段，两段只保留均值 / t 值 / 样本数。"""
    keep = ("label", "n", "share", "mean_pct", "excess_pct", "hit_rate", "t_stat", "low_sample")
    seg_keep = ("label", "n", "mean_pct", "t_stat")
    tables: list[dict[str, Any]] = []
    for table in data.get("tables", []):
        full = table["full"]
        item: dict[str, Any] = {
            "horizon": table["horizon"],
            "n": full["n"],
            "mean_pct": full["mean_pct"],
            "spread_pct": full["spread_pct"],
            "monotonicity": full["monotonicity"],
            "buckets": [{k: b.get(k) for k in keep} for b in full["buckets"]],
        }
        if "segments" in table:
            item["segments"] = [
                {
                    "label": seg["label"],
                    "start": seg["start"],
                    "end": seg["end"],
                    "spread_pct": seg["spread_pct"],
                    "buckets": [{k: b.get(k) for k in seg_keep} for b in seg["buckets"]],
                }
                for seg in table["segments"]
            ]
            item["consistency"] = table["consistency"]
        tables.append(item)
    out = {k: v for k, v in data.items() if k not in ("tables", "notes")}
    out["tables"] = tables
    return out


def _run_signal_lab(svc: Any, args: argparse.Namespace) -> None:
    """执行 research signal-lab 子命令（研究台诊断，只读）。

    Args:
        svc: ResearchDiagnosticsService 实例。
        args: argparse 解析结果。
    """
    as_json = not args.no_json
    command = args.lab_command
    if command == "catalog":
        _emit(svc.signal_lab_catalog(), as_json)
        return
    params = _parse_json_object(args.params)
    if command == "timing":
        data = svc.signal_lab_timing(
            args.index_code, args.signal, args.start, args.end, params, args.strategy_id
        )
        kind = "timing"
    elif command == "market":
        data = svc.signal_lab_market(args.signal, args.proxy_code, args.start, args.end, params)
        kind = "timing"
    elif command == "cross-section":
        data = svc.signal_lab_cross_section(
            args.factor_key,
            args.start,
            args.end,
            args.horizon,
            _split_codes(args.index_codes),
            params,
        )
        kind = "cross_section"
    else:
        raise ValueError(f"未知的 research signal-lab 子命令: {command}")
    _emit(_summarize_signal_lab(data, kind) if args.summary else data, as_json)


def _run_factor_diagnostic(
    svc: Any, args: argparse.Namespace, ledger: ResearchLedgerService | None = None
) -> None:
    """执行 research factor-ic / buckets（因子诊断，只读）。

    诊断对象是派生因子时，诊断完成后把指纹记入研究台账（不计入 N），满足派生因子
    进入研究批量评估与优化候选的前置门槛。

    Args:
        svc: ResearchDiagnosticsService 实例。
        args: argparse 解析结果。
        ledger: 研究台账服务（记录派生因子诊断）。
    """
    instance, proxy = svc.resolve_factor(
        args.factor_id,
        _parse_json_object(args.params),
        args.strategy_id,
        args.ref,
        extra_aliases=_parse_json_object(getattr(args, "aliases", None), "--aliases"),
    )
    if args.subcommand == "factor-ic":
        kwargs: dict[str, Any] = {"forward_days": args.forward_days}
        if args.min_n is not None:
            kwargs["min_n"] = args.min_n
        data = svc.factor_ic(instance, args.start, args.end, **kwargs)
        _record_derived_diagnostic(ledger, instance, args, "factor-ic", data)
        if args.summary:
            data = {k: v for k, v in data.items() if k != "series"}
        _emit(data, not args.no_json)
        return
    factor_index = args.index_code or proxy
    if not factor_index:
        raise ValueError("必须给出 --index（仅当 --ref 是策略择时因子时可缺省为其代理指数）")
    data = svc.bucket_returns(
        instance,
        factor_index,
        args.start,
        args.end,
        horizons=_parse_number_list(args.horizons, "--horizons", int),
        basis=args.basis,
        return_index=args.return_index,
        cut_points=_parse_number_list(args.edges, "--edges") if args.edges else None,
        quantiles=args.quantiles,
        split_at=args.split_date,
        split_halves=args.split_halves,
    )
    _record_derived_diagnostic(ledger, instance, args, "buckets", data)
    _emit(_summarize_buckets(data) if args.summary else data, not args.no_json)


def _alias_view(config_json: dict[str, Any]) -> Any:
    """只解析配置里的 factor_aliases（派生因子门槛只需要别名声明）。

    完整配置的合法性由后续评估 / 会话创建负责校验并给出完整错误，这里解析失败的
    别名按"无派生因子"处理，避免门槛先于正式校验抛出难懂的错误。

    Args:
        config_json: 策略 config_json。

    Returns:
        带 ``factor_aliases`` 属性的对象。
    """
    aliases: dict[str, FactorAliasConfig] = {}
    for name, declaration in (config_json.get("factor_aliases") or {}).items():
        try:
            aliases[name] = FactorAliasConfig(**declaration)
        except (TypeError, ValueError):
            continue
    return SimpleNamespace(factor_aliases=aliases)


def _record_derived_diagnostic(
    ledger: ResearchLedgerService | None,
    instance: Any,
    args: argparse.Namespace,
    diagnostic: str,
    data: dict[str, Any],
) -> None:
    """诊断对象是派生因子时，把诊断记入研究台账并在输出里带上指纹。

    Args:
        ledger: 研究台账服务；None 时不记录。
        instance: 诊断的因子实例。
        args: 命令行参数。
        diagnostic: 诊断命令名。
        data: 诊断结果（原地追加 derived 块）。
    """
    from quant_etf_api.factors.builtins.derived import ExpressionComputer  # noqa: PLC0415

    computer = getattr(instance, "computer", None)
    if ledger is None or not isinstance(computer, ExpressionComputer):
        return
    fingerprint = computer.fingerprint()
    ledger.record_derived_diagnostic(
        fingerprint=fingerprint,
        expression=computer.parsed.text,
        diagnostic=diagnostic,
        strategy_id=getattr(args, "strategy_id", None),
    )
    data["derived"] = {
        "expression": computer.parsed.text,
        "fingerprint": fingerprint,
        "dependencies": list(computer.dependencies),
        "ledger": "已记录诊断，该派生因子可以进入研究批量评估与优化候选",
    }


def _parse_benchmark_mix(text: str | None) -> list[dict[str, Any]] | None:
    """解析 --benchmark-mix（代码:权重,代码:权重）。

    Args:
        text: 参数原文。

    Returns:
        [{"index_code", "weight"}]；未给出时为 None。
    """
    if not text:
        return None
    items: list[dict[str, Any]] = []
    for part in text.split(","):
        code, _, weight = part.strip().partition(":")
        if not code or not weight:
            _fail(f"--benchmark-mix 格式应为 代码:权重，收到 {part!r}")
        items.append({"index_code": code.strip(), "weight": float(weight)})
    return items


def _run_dsr_calibration(svc: DsrCalibrationService, args: argparse.Namespace) -> None:
    """执行 research dsr-calibrate：输出 JSON，或写入 / 打印 markdown 报告。

    Args:
        svc: DsrCalibrationService 实例。
        args: argparse 解析结果。
    """
    report = svc.calibrate(
        args.strategy_id,
        n_sims=args.sims,
        power_sims=args.power_sims,
        block=args.block,
        seed=args.seed,
    )
    if args.file:
        with open(args.file, "w", encoding="utf-8") as f:
            f.write(render_dsr_markdown(report))
        print(f"# 报告已写入: {args.file}")
        return
    if args.no_json:
        print(render_dsr_markdown(report))
        return
    _emit(report, True)


def _run_research(args: argparse.Namespace) -> None:
    """执行 research 命令组（研究批量评估 D-1 + 只读研究诊断）。"""
    db = SessionLocal()
    try:
        if args.subcommand in ("signal-lab", "factor-ic", "buckets"):
            svc = ResearchDiagnosticsService(db)
            if args.subcommand == "signal-lab":
                _run_signal_lab(svc, args)
            else:
                _run_factor_diagnostic(svc, args, ResearchLedgerService(db))
            return
        if args.subcommand in ("hypothesis", "trial", "ledger"):
            _run_ledger(ResearchLedgerService(db), args)
            return
        if args.subcommand == "campaign":
            _run_campaign(ResearchCampaignService(db), args)
            return
        if args.subcommand == "dsr-calibrate":
            _run_dsr_calibration(DsrCalibrationService(db), args)
            return
        if args.subcommand != "batch":
            raise ValueError(f"未知的 research 子命令: {args.subcommand}")
        ledger = ResearchLedgerService(db)
        hypothesis_id = getattr(args, "hypothesis_id", None)
        if hypothesis_id:
            # 先校验假设可挂试验，避免跑完十几分钟才发现假设已关闭
            ledger.require_active_hypothesis(hypothesis_id)
        if not args.variants and not args.timing_attribution:
            raise ValueError("必须提供 --variants，或使用 --timing-attribution 只做择时归因")
        svc = ResearchBatchService(db)
        variants = (
            ResearchBatchService.parse_variants(
                svc.baseline_config(args.strategy_id), _read_json_any(args.variants)
            )
            if args.variants
            else []
        )
        for variant in variants:
            # 派生因子前置门槛：未做过诊断的组合不能直接拿来批量试
            ledger.ensure_derived_diagnosed(_alias_view(variant.config), f"变体 {variant.label}")
        budget = ledger.ensure_budget(
            args.strategy_id, len(variants), getattr(args, "over_budget", None)
        )
        result = svc.run(
            args.strategy_id,
            variants,
            windows=args.windows,
            cost_bps=args.cost_bps,
            cost_ladder=_parse_cost_ladder(args.cost_ladder),
            include_baseline=not args.no_baseline,
            execution_model=args.execution_model,
            timing_attribution=args.timing_attribution,
        )
        ledger_info = _record_research_batch(ledger, args, variants, result)
        if args.summary:
            # 摘要视图是"给人/agent 看的表"，不走 JSON 序列化
            print(result.to_summary_table())
            print(
                f"[ledger] trial={ledger_info.get('trial_id')} "
                f"counted={ledger_info.get('trial_count')} "
                f"hypothesis={ledger_info.get('hypothesis_id')} "
                f"error={ledger_info.get('error')}"
            )
            print(
                f"[budget] lineage={budget['lineage_id']} "
                f"used={budget['used'] + int(ledger_info.get('trial_count') or 0)}"
                f"/{budget['budget']}"
            )
        else:
            payload = result.to_dict()
            payload["ledger"] = ledger_info
            payload["budget"] = budget
            _emit(payload, not args.no_json)
    except (ValueError, LookupError) as exc:
        _fail(str(exc))
    finally:
        db.close()


def _record_research_batch(
    ledger: ResearchLedgerService,
    args: argparse.Namespace,
    variants: list[Any],
    result: Any,
) -> dict[str, Any]:
    """把研究批量评估记入台账（不论是否挂假设都计入 N）。

    台账写入失败不吞掉评估结果，但会在输出里显式标出 error，提醒 N 可能被低估。

    Args:
        ledger: 台账服务。
        args: 命令行参数。
        variants: 用户提交的变体（用于计算配置哈希）。
        result: ``ResearchBatchResult``。

    Returns:
        {trial_id, trial_count, hypothesis_id} 或 {error}。
    """
    try:
        trial = ledger.record_research_batch(
            strategy_id=args.strategy_id,
            variants=result.variants,
            windows=result.windows,
            execution_model=args.execution_model,
            config_hashes=[compute_config_hash(item.config) for item in variants],
            hypothesis_id=getattr(args, "hypothesis_id", None),
        )
    except Exception as exc:  # noqa: BLE001 - 台账失败不能让已跑完的评估结果丢失
        logging.getLogger(__name__).warning("研究批量评估写入台账失败", exc_info=True)
        return {"error": f"写入研究台账失败，N 可能被低估，请用 research trial add 补录: {exc}"}
    return {
        "trial_id": trial["trial_id"],
        "trial_count": trial["trial_count"],
        "hypothesis_id": trial["hypothesis_id"],
    }


def _split_list(raw: str | None) -> list[str] | None:
    """把逗号分隔的参数拆成去空白的列表，空值返回 None。"""
    if not raw:
        return None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or None


def _campaign_id() -> str | None:
    """战役模式标记：驱动器给 agent 进程设置的 QUANT_CAMPAIGN_ID。"""
    return os.environ.get("QUANT_CAMPAIGN_ID") or None


def _campaign_mode(args: argparse.Namespace) -> str | None:
    """本次调用所处的战役（``_guard_campaign_mode`` 判定的结果，缺省回退到环境变量）。"""
    return getattr(args, "campaign_mode_id", None) or _campaign_id()


def _caller_session_id() -> str | None:
    """调用方所在的 Claude Code 会话 ID（桌面编排模式据此区分编排会话与子会话）。"""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def _guard_campaign_mode(args: argparse.Namespace) -> None:
    """判定战役模式并先校验命令（白名单、研究线范围、评审前置），拒绝时直接退出。

    战役模式来自 headless 驱动器的 ``QUANT_CAMPAIGN_ID``，或桌面编排模式下的运行中
    步骤（除编排会话外的调用都受约束）。判定结果写入 ``args.campaign_mode_id``。
    不在任何 Claude Code 会话里的终端可以执行 ``research campaign stop`` 收口卡住的
    桌面战役（停止后战役不再接受任何步骤，不会被利用来放宽约束）。

    Args:
        args: argparse 解析结果。
    """
    db = SessionLocal()
    try:
        svc = ResearchCampaignService(db)
        try:
            campaign_id = svc.resolve_mode(_campaign_id(), _caller_session_id())
        except Exception as exc:  # noqa: BLE001 - 判定失败必须拒绝执行（失败即不放行）
            _fail(f"无法判定是否处于研究战役模式（数据库不可用？）：{exc}")
        args.campaign_mode_id = campaign_id
        if not campaign_id:
            return
        human_stop = (
            not _campaign_id()
            and not _caller_session_id()
            and args.command == "research"
            and getattr(args, "subcommand", None) == "campaign"
            and getattr(args, "campaign_action", None) == "stop"
        )
        if human_stop:
            return
        svc.guard_command(campaign_id, args.command, vars(args))
    except ValueError as exc:
        _fail(f"[战役模式 {args.campaign_mode_id}] {exc}")
    finally:
        db.close()


def _run_campaign(svc: ResearchCampaignService, args: argparse.Namespace) -> None:
    """执行 research campaign 子命令。"""
    as_json = not getattr(args, "no_json", False)
    action = args.campaign_action
    if action == "start":
        result: Any = svc.start(
            objective=args.objective,
            strategy_id=args.strategy_id,
            index_codes=_split_list(args.index_codes),
            max_trials=args.max_trials,
            max_hours=args.max_hours,
            max_rounds=args.max_rounds,
            max_consecutive_rejects=args.max_rejects,
            max_diagnostics=args.max_diagnostics,
            max_step_usd=args.max_step_usd,
            model=args.model,
            created_by=args.created_by,
        )
    elif action == "run":
        from quant_etf_api.services.campaign_driver import (  # noqa: PLC0415
            DriverConfig,
            run_campaign,
        )

        result = run_campaign(
            args.campaign_id,
            DriverConfig(
                max_steps=args.max_steps,
                workers=args.workers,
                step_timeout_hours=args.step_timeout_hours,
                claude_bin=args.claude_bin,
                recover=args.recover,
            ),
        )
    elif action == "begin-step":
        result = svc.begin_desktop_step(
            args.campaign_id, args.orchestrator_session or _caller_session_id() or ""
        )
    elif action == "finalize-step":
        result = svc.finalize_desktop_step(
            args.campaign_id, agent_session_id=args.agent_session, error=args.error
        )
    elif action == "next":
        result = svc.next_task(args.campaign_id)
    elif action == "status":
        result = svc.status(args.campaign_id)
    elif action == "list":
        result = svc.list_campaigns(status=args.status, limit=args.limit)
    elif action == "step-done":
        result = svc.step_done(
            args.campaign_id,
            outcome=args.outcome,
            summary=args.summary,
            hypothesis_id=args.hypothesis_id,
            optimization_id=args.optimization_id,
        )
    elif action == "review":
        result = svc.review(
            args.campaign_id,
            optimization_id=args.optimization_id,
            verdict=args.verdict,
            reason=args.reason,
        )
    elif action == "stop":
        result = svc.stop(args.campaign_id, args.reason)
    elif action == "report":
        markdown = svc.report(args.campaign_id)
        if args.file:
            with open(args.file, "w", encoding="utf-8") as f:
                f.write(markdown)
            print(f"# 报告已写入: {args.file}")
        else:
            print(markdown)
        return
    else:
        raise ValueError(f"未知的 campaign 子命令: {action}")
    _emit(result, as_json)


def _run_ledger(svc: ResearchLedgerService, args: argparse.Namespace) -> None:
    """执行研究台账子命令（hypothesis / trial / ledger）。"""
    as_json = not args.no_json
    if args.subcommand == "ledger":
        _emit(
            svc.ledger(
                strategy_id=args.strategy_id,
                index_code=args.index_code,
                tag=args.tag,
                recent_closed=args.recent,
            ),
            as_json,
        )
        return
    if args.subcommand == "trial":
        _emit(
            svc.record_manual_trial(
                strategy_id=args.strategy_id,
                trial_count=args.count,
                note=args.note,
                hypothesis_id=args.hypothesis_id,
                execution_model=args.execution_model,
                is_estimated=args.estimated,
            ),
            as_json,
        )
        return
    action = args.hypothesis_action
    if action == "add":
        result = svc.add_hypothesis(
            statement=args.statement,
            category=args.category,
            criteria=args.criteria,
            strategy_id=args.strategy_id,
            index_codes=_split_list(args.index_codes),
            tags=_split_list(args.tags),
            rationale=args.rationale,
            execution_models=_split_list(args.execution_models),
            parent_id=args.parent_id,
            created_by=args.created_by or ("campaign" if _campaign_mode(args) else None),
            campaign_id=_campaign_mode(args),
        )
    elif action == "list":
        result = svc.list_hypotheses(
            strategy_id=args.strategy_id,
            index_code=args.index_code,
            tag=args.tag,
            statuses=_split_list(args.status),
            limit=args.limit,
        )
    elif action == "show":
        result = svc.get_hypothesis(args.hypothesis_id)
        if result is None:
            _fail(f"假设 {args.hypothesis_id} 不存在")
    elif action == "close":
        result = svc.close_hypothesis(args.hypothesis_id, args.verdict, args.reason)
    elif action == "reopen":
        result = svc.reopen_hypothesis(args.hypothesis_id, args.reason)
    else:
        raise ValueError(f"未知的 hypothesis 子命令: {action}")
    _emit(result, as_json)


def _run_robustness(args: argparse.Namespace) -> None:
    """执行 robustness 命令组。"""
    db = SessionLocal()
    try:
        svc = RobustnessService(db)
        if args.subcommand in ("scan", "ablate", "pool"):
            ledger = ResearchLedgerService(db)
            hypothesis_id = getattr(args, "hypothesis_id", None)
            if hypothesis_id:
                ledger.require_active_hypothesis(hypothesis_id)
            budget = ledger.ensure_budget(
                args.strategy_id, 1, getattr(args, "over_budget", None)
            )
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
                parallel=getattr(args, "workers", 1),
                execution_model=_execution_model(args),
            )
            if hypothesis_id:
                result["ledger"] = ledger.link_session(
                    kind="robustness",
                    ref_id=result["robustness_id"],
                    strategy_id=args.strategy_id,
                    hypothesis_id=hypothesis_id,
                    trial_count=int(result.get("variants") or 0),
                    execution_model=_execution_model(args),
                )
            result["budget"] = budget
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
        elif args.subcommand == "abandon":
            _emit(svc.abandon(args.robustness_id, reason=args.reason), not args.no_json)
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
            settings = get_settings()
            research_end = date.fromisoformat(settings.research_period_end)
            end = args.end or research_end
            # 起点缺省为研究期起点：若缺省为 end - 730 天，忘了写 --start 时
            # "十年研究期评估"会静默变成"最近两年"，而验收清单照常输出"通过"。
            start = args.start or date.fromisoformat(settings.research_period_start)
            ledger = ResearchLedgerService(db)
            hypothesis_text = args.hypothesis
            hypothesis_id = getattr(args, "hypothesis_id", None)
            if hypothesis_id:
                hypothesis_row = ledger.require_active_hypothesis(hypothesis_id)
                hypothesis_text = hypothesis_text or hypothesis_row.statement
            if not hypothesis_text:
                raise ValueError("必须提供 --hypothesis 或 --hypothesis-id")
            budget = ledger.ensure_budget(
                args.strategy_id, 1, getattr(args, "over_budget", None)
            )
            candidate = _read_json_file(args.candidate_file)
            ledger.ensure_derived_diagnosed(_alias_view(candidate), "候选配置")
            result = svc.start(
                strategy_id=args.strategy_id,
                candidate_config=candidate,
                hypothesis=hypothesis_text,
                start_date=start,
                end_date=end,
                folds=args.folds,
                candidate_strategy_id=args.candidate_strategy_id,
                candidate_version=args.candidate_version,
                execution_model=_execution_model(args),
            )
            if hypothesis_id:
                result["ledger"] = ledger.link_session(
                    kind="optimization",
                    ref_id=result["optimization_id"],
                    strategy_id=args.strategy_id,
                    hypothesis_id=hypothesis_id,
                    trial_count=1,
                    execution_model=_execution_model(args),
                    start_date=start,
                    end_date=end,
                )
            result["budget"] = budget
            _emit(result, not args.no_json)
        elif args.subcommand == "evaluate":
            workers = getattr(args, "workers", 1)
            parallel = workers != 1 and not args.async_mode
            result = svc.evaluate(
                args.optimization_id,
                folds=args.folds,
                async_mode=args.async_mode,
                on_backtests_created=(
                    _parallel_backtest_runner(workers, args.log_dir) if parallel else None
                ),
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
                no_strict_reason=getattr(args, "no_strict_reason", None),
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


def _build_parser() -> argparse.ArgumentParser:
    """构建完整的 CLI 参数解析器（供 main 与单测复用）。"""
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
    return parser


def main() -> None:
    """CLI 入口。"""
    parser = _build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    # 严格口径：交易日历不可用时统一转为可读错误（退出码 1），
    # 不打印 traceback，也不允许任何"按星期近似"的降级结果流出
    from quant_etf_api.domain.common.trading_calendar import (  # noqa: PLC0415
        TradingCalendarUnavailableError,
    )

    try:
        _guard_campaign_mode(args)
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
