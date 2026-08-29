"""CLI 命令行工具，提供因子定义初始化、指数种子数据同步等功能。"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import sys
import time
from datetime import date, timedelta
from typing import Any

from quant_etf_api.config.logging_config import setup_logging
from quant_etf_api.factors.registry import build_default_factor_registry
from quant_etf_api.infra.clients.akshare_index import _PE_PB_NAME_MAP, _calc_percentile
from quant_etf_api.infra.db.base import SessionLocal
from quant_etf_api.infra.db.models.core import IndexValuationModel
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.schemas.backtest import BacktestCreateRequest
from quant_etf_api.schemas.strategy import StrategyConfigCreate, StrategyConfigUpdate
from quant_etf_api.services.backtest_service import BacktestService
from quant_etf_api.services.factor_admin_service import FactorAdminService
from quant_etf_api.services.index_service import IndexService
from quant_etf_api.services.optimization_service import OptimizationService
from quant_etf_api.services.strategy_service import StrategyService

logger = logging.getLogger(__name__)

# legulegu 估值数据源支持的 12 个指数（index_code → 中文名称）
_DEFAULT_INDEXES: list[tuple[str, str]] = [
    ("000300", "沪深300"),
    ("000016", "上证50"),
    ("000905", "中证500"),
    ("000852", "中证1000"),
    ("000906", "中证800"),
    ("000009", "上证380"),
    ("000010", "上证180"),
    ("399330", "深证100"),
    ("399673", "创业板50"),
    ("399324", "深证红利"),
    ("000015", "上证红利"),
    ("000903", "中证100"),
]


def init_factors() -> None:
    """将代码中的因子元数据同步到数据库。

    同步策略：
    - 代码中有、DB 中没有 → INSERT（新因子）
    - 代码和 DB 都有 → 仅更新 version、required_data
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


def init_indexes() -> None:
    """将默认指数种子数据同步到数据库（幂等，已存在的修正名称）。"""
    setup_logging()
    db = SessionLocal()
    try:
        svc = IndexService(db)
        index_repo = BenchmarkIndexRepository(db)
        added = 0
        updated = 0
        for code, name in _DEFAULT_INDEXES:
            try:
                existing = index_repo.find_by_code(code)
                if existing is None:
                    svc.ensure_index_exists(code, name_cn=name)
                    added += 1
                elif existing.name_cn != name:
                    existing.name_cn = name
                    updated += 1
            except Exception:
                logger.warning("指数 %s 同步异常", code, exc_info=True)
        if updated:
            db.commit()
        print(f"指数种子同步完成: 新增={added} 修正={updated}")
    except Exception:
        logger.error("指数种子同步失败", exc_info=True)
        print("指数种子同步失败，请查看日志", file=sys.stderr)
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


def _read_json_file(path: str) -> dict[str, Any]:
    """读取 UTF-8 JSON 配置文件。

    Args:
        path: 文件路径。

    Returns:
        解析后的字典。
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


def _build_backtest_group(subparsers: argparse._SubParsersAction) -> None:
    """注册 backtest 命令组（AI 优化用的回测工具）。"""
    group = subparsers.add_parser("backtest", help="回测工具（AI 优化用）")
    sub = group.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("run", help="创建并执行回测")
    p.add_argument("--strategy", dest="strategy_id", required=True)
    p.add_argument("--start", type=date.fromisoformat, help="起始日期，默认今天往前 2 年")
    p.add_argument("--end", type=date.fromisoformat, help="截止日期，默认今天")
    p.add_argument("--universe", choices=["all", "subset"], default="all")
    p.add_argument("--index-codes", dest="index_codes", help="逗号分隔的指数代码")
    p.add_argument("--benchmark", dest="benchmark_index", default="000300")
    p.add_argument("--no-benchmark", action="store_true")
    p.add_argument("--async", dest="async_mode", action="store_true", help="入队后台执行")
    _add_json_flag(p)

    p = sub.add_parser("status", help="查看回测状态")
    p.add_argument("backtest_id")
    p.add_argument("--wait", action="store_true", help="轮询等待至终态")
    p.add_argument("--timeout", type=float, default=600.0, help="等待超时秒数")
    _add_json_flag(p)

    p = sub.add_parser("show", help="查看回测详情（含配置快照与指标）")
    p.add_argument("backtest_id")
    _add_json_flag(p)

    p = sub.add_parser("results", help="查看回测明细结果")
    p.add_argument("backtest_id")
    p.add_argument("--daily", action="store_true", help="包含每日组合绩效")
    p.add_argument("--index", action="store_true", help="包含每指数信号与收益")
    _add_json_flag(p)


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
            end = args.end or date.today()
            start = args.start or (end - timedelta(days=730))
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
            )
            summary = svc.create_backtest(req)
            if args.async_mode:
                from quant_etf_api.infra.job_queue.queue import get_job_queue

                get_job_queue().enqueue("backtest", {"backtest_id": summary.backtest_id})
                _emit(summary.model_dump(), not args.no_json)
                return
            svc.run_backtest(summary.backtest_id)
            detail = svc.get_backtest(summary.backtest_id)
            if detail is None:
                _fail("回测执行后详情不可用")
            _emit(detail.model_dump(), not args.no_json)
            if detail.status != "success":
                sys.exit(1)
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
            detail = svc.get_backtest(args.backtest_id)
            if detail is None:
                _fail(f"回测 {args.backtest_id} 不存在")
            _emit(detail.model_dump(), not args.no_json)
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


def _run_optimization(args: argparse.Namespace) -> None:
    """执行 optimization 命令组。"""
    db = SessionLocal()
    try:
        svc = OptimizationService(db)
        if args.subcommand == "start":
            end = args.end or date.today()
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


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="量化研究平台 CLI 工具")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    subparsers.add_parser("init-factors", help="将代码中的因子元数据同步到数据库")
    subparsers.add_parser("init-indexes", help="将默认指数种子数据同步到数据库")
    subparsers.add_parser(
        "recompute-valuation-percentiles",
        help="按统一口径重算 index_valuation 历史百分位并回填 source",
    )
    _build_strategy_group(subparsers)
    _build_backtest_group(subparsers)
    _build_optimization_group(subparsers)

    args = parser.parse_args()

    if args.command == "strategy":
        _run_strategy(args)
    elif args.command == "backtest":
        _run_backtest(args)
    elif args.command == "optimization":
        _run_optimization(args)
    elif args.command == "init-factors":
        init_factors()
    elif args.command == "init-indexes":
        init_indexes()
    elif args.command == "recompute-valuation-percentiles":
        recompute_valuation_percentiles()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
