"""回测引擎服务，负责创建、执行和查询回测任务。

重构后使用统一的 _run_backtest_loop 替代 signal/allocation 双模式分支。
集成 FactorProvider、专业绩效指标和基准对比。
回测收益按毛收益口径输出：系统当前阶段不考虑实盘交易与交易成本，
仅研究策略理想效果。净成本口径与稳健性指标在读取路径按单边换手率现算
（见 ``domain.research.stability``），因此存量回测无需重跑即可获得。
"""

from __future__ import annotations

import json
import logging
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from quant_etf_api.config.settings import get_settings
from quant_etf_api.domain.common.numeric import (
    price_invalid as _price_invalid,
)
from quant_etf_api.domain.common.numeric import (
    safe_metric_diff as _safe_metric_diff,
)
from quant_etf_api.domain.common.numeric import (
    sanitize_metric_value as _sanitize_metric_value,
)
from quant_etf_api.domain.common.signal_level import determine_signal_level
from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError
from quant_etf_api.domain.portfolio.accounting import BacktestDayAccumulator
from quant_etf_api.domain.portfolio.benchmark import compute_buy_hold_benchmark
from quant_etf_api.domain.portfolio.returns import (
    compute_allocation_return,
    compute_close_execution_return,
    compute_rebalance_day_return,
    count_missing_allocation_assets,
    count_missing_rebalance_assets,
    get_index_return,
)
from quant_etf_api.domain.portfolio.scaling import compose_two_leg_positions
from quant_etf_api.domain.portfolio.turnover import (
    TURNOVER_MODEL_DELTA_W,
    TURNOVER_MODEL_LEGACY,
    compute_turnover,
)
from quant_etf_api.domain.portfolio.universe import (
    build_universe_items,
    filter_universe_rows,
)
from quant_etf_api.domain.research.metrics import (
    compute_annual_breakdown,
    compute_performance_metrics,
    compute_rolling_metrics,
)
from quant_etf_api.domain.research.periods import (
    PURPOSE_RESEARCH,
    PeriodBoundaries,
    validate_backtest_period,
)
from quant_etf_api.domain.research.stability import (
    DEFAULT_TRADING_DAYS_PER_YEAR,
    compute_cost_ladder,
    compute_stability_metrics,
)
from quant_etf_api.domain.strategies.rebalance import (
    RebalanceLegs,
    select_active_legs,
)
from quant_etf_api.engine.config import RebalanceScheduleConfig, StrategyConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.engine.rebalance import DefaultRebalanceScheduler
from quant_etf_api.factors.registry import max_lookback_days
from quant_etf_api.infra.db.models.core import (
    BacktestComparisonModel,
    BacktestDailyResultModel,
    BacktestIndexResultModel,
    BacktestRunModel,
    RobustnessRunModel,
    StrategyConfigModel,
    StrategyOptimizationModel,
)
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.infra.job_queue.context import JobCancelledError, ensure_not_cancelled
from quant_etf_api.infra.job_queue.queue import BACKTEST_LANE_JOB_TYPES, backtest_job_key
from quant_etf_api.infra.job_queue.repository import JobRepository
from quant_etf_api.infra.time import CHINA_TZ, utcnow_aware
from quant_etf_api.infra.trading_calendar import resolve_trading_calendar
from quant_etf_api.schemas.backtest import (
    AnnualMetrics,
    BacktestCandidatePool,
    BacktestComparisonCreateRequest,
    BacktestComparisonDetail,
    BacktestComparisonSummary,
    BacktestCreateRequest,
    BacktestDailyResult,
    BacktestDeleteResponse,
    BacktestDetail,
    BacktestIndexResult,
    BacktestMetrics,
    BacktestPruneResponse,
    BacktestStability,
    BacktestSummary,
    BacktestWarning,
    ComparisonDailyPoint,
    ComparisonDailyResponse,
    ComparisonMetrics,
    CostLadderEntry,
    DanglingReferenceItem,
    DanglingReferenceReport,
    ValidationUsageItem,
    ValidationUsageResponse,
)
from quant_etf_api.services.backtest_reference_service import (
    HOLDER_OPTIMIZATION_FOLDS,
    HOLDER_ROBUSTNESS_VARIANTS,
    BacktestReferenceService,
)
from quant_etf_api.services.strategy_config_service import (
    StrategyConfigService,
    compute_config_hash,
)

logger = logging.getLogger(__name__)


@dataclass
class BacktestRunCaches:
    """同一批变体共享的行情快照与因子缓存（D-1 研究批量评估）。

    研究批量评估会在同一窗口上跑几十个变体（参数邻域 / 消融 / 池扰动）。
    这些变体的行情、估值与宏观数据完全相同，因子值在没有新增所需因子时
    也完全相同。默认每次 ``_run_backtest_loop`` 都会重新查库并重算，
    批量场景下这部分开销会乘以变体数；把两者按窗口键缓存起来即可把
    "批量探索"从数小时压到分钟级，同时因为复用同一条执行路径，
    指标口径与平台回测严格一致。

    Attributes:
        data: 窗口键 → ``_prepare_backtest_data`` 结果元组。
        factors: (窗口键, 因子集合键) → 逐日预计算因子值。
    """

    data: dict[str, tuple] = field(default_factory=dict)
    factors: dict[tuple[str, str], dict] = field(default_factory=dict)


def _window_cache_key(row: BacktestRunModel) -> str:
    """构造窗口缓存键（区间 + 标的过滤条件）。

    Args:
        row: 回测 ORM 行（或研究批量评估构造的临时行）。

    Returns:
        形如 ``2016-01-01~2018-06-30|{"mode": "subset", ...}`` 的键。
    """
    universe_filter = json.dumps(row.universe_filter or {}, sort_keys=True, ensure_ascii=False)
    return f"{row.start_date.isoformat()}~{row.end_date.isoformat()}|{universe_filter}"


def _execution_price_warning(
    exclusion_dates: dict[str, list[date]],
    exclusion_reasons: dict[str, set[str]],
) -> BacktestWarning | None:
    """构造"缺开盘价被剔除"的警告（F-8）。

    执行模型是 T+1 开盘价，缺开盘价的指数当期无法建仓，会被静默排除在
    候选池之外。旧实现只有一条 info 级的池缩水提示，很容易被读成
    "资产池正常波动"，实际上配置 21 只指数时 2016-2024 只有 10~14 只可交易。

    Args:
        exclusion_dates: 指数 → 被剔除的交易日列表。
        exclusion_reasons: 指数 → 剔除原因集合。

    Returns:
        结构化警告；没有被"缺开盘价"剔除的指数时返回 None。
    """
    missing_open_days = {
        code: len(dates)
        for code, dates in exclusion_dates.items()
        if "MISSING_OPEN" in exclusion_reasons.get(code, set())
    }
    if not missing_open_days:
        return None
    ranked = sorted(missing_open_days.items(), key=lambda item: (-item[1], item[0]))
    detail = "、".join(f"{code} {days} 天" for code, days in ranked[:5])
    return BacktestWarning(
        level="warning",
        code="EXECUTION_PRICE_MISSING",
        message=(
            f"{len(missing_open_days)} 个指数因缺开盘价被排除在候选池之外"
            f"（合计 {sum(missing_open_days.values())} 个交易日）：{detail}"
            f"{' 等' if len(ranked) > 5 else ''}；"
            "执行模型为 T+1 开盘价，缺开盘价当期无法建仓，实际可交易资产域小于配置资产池"
        ),
    )


def _first_bar_by_code(all_bars: dict[tuple[str, date], Any]) -> dict[str, date]:
    """汇总每个指数在回测区间内的首根 K 线日期（F-6 预热期口径用）。

    Args:
        all_bars: 行情数据，键为 (index_code, trade_date)。

    Returns:
        指数代码 → 首根 K 线日期。
    """
    first: dict[str, date] = {}
    for (code, trade_date) in all_bars:
        current = first.get(code)
        if current is None or trade_date < current:
            first[code] = trade_date
    return first


def _parse_candidate_pool(raw: Any) -> BacktestCandidatePool | None:
    """把落库的 candidate_pool JSONB 解析为 schema，非法/缺失时返回 None。

    Args:
        raw: ``backtest_run.candidate_pool`` 的原始值。

    Returns:
        BacktestCandidatePool 实例；无数据或结构不合法时返回 None。
    """
    if not isinstance(raw, dict):
        return None
    try:
        # 嵌套的 segments/exclusions 由 pydantic 自动还原（日期字符串 → date）
        return BacktestCandidatePool(**raw)
    except Exception:
        logger.warning("candidate_pool 解析失败，按缺失处理", exc_info=True)
        return None


class BacktestService:
    """回测引擎服务，负责创建、执行和查询回测任务。"""

    def __init__(
        self,
        db: Session,
        backtest_repo: BacktestRepository | None = None,
        index_bar_repo: IndexDailyBarRepository | None = None,
    ) -> None:
        """初始化回测服务。

        Args:
            db: SQLAlchemy 同步 Session。
            backtest_repo: 回测仓库，未提供时自动创建。
            index_bar_repo: 指数日线仓库，未提供时自动创建。
        """
        self._db = db
        self._engine = StrategyEngine()
        self._backtest_repo = backtest_repo or BacktestRepository(db)
        self._index_bar_repo = index_bar_repo or IndexDailyBarRepository(db)
        # 严格口径（C1）：调仓调度器不再自带"周末降级"日历，
        # 而是在 _run_backtest_loop 里按回测区间解析真实日历后注入；
        # 每日调仓（或未配置 rebalance）不要求日历，此处保持为 None。
        self._rebalance_scheduler: DefaultRebalanceScheduler | None = None

        # 使用进程级单例注册表，避免每次请求重建
        from quant_etf_api.factors.registry import get_default_factor_registry

        self._registry = get_default_factor_registry()
        self._factor_provider = FactorProvider(db=db, registry=self._registry)
        self._context_builder = ContextBuilder(db, factor_provider=self._factor_provider)

    def create_backtest(
        self,
        req: BacktestCreateRequest,
        optimization_id: str | None = None,
        config_snapshot_override: dict[str, Any] | None = None,
    ) -> BacktestSummary:
        """创建回测记录，状态为 pending，立即返回。

        如果策略配置了 index_codes（非空），则强制将回测标的范围限定为这些指数，
        忽略请求中的 universe_mode 和 index_codes。空 index_codes 表示全指数通用策略。
        创建前按用途校验研究期/验证期边界：研究类回测不得越过研究期末端，
        验证与监控类回测允许使用验证期数据（供留痕审计）。

        Args:
            req: 创建回测请求。
            optimization_id: 关联的优化会话 ID（由优化 CLI 写入），普通回测为 None。
            config_snapshot_override: 内部调用可指定不可变策略快照；生命周期监控
                使用它保证回测对象与上线时冻结的配置一致。

        Returns:
            回测摘要。

        Raises:
            ValueError: 策略不存在、配置校验失败，或回测区间不符合用途边界时抛出。
        """
        backtest_id = str(uuid4())
        # created_at 和 started_at/finished_at 一样是 timestamptz，写入必须带时区（B5）
        now = utcnow_aware()

        # 研究期/验证期硬约束：研究类回测越过研究期末端直接拒绝
        validate_backtest_period(_period_boundaries(), req.purpose, req.start_date, req.end_date)

        # 加载策略配置，检查是否有 index_codes 限定
        config_svc = StrategyConfigService(self._db)
        strategy_config = (
            config_svc.parse_snapshot(config_snapshot_override)
            if config_snapshot_override is not None
            else config_svc.get_parsed_config(req.strategy_id)
        )
        if strategy_config is None:
            raise ValueError(f"策略 {req.strategy_id} 配置不存在或解析失败，无法执行回测")

        # P4：回测创建前复用配置校验，未知/停用因子或非法变换函数直接拒绝
        config_validation = config_svc.validate_parsed(strategy_config)
        if not config_validation.valid:
            raise ValueError(
                f"策略 {req.strategy_id} 配置校验失败: {'; '.join(config_validation.errors)}"
            )

        strategy_index_codes = strategy_config.index_codes
        if strategy_index_codes:
            # 策略有指数范围限定，强制使用策略的 index_codes
            universe_filter = {"mode": "subset", "index_codes": strategy_index_codes}
            logger.info(
                "回测 %s：策略 %s 限定指数范围 %s，强制应用",
                backtest_id,
                req.strategy_id,
                strategy_index_codes,
            )
        else:
            universe_filter = (
                {"mode": "all"}
                if req.universe_mode == "all"
                else {"mode": "subset", "index_codes": req.index_codes}
            )

        params = dict(req.params) if req.params else {}
        params["_execution_model"] = req.execution_model
        params["_data_quality_mode"] = req.data_quality_mode
        # 净口径成本的**默认值**随回测固化（读取路径仍可用 cost_bps 覆盖主口径，
        # 多档成本始终并列返回），避免不同时点用不同默认成本折算净收益
        params["_cost_bps"] = (
            req.cost_bps if req.cost_bps is not None else get_settings().default_cost_bps
        )
        # 保存基准配置到 params，供执行时读取；基准统一采用买入持有口径。
        params["_enable_benchmark"] = req.enable_benchmark
        params["_benchmark_index_code"] = req.benchmark_index_code
        # 换手口径指纹（C2）：存量回测无该键，读取时按历史口径（legacy_v1）标注，
        # 避免"含清仓/建仓腿"与"未含"的净口径指标被直接比较
        params["_turnover_model"] = TURNOVER_MODEL_DELTA_W

        # 创建时快照策略配置，保证回测结果与当时配置严格对应
        config_snapshot: dict[str, Any] | None = config_snapshot_override
        config_hash: str | None = None
        try:
            if config_snapshot_override is not None:
                config_hash = compute_config_hash(config_snapshot_override["config_json"])
            else:
                detail = config_svc.get_config(req.strategy_id)
                if detail is not None:
                    config_snapshot = {
                        "strategy_id": detail.strategy_id,
                        "display_name": detail.display_name,
                        "version": detail.version,
                        "frequency": detail.frequency,
                        "config_json": detail.config_json,
                    }
                    config_hash = compute_config_hash(detail.config_json)
        except Exception:
            # 快照属于增强能力，读取失败不应阻塞回测创建；
            # 无快照时 run_backtest 会回退到实时配置
            logger.warning("写入回测配置快照失败，回退为无快照模式", exc_info=True)

        try:
            row = BacktestRunModel(
                backtest_id=backtest_id,
                strategy_id=req.strategy_id,
                start_date=req.start_date,
                end_date=req.end_date,
                universe_filter=universe_filter,
                params=params,
                status="pending",
                config_snapshot=config_snapshot,
                config_hash=config_hash,
                optimization_id=optimization_id,
                purpose=req.purpose,
                purpose_reason=req.purpose_reason,
                created_at=now,
            )
            self._db.add(row)
            self._db.commit()
        except Exception:
            self._db.rollback()
            logger.warning("create_backtest DB insert failed", exc_info=True)

        # 验证期消费留痕（D-5）：只要创建了使用验证期数据的回测，该策略的
        # "验证期样本外"身份即被消费，此后这段数据只能用于否决。留痕挂在策略
        # 元数据上（strategy_config），因为消费发生在研究阶段、可能早于上线。
        if req.purpose in ("validation", "monitor"):
            try:
                StrategyConfigService(self._db).mark_validation_consumed(
                    req.strategy_id,
                    note=f"回测 {backtest_id}（{req.purpose}）：{req.purpose_reason or '未填写用途说明'}",
                )
            except Exception:
                # 留痕失败不能阻塞回测创建，但要留下日志便于事后补救
                self._db.rollback()
                logger.warning(
                    "验证期消费留痕失败: strategy_id=%s backtest_id=%s",
                    req.strategy_id,
                    backtest_id,
                    exc_info=True,
                )

        return BacktestSummary(
            backtest_id=backtest_id,
            strategy_id=req.strategy_id,
            start_date=req.start_date,
            end_date=req.end_date,
            status="pending",
            created_at=now,
            purpose=req.purpose,
        )

    def list_backtests(
        self,
        offset: int = 0,
        limit: int = 50,
        strategy_id: str | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        status: str | None = None,
        purpose: str | None = None,
        order_by: str = "created_at",
        descending: bool = True,
        calendar_source: str | None = None,
        include_net: bool = True,
        cost_bps: float | None = None,
    ) -> tuple[list[BacktestSummary], int]:
        """分页返回回测列表，支持状态/用途/时间范围/日历来源筛选（B4/C1）。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。
            strategy_id: 策略 ID，精确匹配。
            created_from: 创建日期起点（含，中国日期）。
            created_to: 创建日期终点（含，中国日期）。
            status: 回测状态，精确匹配（pending/running/success/failed/cancelled）。
            purpose: 回测用途（research/validation/monitor）。
            order_by: 排序字段：created_at / started_at / finished_at。
            descending: 是否倒序，默认 True。
            calendar_source: 调仓日历来源过滤（upstream/database/not_required），
                用于审计"同一配置是否跑在两种调仓日历上"（C1）。
            include_net: 是否现算净成本口径指标（F-12，默认 True）。
                历史实现只给 `backtest show` 现算净口径，列表里
                `net_sharpe_ratio` 恒为 null，无法在列表层比较"成本吃掉多少超额"。
            cost_bps: 净口径成本（基点），None 时取系统默认值。

        Returns:
            筛选后的回测摘要和总数。
        """
        try:
            start_at = self._china_day_start(created_from) if created_from else None
            end_at = self._china_day_start(created_to + timedelta(days=1)) if created_to else None
            rows, total = self._backtest_repo.find_all(
                offset=offset,
                limit=limit,
                strategy_id=strategy_id,
                created_from=start_at,
                created_to=end_at,
                status=status,
                purpose=purpose,
                order_by=order_by,
                descending=descending,
                calendar_source=calendar_source,
            )
            queue_info = self._queue_info([r.backtest_id for r in rows])
            net_info = self._net_metrics_by_backtest(rows, cost_bps) if include_net else {}
            items = [
                self._row_to_summary(
                    r, queue_info.get(r.backtest_id), net_metrics=net_info.get(r.backtest_id)
                )
                for r in rows
            ]
            return items, total
        except Exception:
            logger.warning("list_backtests DB query failed", exc_info=True)
            return [], 0

    def _net_metrics_by_backtest(
        self, rows: list[BacktestRunModel], cost_bps: float | None
    ) -> dict[str, dict[str, Any]]:
        """批量现算每条回测的净成本口径指标（F-12）。

        与 `backtest show` 使用同一套口径（`domain.research.stability`
        的成本折算），只是把结果并进列表摘要，避免"列表看毛、详情看净"。

        Args:
            rows: 当前页的回测 ORM 行。
            cost_bps: 成本（基点），None 时取系统默认值。

        Returns:
            backtest_id → {net_sharpe_ratio, net_annualized_return_pct,
            annualized_turnover, cost_drag_pct_per_year, cost_bps}；无逐日
            结果或未成功的回测不出现在结果里。
        """
        successful = [row for row in rows if row.status == "success" and row.metrics]
        if not successful:
            return {}
        bps = float(cost_bps if cost_bps is not None else get_settings().default_cost_bps)
        try:
            series_map = self._backtest_repo.find_return_turnover_pairs(
                [row.backtest_id for row in successful]
            )
        except Exception:
            logger.warning("批量现算净口径指标失败", exc_info=True)
            return {}
        result: dict[str, dict[str, Any]] = {}
        for row in successful:
            pairs = series_map.get(row.backtest_id) or []
            returns = [
                float(item[0]) for item in pairs if item[0] is not None
            ]
            if not returns:
                continue
            turnovers = [item[1] for item in pairs]
            benchmarks = [item[2] if len(item) > 2 else None for item in pairs]
            ladder = compute_cost_ladder(returns, turnovers, [bps], benchmark_returns=benchmarks)
            if not ladder:
                continue
            entry = ladder[0]
            normalized_turnovers = [float(t or 0.0) for t in turnovers]
            years = len(returns) / DEFAULT_TRADING_DAYS_PER_YEAR
            result[row.backtest_id] = {
                "cost_bps": bps,
                "net_annualized_return_pct": entry.net_annualized_return_pct,
                "net_sharpe_ratio": entry.net_sharpe_ratio,
                "net_excess_return_pct": entry.net_excess_return_pct,
                "annualized_turnover": (
                    round(sum(normalized_turnovers) / years, 4) if years > 0 else None
                ),
                "cost_drag_pct_per_year": entry.cost_drag_pct_per_year,
            }
        return result

    @staticmethod
    def _china_day_start(value: date) -> datetime:
        """将中国业务日期的零点转换为带时区的 UTC 时间（B5：timestamptz 列）。"""
        return datetime.combine(value, time.min, tzinfo=CHINA_TZ).astimezone(timezone.utc)

    def _queue_info(self, backtest_ids: list[str]) -> dict[str, dict[str, Any]]:
        """批量查询回测任务的排队位置与等待/执行耗时（B3）。

        队列信息属于增强可观测性：任何查询失败都不应影响回测列表本身。

        Args:
            backtest_ids: 回测标识列表。

        Returns:
            backtest_id → {"job_status", "queued_seconds", "elapsed_seconds",
            "queue_position", "job_priority", "batch_id"}。
        """
        if not backtest_ids:
            return {}
        try:
            repo = JobRepository()
            snapshots = repo.find_snapshots_by_keys([backtest_job_key(bid) for bid in backtest_ids])
        except Exception:
            logger.debug("查询回测队列信息失败", exc_info=True)
            return {}
        now = utcnow_aware()
        result: dict[str, dict[str, Any]] = {}
        for backtest_id in backtest_ids:
            snapshot = snapshots.get(backtest_job_key(backtest_id))
            if snapshot is None:
                continue
            info: dict[str, Any] = {
                "job_status": snapshot.status,
                "job_priority": snapshot.priority,
                "batch_id": snapshot.batch_id,
            }
            if snapshot.status == "pending" and snapshot.created_at is not None:
                info["queued_seconds"] = round(
                    max(0.0, (now - snapshot.created_at).total_seconds()), 1
                )
                try:
                    info["queue_position"] = repo.count_pending_ahead(
                        snapshot.priority,
                        snapshot.created_at,
                        BACKTEST_LANE_JOB_TYPES,
                    )
                except Exception:
                    logger.debug("计算队列位置失败: %s", backtest_id, exc_info=True)
            if snapshot.status == "running" and snapshot.started_at is not None:
                info["elapsed_seconds"] = round(
                    max(0.0, (now - snapshot.started_at).total_seconds()), 1
                )
            result[backtest_id] = info
        return result

    def get_backtest(
        self,
        backtest_id: str,
        *,
        cost_bps: float | None = None,
        cost_ladder: list[float] | None = None,
    ) -> BacktestDetail | None:
        """返回回测详情。

        Args:
            backtest_id: 回测标识。
            cost_bps: 可选的成本口径覆盖（基点，C3）；None 时使用回测固化的成本。
                多档成本始终并列返回在 ``stability.cost_ladder`` 中。
            cost_ladder: 可选的多档成本覆盖（基点列表）；None 时使用系统配置的梯子。

        Returns:
            回测详情；不存在或查询失败时返回 None。
        """
        try:
            row = self._backtest_repo.find_by_id(backtest_id)
            if row is None:
                return None
            detail = self._row_to_detail(row, self._queue_info([backtest_id]).get(backtest_id))
            # 分年度绩效表：读取时按自然年切分已持久化的日收益序列计算，
            # 存量回测无需重跑即可获得该表（滚动/分年度口径仅依赖单条序列）
            if detail.status == "success":
                daily_rows = self._backtest_repo.find_daily_results(backtest_id)
                if daily_rows:
                    annual_rows = compute_annual_breakdown(
                        [r.portfolio_return for r in daily_rows],
                        [r.trade_date for r in daily_rows],
                    )
                    # 稳健性指标与净成本口径同样在读取路径现算：与分年度表共用
                    # 已落库的逐日序列，存量回测无需重跑即可获得
                    stability = self._compute_stability(
                        row, daily_rows, cost_bps=cost_bps, cost_ladder=cost_ladder
                    )
                    detail = detail.model_copy(
                        update={
                            "annual_metrics": [
                                AnnualMetrics(
                                    year=r.year,
                                    trading_days=r.trading_days,
                                    total_return_pct=r.total_return_pct,
                                    annualized_return_pct=r.annualized_return_pct,
                                    sharpe_ratio=r.sharpe_ratio,
                                    sortino_ratio=r.sortino_ratio,
                                    max_drawdown_pct=r.max_drawdown_pct,
                                )
                                for r in annual_rows
                            ],
                            "stability": stability,
                            "metrics": _merge_net_metrics(detail.metrics, stability),
                        }
                    )
            return detail
        except Exception:
            logger.warning("get_backtest DB query failed", exc_info=True)
            return None

    def cancel_backtest(self, backtest_id: str) -> dict[str, Any]:
        """请求取消一条回测（B2）。

        未开始（pending）的回测直接落为 cancelled；运行中的回测只打协作取消
        标记，由回测主循环在安全检查点退出。同步执行（不走队列）的回测没有
        队列任务，只能标记"不可取消"。

        Args:
            backtest_id: 回测标识。

        Returns:
            取消结果字典：backtest_id / status / job_status / cancel_requested。

        Raises:
            ValueError: 回测不存在或已处于终态时抛出。
        """
        row = self._backtest_repo.find_by_id(backtest_id)
        if row is None:
            raise ValueError(f"回测 {backtest_id} 不存在")
        if row.status in ("success", "failed", "cancelled"):
            raise ValueError(f"回测 {backtest_id} 已处于终态（{row.status}），无法取消")

        from quant_etf_api.infra.job_queue.queue import get_job_queue

        queue = get_job_queue()
        job_key = backtest_job_key(backtest_id)
        snapshot = queue.find_snapshots_by_keys([job_key]).get(job_key)
        if snapshot is None:
            # 同步 CLI 回测不经过队列：只能提示，无法协作取消
            return {
                "backtest_id": backtest_id,
                "status": row.status,
                "job_status": None,
                "cancel_requested": False,
                "message": "未找到队列任务（同步执行的回测无法取消）",
            }
        job_status = queue.cancel_job(snapshot.job_id, f"用户取消回测 {backtest_id}")
        return {
            "backtest_id": backtest_id,
            "status": row.status,
            "job_id": snapshot.job_id,
            "job_status": job_status,
            "cancel_requested": job_status == "running",
            "message": (
                "已请求取消，回测将在最近的安全检查点退出"
                if job_status == "running"
                else "回测任务已取消"
            ),
        }

    def find_dangling_references(self, limit: int = 200) -> DanglingReferenceReport:
        """审计全部悬挂回测引用（C5）。

        Args:
            limit: 明细条数上限。

        Returns:
            DanglingReferenceReport。
        """
        report = BacktestReferenceService(self._db).find_dangling_references(limit=limit)
        return DanglingReferenceReport(
            total=report["total"],
            items=[DanglingReferenceItem(**item) for item in report["items"]],
        )

    def delete_backtest(self, backtest_id: str, *, force: bool = False) -> BacktestDeleteResponse:
        """删除一条回测记录（C5，受控删除）。

        数据库外键负责结构清理：日结果/对比记录 ``ON DELETE CASCADE``，
        优化会话与生命周期上的回测列 ``ON DELETE SET NULL``。JSONB 引用
        （稳健性变体、优化折窗口）无法加外键，因此：

        - 不存在引用时直接删除；
        - 存在 JSONB 引用且 ``force=False`` 时拒绝（409），列出持有者；
        - ``force=True`` 时删除，并保留持有者信息供 ``backtest orphans`` 审计。

        Args:
            backtest_id: 回测标识。
            force: 是否在存在 JSONB 引用时仍强制删除。

        Returns:
            BacktestDeleteResponse。

        Raises:
            ValueError: 回测不存在、仍处于 pending/running，或存在 JSONB 引用
                而未指定 force 时抛出。
        """
        row = self._backtest_repo.find_by_id(backtest_id)
        if row is None:
            raise ValueError(f"回测 {backtest_id} 不存在")
        if row.status in ("pending", "running"):
            raise ValueError(
                f"回测 {backtest_id} 仍处于 {row.status}，请先取消（backtest cancel）再删除"
            )

        holders = BacktestReferenceService(self._db).find_referencing_holders(backtest_id)
        if holders and not force:
            detail = "；".join(
                f"{item['holder']}:{item['holder_id']}.{item['field']}" for item in holders
            )
            raise ValueError(
                f"回测 {backtest_id} 仍被引用（{detail}），"
                "删除会留下悬挂引用；确认后请带 force=True 重试"
            )
        self._backtest_repo.delete(backtest_id)
        logger.info(
            "回测已删除: backtest_id=%s force=%s holders=%s",
            backtest_id,
            force,
            len(holders),
        )
        return BacktestDeleteResponse(
            backtest_id=backtest_id,
            deleted=True,
            holders=[DanglingReferenceItem(**item, missing_backtest_ids=[]) for item in holders],
            message=(
                "回测已删除；日结果与对比记录已级联清理，优化/生命周期中的引用已置空"
                + ("（JSONB 引用仍指向该回测，可用 backtest orphans 审计）" if holders else "")
            ),
        )

    def prune_dangling_references(self, *, dry_run: bool = True) -> BacktestPruneResponse:
        """清理 JSONB 中的悬挂回测引用（C5）。

        - ``robustness_run.variants[*].backtest_ids``：移除指向已删除回测的窗口，
          并把窗口标签写入同一变体的 ``deleted_windows``（保留"曾经有这些窗口"
          的信息，而不是静默消失）；
        - ``strategy_optimization.fold_backtests[*].{baseline,candidate}_backtest_id``：
          置为 ``null``（回测已删除，折窗口无法再评估）。

        Args:
            dry_run: True 时只统计不写库（默认）。

        Returns:
            BacktestPruneResponse。
        """
        reference_svc = BacktestReferenceService(self._db)
        existing = reference_svc.existing_ids()
        result = BacktestPruneResponse(dry_run=dry_run)

        for row in self._db.query(RobustnessRunModel).all():
            variants = list(row.variants or [])
            changed = False
            for variant in variants:
                mapping = dict(variant.get("backtest_ids") or {})
                missing_labels = [label for label, bid in mapping.items() if bid not in existing]
                if not missing_labels:
                    continue
                changed = True
                result.removed_windows += len(missing_labels)
                result.items.append(
                    DanglingReferenceItem(
                        holder=HOLDER_ROBUSTNESS_VARIANTS,
                        holder_id=row.robustness_id,
                        field=f"variants[{variant.get('label')}].backtest_ids",
                        missing_backtest_ids=[
                            mapping[label] for label in missing_labels if mapping[label]
                        ],
                    )
                )
                if dry_run:
                    continue
                for label in missing_labels:
                    mapping.pop(label, None)
                variant["backtest_ids"] = mapping
                variant["deleted_windows"] = sorted(
                    set(variant.get("deleted_windows") or []) | set(missing_labels)
                )
            if changed:
                result.robustness_batches += 1
                if not dry_run:
                    row.variants = variants
                    # JSONB 就地修改不会被 SQLAlchemy 的变更检测捕获
                    # （赋值前后内容相等 → 不产生 UPDATE），必须显式标记脏
                    flag_modified(row, "variants")

        for opt in self._db.query(StrategyOptimizationModel).all():
            folds = list(opt.fold_backtests or [])
            changed = False
            for fold in folds:
                for key in ("baseline_backtest_id", "candidate_backtest_id"):
                    bid = fold.get(key)
                    if not bid or bid in existing:
                        continue
                    changed = True
                    result.nulled_folds += 1
                    result.items.append(
                        DanglingReferenceItem(
                            holder=HOLDER_OPTIMIZATION_FOLDS,
                            holder_id=opt.optimization_id,
                            field=f"fold_backtests[{fold.get('fold')}].{key}",
                            missing_backtest_ids=[bid],
                        )
                    )
                    if not dry_run:
                        fold[key] = None
            if changed:
                result.optimization_sessions += 1
                if not dry_run:
                    opt.fold_backtests = folds
                    # 同上：JSONB 就地修改需显式标记脏才会落库
                    flag_modified(opt, "fold_backtests")

        if not dry_run:
            self._db.commit()
        return result

    def _compute_stability(
        self,
        row: BacktestRunModel,
        daily_rows: list,
        cost_bps: float | None = None,
        cost_ladder: list[float] | None = None,
    ) -> BacktestStability:
        """从已落库的逐日结果现算稳健性指标与净成本口径。

        成本默认取创建回测时固化的 ``params["_cost_bps"]``，缺失时回退系统默认值；
        调用方可传 ``cost_bps`` 覆盖主口径（C3：想看 20/30/50bp 无需重跑回测），
        并始终并列输出多档成本（``cost_ladder``）。执行模型、数据质量口径、
        基准、调仓日历来源与换手口径一并写入结果，作为指标口径指纹，避免
        不同口径的回测指标被相互比较（历史上 warn/strict 口径分叉、周末降级
        日历、换手未计清仓腿都曾导致同一配置出现两套结果）。

        Args:
            row: 回测 ORM 行。
            daily_rows: 该回测的逐日结果行（按日期升序）。
            cost_bps: 可选的成本覆盖（基点）；None 时使用回测固化的成本。
            cost_ladder: 可选的多档成本覆盖（基点列表）；None 时使用系统配置梯子。

        Returns:
            BacktestStability 实例。
        """
        params = row.params or {}
        effective_cost_bps = params.get("_cost_bps") if cost_bps is None else cost_bps
        if effective_cost_bps is None:
            effective_cost_bps = get_settings().default_cost_bps
        enable_benchmark = params.get("_enable_benchmark", True)
        daily_returns = [r.portfolio_return for r in daily_rows]
        trade_dates = [r.trade_date for r in daily_rows]
        turnovers = [getattr(r, "turnover", None) for r in daily_rows]
        benchmark_returns = [getattr(r, "benchmark_return", None) for r in daily_rows]
        stability = compute_stability_metrics(
            daily_returns,
            trade_dates,
            benchmark_returns=benchmark_returns,
            turnovers=turnovers,
            exposures=[getattr(r, "total_exposure", None) for r in daily_rows],
            positions=[getattr(r, "positions", None) for r in daily_rows],
            cost_bps=float(effective_cost_bps),
        )
        # 多档成本并列（C3）：只重算与成本有关的量，读取路径零可感额外成本
        ladder_config = (
            cost_ladder
            if cost_ladder is not None
            else [float(item) for item in get_settings().stability_cost_ladder]
        )
        ladder = [
            CostLadderEntry(
                cost_bps=entry.cost_bps,
                net_cumulative_return_pct=entry.net_cumulative_return_pct,
                net_annualized_return_pct=entry.net_annualized_return_pct,
                net_sharpe_ratio=entry.net_sharpe_ratio,
                net_excess_return_pct=entry.net_excess_return_pct,
                cost_drag_pct_per_year=entry.cost_drag_pct_per_year,
            )
            for entry in compute_cost_ladder(
                daily_returns,
                turnovers,
                [float(item) for item in ladder_config],
                benchmark_returns=benchmark_returns,
            )
        ]
        return BacktestStability(
            cost_bps=stability.cost_bps,
            execution_model=params.get("_execution_model"),
            data_quality_mode=params.get("_data_quality_mode"),
            benchmark_index_code=(
                params.get("_benchmark_index_code") if enable_benchmark else None
            ),
            calendar_source=params.get("_calendar_source"),
            turnover_model=params.get("_turnover_model") or TURNOVER_MODEL_LEGACY,
            cost_ladder=ladder,
            candidate_pool=_parse_candidate_pool(getattr(row, "candidate_pool", None)),
            warmup_trading_days=int(params.get("_warmup_trading_days") or 0),
            annualized_turnover=stability.annualized_turnover,
            cost_drag_pct_per_year=stability.cost_drag_pct_per_year,
            net_cumulative_return_pct=stability.net_cumulative_return_pct,
            net_annualized_return_pct=stability.net_annualized_return_pct,
            net_sharpe_ratio=stability.net_sharpe_ratio,
            net_excess_return_pct=stability.net_excess_return_pct,
            year_return_share_max=stability.year_return_share_max,
            year_return_share_hhi=stability.year_return_share_hhi,
            best_year=stability.best_year,
            ex_best_year_annualized_return_pct=(stability.ex_best_year_annualized_return_pct),
            ex_best_year_sharpe_ratio=stability.ex_best_year_sharpe_ratio,
            annual_sharpe_positive_ratio=stability.annual_sharpe_positive_ratio,
            segment_sharpe_positive_ratio=stability.segment_sharpe_positive_ratio,
            best_segment_sharpe=stability.best_segment_sharpe,
            worst_segment_sharpe=stability.worst_segment_sharpe,
            max_drawdown_pct=stability.max_drawdown_pct,
            current_drawdown_pct=stability.current_drawdown_pct,
            current_drawdown_percentile_pct=stability.current_drawdown_percentile_pct,
            max_drawdown_days=stability.max_drawdown_days,
            average_exposure=stability.average_exposure,
            position_concentration=stability.position_concentration,
        )

    def list_validation_usage(self, limit: int = 200) -> ValidationUsageResponse:
        """列出所有使用验证期（2026-01-01 起）数据的回测，用于留痕审计。

        验证期数据只能用于否决、不能用于确认；只要看过就应留痕，避免
        "研究者自身成为模型的一部分"这类隐性过拟合。

        Args:
            limit: 返回条数上限，默认 200。

        Returns:
            ValidationUsageResponse（按创建时间倒序）。
        """
        boundaries = _period_boundaries()
        try:
            rows = (
                self._db.query(BacktestRunModel)
                .filter(BacktestRunModel.end_date >= boundaries.validation_start)
                .order_by(BacktestRunModel.created_at.desc())
                .limit(limit)
                .all()
            )
        except Exception:
            logger.warning("list_validation_usage DB query failed", exc_info=True)
            return ValidationUsageResponse()

        items: list[ValidationUsageItem] = []
        # 策略级验证期消费留痕（D-5）：与"跑过验证期回测"的回测级记录并列输出，
        # 便于判断"该策略的验证期是否已经被研究者消费过"
        consumed: dict[str, tuple[datetime | None, str | None]] = {}
        strategy_ids = {row.strategy_id for row in rows}
        if strategy_ids:
            try:
                for strategy_row in (
                    self._db.query(StrategyConfigModel)
                    .filter(StrategyConfigModel.strategy_id.in_(sorted(strategy_ids)))
                    .all()
                ):
                    consumed[strategy_row.strategy_id] = (
                        strategy_row.validation_consumed_at,
                        strategy_row.validation_consumed_note,
                    )
            except Exception:
                logger.warning("读取验证期消费留痕失败", exc_info=True)

        for row in rows:
            snapshot = row.config_snapshot or {}
            consumed_at, consumed_note = consumed.get(row.strategy_id, (None, None))
            items.append(
                ValidationUsageItem(
                    backtest_id=row.backtest_id,
                    strategy_id=row.strategy_id,
                    strategy_version=snapshot.get("version"),
                    config_hash=row.config_hash,
                    purpose=getattr(row, "purpose", None) or PURPOSE_RESEARCH,
                    purpose_reason=getattr(row, "purpose_reason", None),
                    start_date=row.start_date,
                    end_date=row.end_date,
                    created_at=row.created_at,
                    strategy_validation_consumed_at=consumed_at,
                    strategy_validation_consumed_note=consumed_note,
                )
            )
        return ValidationUsageResponse(items=items, total=len(items))

    def get_daily_results(self, backtest_id: str) -> list[BacktestDailyResult]:
        """返回回测每日组合绩效（含 252 交易日滚动夏普/索提诺）。"""
        try:
            rows = self._backtest_repo.find_daily_results(backtest_id)
            results = [
                BacktestDailyResult(
                    trade_date=r.trade_date,
                    portfolio_return=r.portfolio_return,
                    cumulative_return=r.cumulative_return,
                    drawdown=r.drawdown,
                    timing_regime=r.timing_regime,
                    total_exposure=r.total_exposure,
                    cash_ratio=r.cash_ratio,
                    positions=r.positions,
                    executed_positions=getattr(r, "executed_positions", None),
                    benchmark_return=getattr(r, "benchmark_return", None),
                    turnover=getattr(r, "turnover", None),
                    missing_bar_count=getattr(r, "missing_bar_count", 0),
                )
                for r in rows
            ]
            if rows:
                # 滚动指标在读取路径计算：与全期指标共用同一纯函数与口径，
                # 样本不足 252 个交易日的回测前段返回 None（前端留空展示）
                rolling = compute_rolling_metrics([r.portfolio_return for r in rows])
                results = [
                    m.model_copy(
                        update={
                            "rolling_sharpe_252": rm.sharpe_ratio if rm else None,
                            "rolling_sortino_252": rm.sortino_ratio if rm else None,
                        }
                    )
                    for m, rm in zip(results, rolling)
                ]
            return results
        except Exception:
            logger.warning("get_daily_results DB query failed", exc_info=True)
            return []

    def get_index_results(
        self, backtest_id: str, index_code: str | None = None
    ) -> list[BacktestIndexResult]:
        """返回回测每日每指数信号与收益。

        口径与实时信号保持一致：signal_score 为综合得分，target_weight 为信号目标仓位权重。
        scored=False 的行未参与评分，signal_score 为占位 0.0。
        """
        try:
            rows = self._backtest_repo.find_index_results(backtest_id, index_code)
            return [
                BacktestIndexResult(
                    trade_date=r.trade_date,
                    index_code=r.index_code,
                    signal_score=r.signal_score,
                    # 迁移 0053 之前的历史行没有该列，按"已评分"回退保持旧口径
                    scored=getattr(r, "scored", True),
                    selection_rebalanced=getattr(r, "selection_rebalanced", True),
                    signal_level=r.signal_level,
                    in_portfolio=r.in_portfolio,
                    index_return=r.index_return,
                    target_weight=getattr(r, "target_weight", None),
                )
                for r in rows
            ]
        except Exception:
            logger.warning("get_index_results DB query failed", exc_info=True)
            return []

    def run_backtest(self, backtest_id: str, require_pending: bool = False) -> None:
        """回测执行入口，统一使用 _run_backtest_loop。

        Args:
            backtest_id: 回测标识。
            require_pending: True 时用带条件的原子占用取代无条件置 running——
                仅当回测仍是 pending 才执行，已被占用或已执行过则直接返回。
                并行执行池（同一回测可能被派发多次）必须传 True。
        """
        try:
            row = self._backtest_repo.find_by_id(backtest_id)
            if row is None:
                logger.error("run_backtest: backtest_id %s not found", backtest_id)
                return

            # 状态流转统一走仓库（与 RunService 模式一致，避免绕过 repository）；
            # require_pending 走条件更新，避免多条进程同时写同一回测的结果
            if require_pending:
                if not self._backtest_repo.try_mark_running(backtest_id):
                    logger.info(
                        "回测 %s 已被占用或已执行过，跳过（require_pending）", backtest_id
                    )
                    return
                row = self._backtest_repo.find_by_id(backtest_id)
                if row is None:
                    return
            else:
                self._backtest_repo.mark_running(backtest_id)

            # 加载策略配置：优先使用创建时的快照，保证结果可复现；
            # 旧回测行无快照时回退到实时配置
            config_svc = StrategyConfigService(self._db)
            config = None
            if getattr(row, "config_snapshot", None):
                config = config_svc.parse_snapshot(row.config_snapshot)
            if config is None:
                config = config_svc.get_parsed_config(row.strategy_id)
            if config is None:
                raise ValueError(f"策略 {row.strategy_id} 配置不存在")

            self._record_data_cutoff(row)
            self._run_backtest_loop(backtest_id, row, config)

        except JobCancelledError:
            # 协作取消：回滚当前未提交分段后把回测落成 cancelled，
            # 并向上抛出以便队列把 background_job 也标记为已取消（B2）
            self._db.rollback()
            logger.info("回测已按请求取消: backtest_id=%s", backtest_id)
            try:
                self._backtest_repo.mark_cancelled(
                    backtest_id,
                    "回测已按请求取消",
                    warnings=[
                        BacktestWarning(
                            level="warning",
                            code="CANCELLED",
                            message="回测在执行过程中被取消，已保存的日结果保留",
                        ).model_dump()
                    ],
                )
            except Exception:
                self._db.rollback()
            raise

        except Exception as exc:
            self._db.rollback()
            logger.exception("run_backtest failed for %s", backtest_id)
            try:
                # 分段提交后，回滚只丢弃当前未提交分段；
                # 在失败信息中带上已保存的部分结果截止日期，便于用户判断进度
                message = str(exc)
                partial_date = self._backtest_repo.find_latest_daily_date(backtest_id)
                if partial_date is not None:
                    message = f"{message}；已保存部分结果至 {partial_date}"
                    logger.warning(
                        "[backtest] 部分结果: backtest_id=%s 已保存部分结果至 %s",
                        backtest_id,
                        partial_date,
                    )
                fail_warnings: list[BacktestWarning] = []
                if partial_date is not None:
                    fail_warnings.append(
                        BacktestWarning(
                            level="error",
                            code="PARTIAL_RESULT",
                            message=f"回测中途失败，已保存部分结果至 {partial_date}",
                        )
                    )
                self._backtest_repo.mark_failed(
                    backtest_id,
                    message,
                    warnings=[w.model_dump() for w in fail_warnings],
                )
            except Exception:
                self._db.rollback()

    def _record_data_cutoff(self, row: BacktestRunModel) -> None:
        """记录回测执行时的行情数据截止日期（非致命失败）。

        数据截止日期用于评估回测所用数据口径，方便优化报告说明
        "基于截至 X 的数据"；查询失败或为空时不阻塞回测主流程。

        Args:
            row: 回测记录 ORM 行。
        """
        try:
            cutoff = self._index_bar_repo.get_latest_trade_date()
            if cutoff is None:
                return
            row.data_cutoff_date = cutoff
            self._db.commit()
        except Exception:
            self._db.rollback()
            logger.warning("记录回测数据截止日期失败", exc_info=True)

    # ── 统一回测主循环 ────────────────────────────────────────────────────

    def _run_backtest_loop(
        self,
        backtest_id: str,
        row: BacktestRunModel,
        config: StrategyConfig,
        *,
        persist: bool = True,
        caches: BacktestRunCaches | None = None,
    ) -> dict[str, Any]:
        """统一回测主循环，替代旧的 signal/allocation 双分支。

        流程：
        1. 准备数据（标的、交易日、行情、估值，回望窗口按因子 lookback 推导）
        2. 解析交易日历并注入口径指纹（C1）
        3. 预计算全区间因子值
        3. 逐日执行引擎管线
        4. 按仓位/排名计算组合收益（毛收益，不扣除交易成本）
        5. 计算基准收益（如启用）
        6. 写入每日结果和指数结果
        7. 计算汇总绩效指标

        ``persist=False`` 时保留完全相同的计算路径，只是不写 ``backtest_daily_result``
        / ``backtest_index_result``、不提交、不落最终状态，并把逐日结果原样返回，
        供研究批量评估（D-1）在内存里算净口径与稳健性指标。**这是同一条执行路径**，
        因此指标口径与落库回测一致；但它不构成验收凭证——验收必须用
        ``backtest run`` 生成可审计的落库回测。

        Args:
            backtest_id: 回测标识（仅用于日志与落库）。
            row: 回测 ORM 行；``persist=False`` 时可以是未加入 session 的临时行。
            config: 策略配置。
            persist: 是否把逐日/逐指数结果与最终状态写入数据库。
            caches: 可选的跨变体共享缓存（行情快照与因子值），仅批量场景使用。

        Returns:
            结果字典：``metrics``（毛口径汇总指标）、``daily_rows``（``persist=False``
            时的逐日结果对象列表）、``warnings``、``candidate_pool``、
            ``trading_dates`` 与 ``params``。

        Raises:
            JobCancelledError: 收到协作取消请求时抛出（由调用方落 cancelled）。
        """
        # 回测使用一次性行情快照；关闭 checkpoint commit 后的 ORM 对象过期，
        # 避免收尾数据质量扫描访问行情字段时逐条触发隐式 SELECT。
        self._db.expire_on_commit = False
        # 严格口径（C1）：周度/月度调仓强依赖真实交易日历，解析失败即失败；
        # 每日调仓（或未配置 rebalance）与日历无关，记为 not_required。
        self._resolve_rebalance_calendar(row, config)
        universe, index_codes, trading_dates, all_bars, all_valuation, all_macro = (
            self._prepare_backtest_data(row, caches)
        )
        # 非交易日行情过滤（F-7）：上游偶尔把行情打在假期日期上，直接当交易日
        # 会多出假调仓日与假收益；日历不可用时保持原行为并留痕
        trading_dates, non_trading_dates = self._filter_non_trading_dates(
            row.start_date, row.end_date, trading_dates
        )
        if non_trading_dates:
            params = dict(row.params or {})
            params["_non_trading_bar_dates"] = [d.isoformat() for d in non_trading_dates]
            row.params = params

        # 策略显式限定资产范围时，必须在因子预计算前收窄回测池。此前仅在主循环
        # 构建上下文时过滤，会让扩散等成分面板因子为无关指数加载数据并产生缺失告警。
        if config.index_codes:
            configured_codes = set(config.index_codes)
            universe = [item for item in universe if item["index_code"] in configured_codes]
            index_codes = [code for code in index_codes if code in configured_codes]
            if not index_codes:
                raise ValueError("策略 index_codes 与回测标的范围无交集")

        # 若策略引用了市场级因子（如市场宽度），需加载全市场活跃指数行情，
        # 保证回测口径与实时预计算（全量活跃指数）一致
        self._ensure_market_scope_bars(config, trading_dates, all_bars)

        # 确保择时代理指数的行情数据和因子也被加载（代理指数可能不在策略标的池中）
        factor_index_codes = list(index_codes)
        if config.timing:
            for proxy_code in config.timing.proxy_index_codes:
                if proxy_code not in factor_index_codes:
                    factor_index_codes.append(proxy_code)
                    # 加载代理指数的日线数据
                    proxy_bars = self._load_all_index_bars(trading_dates, [proxy_code])
                    all_bars.update(proxy_bars)
                    # 加载代理指数的估值数据
                    proxy_val = self._load_all_valuation(trading_dates, [proxy_code])
                    all_valuation.update(proxy_val)

        # 预计算所有因子值（含择时代理指数）；批量评估时按
        # （窗口, 所需因子, 参与计算的指数）复用，避免每个变体重算一遍
        required_factor_ids = FactorProvider.collect_required_factor_ids(config)
        factor_cache_key = (
            _window_cache_key(row),
            ",".join(sorted(required_factor_ids)),
            ",".join(sorted(factor_index_codes)),
        )
        precomputed = caches.factors.get(factor_cache_key) if caches is not None else None
        if precomputed is None:
            precomputed = self._factor_provider.precompute_backtest_factors(
                config, trading_dates, factor_index_codes, all_bars, all_valuation, all_macro
            )
            if caches is not None:
                caches.factors[factor_cache_key] = precomputed

        # 记录实际回望天数与因子预热期（"前 N 个交易日因子数据不足"提示），
        # 写入回测 params 元数据，随 mark_success 一并持久化
        lookback_days = self._get_lookback_days()
        warmup_trading_days = self._estimate_warmup_trading_days(
            precomputed,
            trading_dates,
            factor_index_codes,
            required_factor_ids,
            _first_bar_by_code(all_bars),
        )
        run_params = row.params or {}
        run_params["_lookback_days"] = lookback_days
        run_params["_warmup_trading_days"] = warmup_trading_days
        row.params = run_params
        if warmup_trading_days > 0:
            logger.warning(
                "[backtest] 因子预热期: backtest_id=%s 前 %s 个交易日长周期因子可能数据不足",
                backtest_id,
                warmup_trading_days,
            )

        # 基准配置
        params = row.params or {}
        enable_benchmark = params.get("_enable_benchmark", True)
        benchmark_index_code = params.get("_benchmark_index_code", "000300")
        # 基准口径标记：策略/基准逐日序列均按"决策日归因"落账
        # （T 日决策 → T+1 行情），历史数据一次性回填命令据此跳过新记录
        params["_benchmark_alignment"] = "decision_day"
        row.params = params
        # 无风险利率（%）：默认 0（历史口径），预留从回测参数读取的通道
        risk_free_rate_pct = float(params.get("_annual_risk_free_rate_pct", 0.0) or 0.0)

        # 基准日收益率序列
        benchmark_returns: list[float] = []
        if enable_benchmark:
            # 确保基准指数的日线数据已加载（基准指数可能不在策略 universe 中）
            if benchmark_index_code not in index_codes:
                benchmark_bars = self._load_all_index_bars(trading_dates, [benchmark_index_code])
                all_bars.update(benchmark_bars)
            benchmark_returns = compute_buy_hold_benchmark(
                all_bars, benchmark_index_code, trading_dates
            )

        logger.info(
            "[backtest] 回测启动: backtest_id=%s strategy=%s 区间=%s~%s 标的=%d "
            "回望=%s天 预热=%s个交易日 执行模型=%s 数据质量=%s 基准=%s",
            backtest_id,
            row.strategy_id,
            trading_dates[0],
            trading_dates[-1],
            len(index_codes),
            lookback_days,
            warmup_trading_days,
            params.get("_execution_model", "t_plus_1_open"),
            params.get("_data_quality_mode", "warn"),
            benchmark_index_code if enable_benchmark else "禁用",
        )

        # ORM 行列表：用于 checkpoint 提交后释放对象引用
        daily_results: list[BacktestDailyResultModel] = []
        # 不落库模式（D-1）下保留全部逐日结果，供净口径与稳健性指标现算
        memory_rows: list[BacktestDailyResultModel] = []
        # 账户累积器：累计净值/回撤等账务状态由领域对象跟踪，
        # 主循环只负责生成当日持仓与收益（执行与绩效关注点分离）
        accumulator = BacktestDayAccumulator()
        prev_positions: dict[str, float] = {}
        pending_positions: dict[str, float] | None = None
        last_selection_rebalance_date: date | None = None
        last_risk_rebalance_date: date | None = None
        # 数据缺口天数：至少一个持仓资产当日收益受缺失行情影响的交易日数（B10）
        data_gap_days = 0
        # 数据缺口剔除记录：两种数据质量口径共用同一候选池，因此剔除集合一致，
        # 仅提示详细程度不同（strict 逐指数、warn 汇总）
        exclusion_dates: dict[str, list[date]] = {}
        exclusion_reasons: dict[str, set[str]] = {}
        # 有效候选池逐日规模（C6）：用于还原"早期实际可交易池只有 N 个"这类事实
        pool_sizes: list[int] = []
        # 进度跟踪：每完成约 10% 交易日写一次进度
        last_progress = 0
        total_dates = len(trading_dates)

        # 信号准确率累积器（替代 DB 查询）
        total_in_pos_count = 0
        total_in_pos_positive = 0

        # 预构建 universe 和 metadata（回测区间内不变，避免每日重复构造）
        codes = index_codes
        if config.index_codes:
            strategy_codes = set(config.index_codes)
            codes = [c for c in codes if c in strategy_codes]
        cached_metadata = {code: {"name_cn": code, "category": "broad_index"} for code in codes}

        for i, trade_date in enumerate(trading_dates):
            # 协作取消检查点（B2）：取消标记有短 TTL 缓存，不会每个交易日都查库
            ensure_not_cancelled()
            day_factors = precomputed.get(trade_date, {})
            next_date = trading_dates[i + 1] if i + 1 < len(trading_dates) else None

            execution_model = params.get("_execution_model", "t_plus_1_open")
            data_quality_mode = params.get("_data_quality_mode", "warn")
            # 口径统一：候选池与 data_quality_mode 无关，两种模式都使用"当日可执行
            # 候选池"。宽松模式此前把所有标的（含无法交易/因子缺失的标的）放进
            # 横截面 z-score 池，会改变统计量进而改变选股结果——同一配置在两种
            # 口径下得到完全不同的组合与收益，这是需要消除的口径分叉。
            day_codes, day_exclusion_reasons = self._build_candidate_pool(
                config, trade_date, next_date, index_codes, all_bars, day_factors, execution_model
            )
            for excluded_code in set(index_codes) - set(day_codes):
                exclusion_dates.setdefault(excluded_code, []).append(trade_date)
                exclusion_reasons.setdefault(excluded_code, set()).update(
                    day_exclusion_reasons.get(excluded_code, {"DATA_EXCLUDED"})
                )
            # 逐日有效候选池规模（C6）：与剔除记录共用同一次池计算，零额外开销
            pool_sizes.append(len(day_codes))
            day_universe = build_universe_items(
                [{"index_code": c, "name_cn": c} for c in day_codes]
            )
            day_metadata = {code: cached_metadata[code] for code in day_codes}

            # 构建上下文
            context = self._context_builder.build(
                config,
                trade_date,
                index_codes=day_codes,
                all_bars=all_bars,
                all_valuation=all_valuation,
                precomputed_factors=day_factors,
                cached_universe=day_universe,
                cached_metadata=day_metadata,
            )

            # 两条腿独立到期：选股腿改成分，风险腿仅按择时等比缩放总仓位。
            # 两腿同频（旧配置升级后的默认形态）时，两腿同日命中，合成为改造前的
            # "新成分 + 择时目标仓位"，历史回测口径不变。
            legs = self._select_legs(
                config,
                trade_date,
                last_selection_rebalance_date,
                last_risk_rebalance_date,
            )
            should_select = legs.selection
            should_scale_risk = legs.risk
            should_rebalance = should_select or should_scale_risk

            # 执行引擎（回测模式跳过详细的 StrategyResult 构建以提升性能）
            result = self._engine.run(config, context, include_details=False)

            target_positions = dict(result.positions) if result.positions else {}
            new_positions = compose_two_leg_positions(
                previous=prev_positions,
                selection_target=target_positions,
                timing_target_exposure=result.total_exposure,
                should_select=should_select,
                should_scale_risk=should_scale_risk,
            )
            executed_positions = dict(prev_positions)
            next_positions = dict(prev_positions)

            # 确定持仓与收益。收盘模式将信号延迟到下一交易日收盘执行。
            if execution_model == "t_plus_1_close":
                # T+1 收盘成交：上一决策日产生的目标仓位在今日收盘成交，
                # 因此本行收益区间 [close_T, close_{T+1}] 使用的仓位就是
                # ``pending_positions``（首次建仓前沿用上一日实际仓位）。
                # 历史缺陷：此处用 prev_positions 计收益，使新仓位的首个收益
                # 区间被旧仓位吃掉，实际等价于 T+2 收盘执行。
                executed_positions = (
                    dict(pending_positions)
                    if pending_positions is not None
                    else dict(prev_positions)
                )
                portfolio_return = compute_close_execution_return(
                    executed_positions, trade_date, next_date, all_bars
                )
                # 落库的 positions 表示当日实际持仓（与开盘模型一致）
                positions = executed_positions
                next_positions = executed_positions
                if pending_positions is not None:
                    # 成交腿 = 昨日实际持仓 → 今日成交后的仓位
                    turnover = compute_turnover(prev_positions, pending_positions)
                else:
                    turnover = 0.0
                if should_select:
                    last_selection_rebalance_date = trade_date
                if should_scale_risk:
                    last_risk_rebalance_date = trade_date
                pending_positions = new_positions if should_rebalance else None
                day_total_exposure = round(sum(executed_positions.values()), 4)
                day_cash_ratio = round(1.0 - day_total_exposure, 4)
            elif should_rebalance:
                # 调仓日：旧仓位持有至 T+1 开盘（隔夜段），新仓位自 T+1 开盘买入（日内段），
                # 当日收益 = 旧仓位 × [close_T, open_{T+1}] + 新仓位 × [open_{T+1}, close_{T+1}]
                portfolio_return = compute_rebalance_day_return(
                    prev_positions, new_positions, trade_date, next_date, all_bars
                )
                positions = new_positions
                executed_positions = new_positions
                next_positions = new_positions
                day_total_exposure = round(sum(positions.values()), 4)
                day_cash_ratio = round(1.0 - day_total_exposure, 4)
                # 换手率基于新旧目标仓位计算（C2）：统一按 Σ|Δw|/2，
                # 不再在 prev/new 为空时跳过——清仓腿与建仓腿同样是真实成本
                turnover = compute_turnover(prev_positions, new_positions)
                if should_select:
                    last_selection_rebalance_date = trade_date
                if should_scale_risk:
                    last_risk_rebalance_date = trade_date
            else:
                # 非调仓日：沿用上次持仓，按收盘对收盘计算收益
                positions = dict(prev_positions)
                executed_positions = positions
                next_positions = positions
                day_total_exposure = round(sum(positions.values()), 4)
                day_cash_ratio = round(1.0 - day_total_exposure, 4)
                turnover = 0.0
                portfolio_return = compute_allocation_return(
                    positions, trade_date, next_date, all_bars
                )

            # 统计当日受数据缺口影响的持仓资产数（B10），
            # 供逐日 missing_bar_count 记录与 data_gap_days 指标汇总
            if execution_model == "t_plus_1_close":
                missing_bar_count = count_missing_allocation_assets(
                    executed_positions, trade_date, next_date, all_bars
                )
            elif should_rebalance:
                missing_bar_count = count_missing_rebalance_assets(
                    prev_positions, positions, trade_date, next_date, all_bars
                )
            else:
                missing_bar_count = count_missing_allocation_assets(
                    positions, trade_date, next_date, all_bars
                )
            if missing_bar_count > 0:
                data_gap_days += 1

            # 持仓统计
            has_positions = bool(executed_positions)
            # 更新累计净值、峰值与回撤（领域对象记账）
            accumulator.apply_day(portfolio_return, has_positions)
            cumulative_return_pct = accumulator.cumulative_return_pct
            drawdown = accumulator.drawdown_pct

            # 基准收益
            benchmark_ret = benchmark_returns[i] if i < len(benchmark_returns) else None

            daily_row = BacktestDailyResultModel(
                backtest_id=backtest_id,
                trade_date=trade_date,
                portfolio_return=round(portfolio_return, 4),
                cumulative_return=round(cumulative_return_pct, 4),
                drawdown=round(drawdown, 4),
                timing_regime=result.timing.regime if result.timing else None,
                total_exposure=day_total_exposure,
                cash_ratio=day_cash_ratio,
                positions=positions if positions else None,
                executed_positions=executed_positions if executed_positions else None,
                benchmark_return=round(benchmark_ret, 4) if benchmark_ret is not None else None,
                turnover=round(turnover, 4) if turnover > 0 else None,
                missing_bar_count=missing_bar_count,
            )
            if persist:
                self._backtest_repo.add_daily_result(daily_row)
                daily_results.append(daily_row)
            else:
                memory_rows.append(daily_row)

            # 写入指数结果并获取持仓统计
            day_pos_count, day_pos_positive = self._write_index_results(
                backtest_id,
                trade_date,
                next_date,
                universe,
                result,
                all_bars,
                result.positions if result.positions else {},
                timing_regime=result.timing.regime if result.timing else None,
                scoring_mode=config.score.scoring_mode,
                selection_rebalanced=should_select,
                persist=persist,
            )
            total_in_pos_count += day_pos_count
            total_in_pos_positive += day_pos_positive

            logger.debug(
                "[backtest] 日结果: backtest_id=%s date=%s regime=%s exposure=%s 持仓=%s 收益=%s%%",
                backtest_id,
                trade_date,
                result.timing.regime if result.timing else None,
                day_total_exposure,
                {k: round(v, 4) for k, v in positions.items()},
                round(portfolio_return, 4),
            )

            prev_positions = next_positions

            # 每 10% 交易日更新一次进度（最少 1 天触发）
            if total_dates > 0:
                new_progress = int((i + 1) / total_dates * 100)
                if new_progress - last_progress >= 10:
                    if persist:
                        self._backtest_repo.update_progress(backtest_id, new_progress)
                    last_progress = new_progress
                    logger.info(
                        "[backtest] 进度: backtest_id=%s %s/%s (%s%%) date=%s 累计收益=%s%%",
                        backtest_id,
                        i + 1,
                        total_dates,
                        new_progress,
                        trade_date,
                        round(cumulative_return_pct, 2),
                    )

            # 每 100 天 checkpoint 提交一次：
            # - 释放事务大小，避免长区间回测的单一巨大事务
            # - 中途失败时已提交的分段结果保留，前端可查看部分权益曲线
            # - progress 裸 SQL 更新随本次 commit 一起对其它连接可见
            if persist and (i + 1) % 100 == 0:
                self._db.flush()
                self._db.commit()
                daily_results.clear()  # 释放 ORM 对象，账户累积器已保留关键数据

        finalize_started = perf_counter()
        logger.info(
            "[backtest] 收尾开始: backtest_id=%s 已完成循环=%s天，准备最终 flush",
            backtest_id,
            total_dates,
        )
        if persist:
            self._db.flush()
        flush_elapsed = perf_counter() - finalize_started
        logger.info(
            "[backtest] 收尾 flush 完成: backtest_id=%s 耗时=%.3fs",
            backtest_id,
            flush_elapsed,
        )

        # 计算汇总指标（使用账户累积器与内存中的信号准确率，避免依赖已 flush 的 ORM 对象）
        metrics_started = perf_counter()
        metrics = self._compute_summary_metrics(
            accumulator,
            benchmark_returns,
            total_in_pos_count=total_in_pos_count,
            total_in_pos_positive=total_in_pos_positive,
            data_gap_days=data_gap_days,
            annual_risk_free_rate_pct=risk_free_rate_pct,
        )
        logger.info(
            "[backtest] 汇总指标完成: backtest_id=%s 耗时=%.3fs",
            backtest_id,
            perf_counter() - metrics_started,
        )
        # 收集结构化警告：预热期 / 因子缺失 / 数据缺口 / 基准缺失，
        # 随成功状态一并持久化，前端轮询时按 key 去重弹窗。
        warnings_started = perf_counter()
        run_warnings: list[BacktestWarning] = []
        # 口径提示（C1）：使用本地日历表快照而非上游数据源时显式告知，
        # 便于判断"同一配置两条回测的调仓日历是否来自同一来源"
        if (row.params or {}).get("_calendar_source") == "database":
            run_warnings.append(
                BacktestWarning(
                    level="info",
                    code="CALENDAR_SOURCE_DATABASE",
                    message=(
                        "交易日历来自本地 trading_calendar 表快照"
                        "（上游 Tushare/AkShare 当前不可用，快照可能滞后于官方日历）"
                    ),
                )
            )
        # 非交易日行情（F-7）：行情表里存在日历判定为非交易日的日期时
        # 显式告知，避免"回测多出一个假调仓日"被当成正常波动
        non_trading_dates = (row.params or {}).get("_non_trading_bar_dates") or []
        if non_trading_dates:
            run_warnings.append(
                BacktestWarning(
                    level="warning",
                    code="NON_TRADING_BAR",
                    message=(
                        f"行情数据包含 {len(non_trading_dates)} 个非交易日"
                        f"（{', '.join(non_trading_dates[:5])}"
                        f"{' 等' if len(non_trading_dates) > 5 else ''}），"
                        "已从回测交易日中剔除（上游把行情打在假期日期上）"
                    ),
                )
            )
        # point-in-time 标的池提示：纳入"已停用但区间内仍存续"的指数，
        # 让研究者知道资产域包含退市/停发指数（而非只统计活到今天的资产）
        effective_codes = set(index_codes)
        delisted_codes = sorted(
            item["index_code"]
            for item in universe
            if item.get("is_active") is False and item["index_code"] in effective_codes
        )
        if delisted_codes:
            run_warnings.append(
                BacktestWarning(
                    level="info",
                    code="UNIVERSE_INCLUDES_DELISTED",
                    message=(
                        f"标的池包含 {len(delisted_codes)} 个已停用（退市/停发）指数："
                        f"{'、'.join(delisted_codes[:5])}"
                        f"{' 等' if len(delisted_codes) > 5 else ''}；"
                        "回测按 point-in-time 口径纳入区间内仍存续的指数，"
                        "其停止发布后的日期由逐日候选池自动剔除（避免幸存者偏差）"
                    ),
                )
            )
        if warmup_trading_days > 0:
            run_warnings.append(
                BacktestWarning(
                    level="warning",
                    code="WARMUP",
                    message=(
                        f"回测前 {warmup_trading_days} 个交易日长周期因子数据不足"
                        "（按各指数自身首根 K 线起算的预热期），前段信号与绩效参考价值有限"
                    ),
                )
            )
        # 有效候选池时间线（C6）：逐日规模游程编码 + 剔除指数区间，
        # 随 mark_success 落库；缩水到理论池以下时补一条信息级提示
        candidate_pool = self._build_candidate_pool_timeline(
            trading_dates, pool_sizes, len(codes), exclusion_dates, exclusion_reasons
        )
        if candidate_pool["min_size"] < candidate_pool["base_size"]:
            shrunk = next(
                (
                    seg
                    for seg in candidate_pool["segments"]
                    if seg["size"] == candidate_pool["min_size"]
                ),
                None,
            )
            run_warnings.append(
                BacktestWarning(
                    level="info",
                    code="CANDIDATE_POOL_SHRINK",
                    message=(
                        f"有效候选池 {candidate_pool['base_size']} → 最小 "
                        f"{candidate_pool['min_size']}"
                        + (
                            f"（{shrunk['start_date']}~{shrunk['end_date']}）"
                            if shrunk is not None
                            else ""
                        )
                        + "，逐日时间线见 stability.candidate_pool"
                    ),
                )
            )
        missing_factor_started = perf_counter()
        required_factor_ids = FactorProvider.collect_required_factor_ids(config)
        run_warnings.extend(
            self._collect_missing_factor_warnings(
                precomputed,
                trading_dates,
                factor_index_codes,
                required_factor_ids,
            )
        )
        logger.info(
            "[backtest] 缺失因子告警完成: backtest_id=%s factors=%s dates=%s indexes=%s "
            "warnings=%s 耗时=%.3fs",
            backtest_id,
            len(required_factor_ids),
            len(trading_dates),
            len(factor_index_codes),
            len(run_warnings),
            perf_counter() - missing_factor_started,
        )
        if exclusion_dates:
            exclusion_warning_started = perf_counter()
            excluded_days_total = sum(len(dates) for dates in exclusion_dates.values())
            # 缺失开盘价导致的剔除单列（F-8）：执行模型是 t_plus_1_open，
            # 缺开盘价的指数会被静默排除在候选池之外，实际资产域可能远小于
            # 配置的资产池；这里显式给出"哪些资产、被排除了多少天"
            execution_warning = _execution_price_warning(exclusion_dates, exclusion_reasons)
            if execution_warning is not None:
                run_warnings.append(execution_warning)
            if data_quality_mode == "strict":
                for code, dates in list(exclusion_dates.items())[:5]:
                    run_warnings.append(
                        BacktestWarning(
                            level="warning",
                            code="DATA_EXCLUDED",
                            message=(
                                f"数据缺口剔除指数 {code} {len(dates)} 个交易日，"
                                f"范围 {dates[0]}~{dates[-1]}；"
                                f"原因：{','.join(sorted(exclusion_reasons.get(code, set())))}"
                            ),
                            index_code=code,
                        )
                    )
            else:
                # 宽松口径：决策与严格口径一致，仅把逐指数明细压缩为一条汇总提示
                run_warnings.append(
                    BacktestWarning(
                        level="warning",
                        code="DATA_EXCLUDED",
                        message=(
                            f"数据缺口剔除 {len(exclusion_dates)} 个指数、"
                            f"合计 {excluded_days_total} 个交易日（候选池口径与严格模式一致，"
                            "切换严格模式可查看逐指数明细）"
                        ),
                    )
                )
            logger.info(
                "[backtest] 数据缺口剔除告警完成: backtest_id=%s mode=%s excluded_indexes=%s "
                "warnings=%s 耗时=%.3fs",
                backtest_id,
                data_quality_mode,
                len(exclusion_dates),
                len(run_warnings),
                perf_counter() - exclusion_warning_started,
            )
        data_gap_started = perf_counter()
        run_warnings.extend(
            self._collect_data_gap_warnings(
                trading_dates,
                index_codes,
                all_bars,
                execution_model=execution_model,
                data_quality_mode=data_quality_mode,
            )
        )
        logger.info(
            "[backtest] 行情缺口告警完成: backtest_id=%s dates=%s indexes=%s warnings=%s "
            "耗时=%.3fs",
            backtest_id,
            len(trading_dates),
            len(index_codes),
            len(run_warnings),
            perf_counter() - data_gap_started,
        )
        if enable_benchmark:
            benchmark_warning_started = perf_counter()
            bench_missing = [d for d in trading_dates if (benchmark_index_code, d) not in all_bars]
            if bench_missing:
                run_warnings.append(
                    BacktestWarning(
                        level="warning",
                        code="BENCHMARK_MISSING",
                        message=(
                            f"基准指数 {benchmark_index_code} 有 {len(bench_missing)} 个交易日"
                            f"缺行情数据（{bench_missing[0]}~{bench_missing[-1]}），基准对比存在缺口"
                        ),
                        index_code=benchmark_index_code,
                    )
                )
            logger.info(
                "[backtest] 基准缺口告警完成: backtest_id=%s missing_dates=%s 耗时=%.3fs",
                backtest_id,
                len(bench_missing),
                perf_counter() - benchmark_warning_started,
            )
        logger.info(
            "[backtest] 告警收集完成: backtest_id=%s warnings=%s 耗时=%.3fs",
            backtest_id,
            len(run_warnings),
            perf_counter() - warnings_started,
        )
        logger.info(
            "[backtest] 回测完成: backtest_id=%s 累计收益=%s%% 最大回撤=%s%% 夏普=%s "
            "胜率=%s%% 信号准确率=%s%%",
            backtest_id,
            metrics.get("cumulative_return_pct", 0.0),
            metrics.get("max_drawdown_pct", 0.0),
            metrics.get("sharpe_ratio", 0.0),
            metrics.get("win_rate_pct", 0.0),
            metrics.get("signal_accuracy_pct", 0.0),
        )
        mark_success_started = perf_counter()
        if persist:
            self._backtest_repo.mark_success(
                backtest_id,
                metrics,
                warnings=[w.model_dump() for w in run_warnings],
                candidate_pool=candidate_pool,
            )
        else:
            # 不落库模式：结果只回传给调用方（研究批量评估），
            # 数据库里不留下任何"看起来像验收记录"的痕迹
            logger.info(
                "[backtest] 研究模式完成（未落库）: run_id=%s 累计收益=%s%% 夏普=%s",
                backtest_id,
                metrics.get("cumulative_return_pct", 0.0),
                metrics.get("sharpe_ratio", 0.0),
            )
        logger.info(
            "[backtest] 最终状态提交完成: backtest_id=%s 耗时=%.3fs 总收尾耗时=%.3fs",
            backtest_id,
            perf_counter() - mark_success_started,
            perf_counter() - finalize_started,
        )
        return {
            "metrics": metrics,
            "daily_rows": memory_rows,
            "warnings": [w.model_dump() for w in run_warnings],
            "candidate_pool": candidate_pool,
            "trading_dates": trading_dates,
            "params": dict(row.params or {}),
        }

    def _build_candidate_pool_timeline(
        self,
        trading_dates: list[date],
        pool_sizes: list[int],
        base_size: int,
        exclusion_dates: dict[str, list[date]],
        exclusion_reasons: dict[str, set[str]],
    ) -> dict[str, Any]:
        """构建逐日有效候选池时间线（C6）。

        - 逐日规模做**游程编码**（连续相同规模合并为一段），避免把 2400 个交易日
          逐条落库；
        - 剔除明细按"指数—日期区间"聚合，按交易日数降序截断到
          ``candidate_pool_exclusion_limit``。

        Args:
            trading_dates: 回测交易日列表（升序）。
            pool_sizes: 与 ``trading_dates`` 等长的逐日有效候选池规模。
            base_size: 回测标的池的理论规模。
            exclusion_dates: 指数 → 被剔除交易日列表。
            exclusion_reasons: 指数 → 剔除原因集合。

        Returns:
            可直接写入 ``backtest_run.candidate_pool`` 的字典。
        """
        total_days = len(trading_dates)
        if not pool_sizes or total_days == 0:
            return {
                "base_size": base_size,
                "min_size": 0,
                "max_size": 0,
                "median_size": 0,
                "trading_days": 0,
                "pool_coverage_ratio": 0.0,
                "segments": [],
                "exclusions": [],
                "truncated_exclusions": False,
            }

        # 游程编码：把连续相同规模合并为一段
        # 日期统一转 ISO 字符串——candidate_pool 列是 JSONB，psycopg 的 json
        # 编码器不认 date 对象（读取时由 pydantic 还原为 date）
        segments: list[dict[str, Any]] = []
        start_index = 0
        for index in range(1, len(pool_sizes) + 1):
            if index < len(pool_sizes) and pool_sizes[index] == pool_sizes[start_index]:
                continue
            segments.append(
                {
                    "start_date": trading_dates[start_index].isoformat(),
                    "end_date": trading_dates[index - 1].isoformat(),
                    "trading_days": index - start_index,
                    "size": pool_sizes[start_index],
                }
            )
            start_index = index

        sorted_sizes = sorted(pool_sizes)
        median_size = sorted_sizes[len(sorted_sizes) // 2]
        total_slots = base_size * total_days
        coverage = (sum(pool_sizes) / total_slots) if total_slots > 0 else 0.0

        limit = get_settings().candidate_pool_exclusion_limit
        exclusions = sorted(
            (
                {
                    "index_code": code,
                    "first_date": dates[0].isoformat(),
                    "last_date": dates[-1].isoformat(),
                    "trading_days": len(dates),
                    "reasons": sorted(exclusion_reasons.get(code, set())),
                }
                for code, dates in exclusion_dates.items()
                if dates
            ),
            key=lambda item: item["trading_days"],
            reverse=True,
        )
        return {
            "base_size": base_size,
            "min_size": min(pool_sizes),
            "max_size": max(pool_sizes),
            "median_size": median_size,
            "trading_days": total_days,
            "pool_coverage_ratio": round(coverage, 4),
            "segments": segments,
            "exclusions": exclusions[:limit],
            "truncated_exclusions": len(exclusions) > limit,
        }

    def _prepare_backtest_data(
        self,
        row: BacktestRunModel,
        caches: BacktestRunCaches | None = None,
    ) -> tuple[
        list[dict[str, Any]], list[str], list[date], dict, dict, dict[str, dict[str, float]]
    ]:
        """准备回测通用数据：标的、交易日、行情、估值、宏观指标。

        Args:
            row: 回测 ORM 行（或研究批量评估的临时行）。
            caches: 可选的跨变体共享缓存；命中同一窗口时直接复用已加载的行情
                快照（批量评估里几十个变体的行情、估值与宏观数据完全相同）。

        Returns:
            (universe, index_codes, trading_dates, all_bars, all_valuation, all_macro) 元组。
        """
        cache_key = _window_cache_key(row)
        if caches is not None:
            cached = caches.data.get(cache_key)
            if cached is not None:
                return cached

        universe = self._resolve_index_universe(row.universe_filter, row.start_date)
        if not universe:
            raise ValueError("回测标的范围为空，请检查 universe_filter 配置")

        index_codes = [u["index_code"] for u in universe]
        trading_dates = self._get_index_trading_dates(row.start_date, row.end_date, index_codes)
        if not trading_dates:
            raise ValueError(f"区间 {row.start_date} ~ {row.end_date} 内无交易日数据")

        all_bars = self._load_all_index_bars(trading_dates, index_codes)
        all_valuation = self._load_all_valuation(trading_dates, index_codes)
        all_macro = self._load_all_macro()

        prepared = (universe, index_codes, trading_dates, all_bars, all_valuation, all_macro)
        if caches is not None:
            caches.data[cache_key] = prepared
        return prepared

    def _ensure_market_scope_bars(
        self,
        config: StrategyConfig,
        trading_dates: list[date],
        all_bars: dict,
    ) -> None:
        """为市场级因子补充加载全市场活跃指数行情数据（就地更新 all_bars）。

        当策略引用的任一因子的 FactorSpec.market_scope 为 True 时，
        额外加载全市场指数的日线数据作为因子上下文；这些数据只参与
        因子计算，不进入回测标的池，避免影响组合收益口径。

        集合口径与回测标的池一致，按 point-in-time 取"区间起点仍在存续"的指数
        （``find_for_period``）：否则市场级因子（如市场宽度）只统计活到今天的
        指数，历史上退市的成分被整段抹掉。实时路径只能用当日活跃集合
        （实时无法知道未来），这是模式固有的差异。

        Args:
            config: 策略配置，用于推导所需因子 ID。
            trading_dates: 回测交易日列表。
            all_bars: 已加载的行情数据字典，就地补充全市场数据。
        """
        factor_ids = FactorProvider.collect_required_factor_ids(config)
        need_market = any(
            (c := self._registry.get(fid)) is not None and getattr(c.spec, "market_scope", False)
            for fid in factor_ids
        )
        if not need_market:
            return

        active = BenchmarkIndexRepository(self._db).find_for_period(trading_dates[0])
        market_codes = [idx.index_code for idx in active]
        loaded_codes = {code for code, _ in all_bars}
        missing = [code for code in market_codes if code not in loaded_codes]
        if not missing:
            return
        logger.info(
            "[backtest] 策略引用市场级因子，补充加载全市场行情: 新增 %d 个指数",
            len(missing),
        )
        extra_bars = self._load_all_index_bars(trading_dates, missing)
        all_bars.update(extra_bars)

    def _write_index_results(
        self,
        backtest_id: str,
        trade_date: date,
        next_date: date | None,
        universe: list[dict[str, Any]],
        result: Any,
        all_bars: dict,
        signal_positions: dict[str, float],
        timing_regime: str | None = None,
        scoring_mode: str = "absolute",
        selection_rebalanced: bool = True,
        persist: bool = True,
    ) -> tuple[int, int]:
        """写入每日每指数的回测结果，使用与实时一致的信号等级判定逻辑。

        与实时 `_build_strategy_results` 共用同一 `determine_signal_level` 输入：
        score=当日综合得分、target_weight=当日目标权重（result.positions）、
        timing_regime=当日择时 regime、scoring_mode=策略评分模式。

        落库范围是当日 universe 全量标的，而 `result.scores` 只含通过候选池与
        过滤规则的资产（过滤器会从 scores 中删除未通过的资产）。两者的差集用
        `scored=False` 标记，其 `signal_score` 是占位 0.0——消费该列的横截面
        统计（组合分数 IC）必须跳过这些行，否则占位 0 会占据固定底部名次。

        Args:
            backtest_id: 回测标识。
            trade_date: 当日交易日。
            next_date: 下一交易日（无则为 None）。
            universe: 当日标的列表。
            result: 引擎执行结果。
            all_bars: 行情数据。
            signal_positions: 当日目标权重。
            timing_regime: 当日择时 regime。
            scoring_mode: 策略评分模式。
            selection_rebalanced: 当日是否执行选股调仓；组合分数 IC 只评估这类日期。
            persist: False 时只统计持仓命中数（信号准确率），不写指数结果行
                （D-1 研究批量评估路径）。

        Returns:
            (in_portfolio_count, in_portfolio_positive_count) 元组。
        """
        score_map = result.scores
        in_pos_count = 0
        in_pos_positive = 0

        for item in universe:
            code = item["index_code"]
            target_weight = signal_positions.get(code, 0.0)
            # 未进入 scores 的资产当日没有得分：记录 scored=False，得分落占位 0.0
            scored = code in score_map
            score = score_map.get(code, 0.0)

            level, _ = determine_signal_level(
                score=score,
                target_weight=target_weight,
                timing_regime=timing_regime,
                scoring_mode=scoring_mode,
            )
            in_portfolio = target_weight > 0
            signal_score = round(score, 2)

            idx_ret = None
            if next_date and in_portfolio:
                idx_ret = self._get_index_return(code, trade_date, next_date, all_bars)
                in_pos_count += 1
                if idx_ret is not None and idx_ret > 0:
                    in_pos_positive += 1

            if persist:
                self._backtest_repo.add_index_result(
                    BacktestIndexResultModel(
                        backtest_id=backtest_id,
                        trade_date=trade_date,
                        index_code=code,
                        signal_score=signal_score,
                        scored=scored,
                        selection_rebalanced=selection_rebalanced,
                        signal_level=level,
                        in_portfolio=in_portfolio,
                        index_return=idx_ret,
                        target_weight=round(target_weight, 4),
                    )
                )

        return in_pos_count, in_pos_positive

    # ── 收益计算 ───────────────────────────────────────────────────────────

    def _compute_allocation_return(
        self,
        positions: dict[str, float],
        trade_date: date,
        next_date: date | None,
        all_bars: dict,
    ) -> float:
        """按仓位分配方案计算指数组合 T+1 收益（委托领域函数）。"""
        return compute_allocation_return(positions, trade_date, next_date, all_bars)

    # ── 调仓检查 ───────────────────────────────────────────────────────────

    def _build_candidate_pool(
        self,
        config: StrategyConfig,
        trade_date: date,
        next_date: date | None,
        index_codes: list[str],
        all_bars: dict,
        day_factors: dict[tuple[str, str], float | None],
        execution_model: str,
    ) -> tuple[list[str], dict[str, set[str]]]:
        """构建当日"可执行候选池"（横截面评分/排名/过滤与收益口径的唯一入口）。

        该候选池与 `data_quality_mode` 无关：两种口径都用同一池子选股，避免
        宽松口径把"无法交易/因子缺失"的标的塞进横截面 z-score 池，导致统计量
        变化进而改变选股结果（同一配置出现两套完全不同绩效）。

        入池条件（全部满足）：
        1. 当日 bar 存在且收盘价有效；
        2. 策略引用的资产级因子（评分 + 过滤 + compare_to + regime 覆盖）当日均有值；
        3. 若引用 ATR/Donchian/月线等因子，则当日最高/最低价有效；
        4. 存在下一交易日时，次日行情可支撑收益结算：次日 bar 与收盘价有效
           （``MISSING_NEXT_BAR``）、需要高低价时次日高低价有效
           （``MISSING_NEXT_HIGH_LOW``）；执行模型为 T+1 开盘时还需次日开盘价有效
           （``MISSING_OPEN``）。

        说明（已知取舍）：条件 1~3 只用当日信息，条件 4 需要"次日行情是否存在"，
        属 ex-ante 可执行域约束——若不剔除，缺次日行情的资产会以 0 收益参与组合，
        反而系统性低估波动。剔除明细逐日记录在候选池时间线（C6）中，可审计。

        `data_quality_mode` 只影响缺口提示的详细程度（strict 逐指数 / warn 汇总），
        不影响候选池、选股结果与收益序列。

        Args:
            config: 策略配置。
            trade_date: 当前信号交易日。
            next_date: 下一交易日。
            index_codes: 候选指数代码。
            all_bars: 预加载行情。
            day_factors: 当前日预计算因子值。
            execution_model: 执行模型。

        Returns:
            当日可参与策略决策和收益计算的指数代码列表，以及每个被排除指数的原因集合。
        """
        required_ids = set(config.score.factors)
        if config.filters:
            for rule in config.filters.rules:
                required_ids.add(rule.factor)
                if rule.compare_to:
                    required_ids.add(rule.compare_to)
        for regime_rule in config.regime_rules.values():
            if regime_rule.score:
                required_ids.update(regime_rule.score.factors)
            if regime_rule.filters:
                for rule in regime_rule.filters.rules:
                    required_ids.add(rule.factor)
                    if rule.compare_to:
                        required_ids.add(rule.compare_to)
        asset_factor_ids = {
            spec.factor_id for spec in self._registry.specs() if spec.value_shape == "asset"
        }
        required_ids &= asset_factor_ids
        needs_high_low = any(
            factor_id.startswith(("atr_", "donchian_", "monthly_", "rsrs", "price_position_ir_"))
            for factor_id in required_ids
        )
        result: list[str] = []
        reasons: dict[str, set[str]] = {}
        for code in index_codes:
            bar = all_bars.get((code, trade_date))
            if bar is None or _price_invalid(getattr(bar, "close_price", None)):
                reasons.setdefault(code, set()).add("MISSING_CLOSE")
                continue
            if needs_high_low and any(
                _price_invalid(getattr(bar, field)) for field in ("high_price", "low_price")
            ):
                reasons.setdefault(code, set()).add("MISSING_HIGH_LOW")
                continue
            if any(day_factors.get((code, factor_id)) is None for factor_id in required_ids):
                reasons.setdefault(code, set()).add("MISSING_FACTOR")
                continue
            if next_date is None:
                result.append(code)
                continue
            next_bar = all_bars.get((code, next_date))
            if next_bar is None or _price_invalid(getattr(next_bar, "close_price", None)):
                reasons.setdefault(code, set()).add("MISSING_NEXT_BAR")
                continue
            if needs_high_low and any(
                _price_invalid(getattr(next_bar, field)) for field in ("high_price", "low_price")
            ):
                reasons.setdefault(code, set()).add("MISSING_NEXT_HIGH_LOW")
                continue
            if execution_model == "t_plus_1_open" and _price_invalid(
                getattr(next_bar, "open_price", None)
            ):
                reasons.setdefault(code, set()).add("MISSING_OPEN")
                continue
            result.append(code)
        return result, reasons

    def _resolve_rebalance_calendar(self, row: BacktestRunModel, config: StrategyConfig) -> str:
        """解析调仓所需的交易日历，并把来源写入口径指纹（C1）。

        严格口径：周度/月度调仓必须有真实交易日历（上游数据源或本地
        `trading_calendar` 表快照），解析失败直接抛
        ``TradingCalendarUnavailableError``，回测落 failed——不允许按星期近似，
        否则同一 ``config_hash`` 会出现两套调仓日历、两套结果。

        每日调仓（或策略未配置 rebalance）与日历无关，记为 ``not_required``。

        Args:
            row: 回测 ORM 行（提供区间与研究区间校验）。
            config: 策略配置。

        Returns:
            口径指纹值：upstream / database / not_required。

        Raises:
            TradingCalendarUnavailableError: 需要日历但无法解析出有效日历时抛出。
        """
        schedules = (
            (config.rebalance.selection, config.rebalance.risk)
            if config.rebalance is not None
            else ()
        )
        frequencies = [schedule.frequency for schedule in schedules]
        needs_calendar = any(frequency in ("weekly", "biweekly", "monthly") for frequency in frequencies)

        if not needs_calendar:
            self._rebalance_scheduler = None
            self._set_param(row, "_calendar_source", "not_required", commit=True)
            return "not_required"

        calendar, source = resolve_trading_calendar(
            self._db, required_range=(row.start_date, row.end_date)
        )
        self._rebalance_scheduler = DefaultRebalanceScheduler(calendar)
        # 立即提交：即使回测随后失败，口径指纹也应落库以便审计
        self._set_param(row, "_calendar_source", source, commit=True)
        logger.info(
            "[backtest] 调仓日历已解析: backtest_id=%s frequencies=%s source=%s",
            row.backtest_id,
            frequencies,
            source,
        )
        return source

    def _set_param(
        self, row: BacktestRunModel, key: str, value: Any, *, commit: bool = False
    ) -> None:
        """写入回测 params 的口径指纹键（``_`` 前缀元数据）。

        Args:
            row: 回测 ORM 行。
            key: 键名（如 ``_calendar_source``）。
            value: 键值。
            commit: 是否立即提交（默认随 checkpoint 一起提交）。
        """
        params = dict(row.params or {})
        params[key] = value
        row.params = params
        if commit:
            self._db.commit()

    def _check_rebalance_schedule(
        self,
        schedule: RebalanceScheduleConfig | None,
        trade_date: date,
        last_rebalance_date: date | None,
    ) -> bool:
        """检查某条腿当日是否到期。

        委托给 DefaultRebalanceScheduler，与实盘模式使用相同的交易日历对齐逻辑。
        未传腿配置（或配置未启用调仓模块）时默认为每日调仓。

        Args:
            schedule: 单条腿的频率配置，None 表示每日调仓。
            trade_date: 当前交易日。
            last_rebalance_date: 上次调仓日期。

        Returns:
            是否应该调仓。

        Raises:
            ValueError: 需要交易日历但未解析出调度器时抛出。
        """
        if schedule is None:
            return True
        if schedule.frequency == "daily":
            return True
        if self._rebalance_scheduler is None:
            raise ValueError("调仓调度器未初始化：周度/月度/双周调仓前必须先解析交易日历（C1）")
        return self._rebalance_scheduler.should_rebalance(
            schedule, trade_date, last_rebalance_date
        )

    def _check_rebalance(
        self, config: StrategyConfig, trade_date: date, last_rebalance_date: date | None
    ) -> bool:
        """兼容旧内部调用：按选股腿判断当日是否调仓。

        Args:
            config: 策略配置。
            trade_date: 当前交易日。
            last_rebalance_date: 上次调仓日期。

        Returns:
            选股腿当日是否到期。
        """
        return self._check_rebalance_schedule(
            config.rebalance.selection if config.rebalance else None,
            trade_date,
            last_rebalance_date,
        )

    def _select_legs(
        self,
        config: StrategyConfig,
        trade_date: date,
        last_selection_date: date | None,
        last_risk_date: date | None,
    ) -> RebalanceLegs:
        """判定当日到期的调仓腿（回测主循环与口径指纹共用）。

        Args:
            config: 策略配置。
            trade_date: 当前交易日。
            last_selection_date: 上次选股腿调仓日。
            last_risk_date: 上次风险腿调仓日。

        Returns:
            两腿到期情况；未配置调仓模块时为每日调仓。

        Raises:
            ValueError: 需要交易日历但未解析出调度器时抛出。
        """
        rebalance = config.rebalance
        if rebalance is not None and self._rebalance_scheduler is None:
            needed = {
                rebalance.selection.frequency,
                rebalance.risk.frequency,
            } & {"weekly", "biweekly", "monthly"}
            if needed:
                raise ValueError(
                    "调仓调度器未初始化：周度/月度/双周调仓前必须先解析交易日历（C1）"
                )
            # 两腿均为每日调仓：无需日历，直接短路避免构造调度器
            return RebalanceLegs(selection=True, risk=True)
        return select_active_legs(
            self._rebalance_scheduler,
            rebalance,
            trade_date,
            last_selection_date,
            last_risk_date,
        )

    # ── 汇总指标 ───────────────────────────────────────────────────────────

    def _compute_summary_metrics(
        self,
        accumulator: BacktestDayAccumulator,
        benchmark_returns: list[float],
        total_in_pos_count: int = 0,
        total_in_pos_positive: int = 0,
        data_gap_days: int = 0,
        annual_risk_free_rate_pct: float = 0.0,
    ) -> dict[str, Any]:
        """计算回测汇总绩效指标，集成专业指标和基准对比。

        Args:
            accumulator: 账户累积器（每日收益 + 有持仓收益）。
            benchmark_returns: 基准日收益率序列。
            total_in_pos_count: 持仓指数总次数（主循环累积）。
            total_in_pos_positive: 持仓指数正收益总次数（主循环累积）。
            data_gap_days: 至少一个持仓资产受数据缺口影响的交易日数（B10）。
            annual_risk_free_rate_pct: 年化无风险利率（%），默认 0（历史口径）。
        """
        if not accumulator.daily_returns:
            return BacktestMetrics(
                cumulative_return_pct=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                win_rate_pct=0.0,
                signal_accuracy_pct=0.0,
                total_trading_days=0,
                active_days=0,
            ).model_dump()

        daily_rets = accumulator.daily_returns
        active_rets = accumulator.active_returns

        # 专业指标
        perf = compute_performance_metrics(
            daily_rets,
            benchmark_returns=benchmark_returns if benchmark_returns else None,
            active_returns=active_rets,
            annual_risk_free_rate_pct=annual_risk_free_rate_pct,
        )

        # 信号准确率（从主循环累积的内存计数器获取，无需 DB 查询）
        signal_accuracy_pct = 0.0
        if total_in_pos_count > 0:
            signal_accuracy_pct = round(total_in_pos_positive / total_in_pos_count * 100, 2)

        # 基准对比
        benchmark_return_pct = None
        excess_return_pct = None
        annualized_excess_return_pct = None
        benchmark_up_days_pct = None
        if benchmark_returns:
            bench_cumulative = 1.0
            for r in benchmark_returns:
                bench_cumulative *= 1 + r / 100
            benchmark_return_pct = round((bench_cumulative - 1) * 100, 2)
            excess_return_pct = round(perf.total_return_pct - benchmark_return_pct, 2)
            # 年化口径超额（毛口径）：与 stability.net_excess_return_pct（扣成本）
            # 区分；两者若都叫"超额"会让"累计差"被误读成年化差
            bench_annualized = compute_performance_metrics(benchmark_returns).annualized_return_pct
            annualized_excess_return_pct = round(
                perf.annualized_return_pct - bench_annualized, 2
            )
            # 基准上涨日占比：signal_accuracy_pct 的市场基准率参照
            up_days = sum(1 for r in benchmark_returns if r > 0)
            benchmark_up_days_pct = round(up_days / len(benchmark_returns) * 100, 2)

        metrics = BacktestMetrics(
            cumulative_return_pct=perf.total_return_pct,
            max_drawdown_pct=perf.max_drawdown_pct,
            sharpe_ratio=perf.sharpe_ratio,
            win_rate_pct=perf.win_rate_pct,
            signal_accuracy_pct=signal_accuracy_pct,
            total_trading_days=len(accumulator.daily_returns),
            active_days=len(active_rets),
            annualized_return_pct=perf.annualized_return_pct,
            sortino_ratio=perf.sortino_ratio,
            calmar_ratio=perf.calmar_ratio,
            max_drawdown_days=perf.max_drawdown_days,
            profit_loss_ratio=perf.profit_loss_ratio,
            alpha=perf.alpha,
            beta=perf.beta,
            information_ratio=perf.information_ratio,
            benchmark_return_pct=benchmark_return_pct,
            excess_return_pct=excess_return_pct,
            annualized_excess_return_pct=annualized_excess_return_pct,
            benchmark_up_days_pct=benchmark_up_days_pct,
            data_gap_days=data_gap_days,
        ).model_dump()
        # NaN/Inf 指标转 None，防止 PostgreSQL JSON 列写入失败
        # （如零波动 Sharpe、负累计年化开方等产生 NaN 的场景）
        return {k: _sanitize_metric_value(v) for k, v in metrics.items()}

    # ── 辅助方法 ───────────────────────────────────────────────────────────

    def _resolve_index_universe(
        self, universe_filter: dict[str, Any], start_date: date
    ) -> list[dict[str, Any]]:
        """根据 universe_filter 查询回测指数列表（point-in-time 口径）。

        标的池按"回测区间起点仍在存续"解析（``find_for_period``）：
        当前活跃指数 + 退市日晚于区间起点的指数一并纳入，消除"只回测活到今天的
        资产"的幸存者偏差；退市日未知（NULL）的历史行也纳入，交给逐日行情缺口
        判定实际可交易区间。读取走仓库，过滤走领域纯函数。

        结果字典额外带 ``is_active`` 标记，供主循环产出
        ``UNIVERSE_INCLUDES_DELISTED`` 提示（引擎只读取 index_code 等已知键）。

        Args:
            universe_filter: {"mode": "all"} 或 {"mode": "subset", "index_codes": [...]}。
            start_date: 回测区间起始日期（含）。

        Returns:
            universe 字典列表（index_code/name_cn/category/is_active）。
        """
        repo = BenchmarkIndexRepository(self._db)
        rows = repo.find_for_period(start_date)
        rows = filter_universe_rows(rows, universe_filter)
        items = build_universe_items(rows)
        active_codes = {row.index_code for row in repo.find_active()}
        for item in items:
            item["is_active"] = item["index_code"] in active_codes
        return items

    def _get_index_trading_dates(
        self, start: date, end: date, index_codes: list[str]
    ) -> list[date]:
        """从 index_daily_bar 中提取区间内的交易日列表（读取走仓库）。"""
        return self._index_bar_repo.find_trading_dates(start, end, index_codes)

    def _filter_non_trading_dates(
        self, start: date, end: date, trading_dates: list[date]
    ) -> tuple[list[date], list[date]]:
        """剔除"行情里有数据、日历判定为非交易日"的日期（F-7）。

        上游数据源偶尔把上一交易日的收盘价打在假期日期上（实测 2018-06-18
        端午节有 7 个指数日线），旧实现直接把它当交易日，于是回测多出一个
        假调仓日、一段假收益，还会连带一批 DATA_GAP / BENCHMARK_MISSING 警告。

        日历解析失败时**保持原有行为**（不静默改变历史结果口径），
        并通过 ``_non_trading_bar_policy=skipped_no_calendar`` 留痕。

        Args:
            start: 回测起始日期。
            end: 回测截止日期。
            trading_dates: 由行情推导出的交易日列表。

        Returns:
            (过滤后的交易日列表, 被剔除的日期列表)。
        """
        if not trading_dates:
            return trading_dates, []
        try:
            calendar, _ = resolve_trading_calendar(self._db, required_range=(start, end))
        except TradingCalendarUnavailableError:
            logger.warning(
                "[backtest] 交易日历不可用，跳过非交易日行情过滤（保持原口径）: %s~%s",
                start,
                end,
            )
            return trading_dates, []
        filtered = [d for d in trading_dates if calendar.is_trading_day(d)]
        dropped = [d for d in trading_dates if d not in set(filtered)]
        if dropped:
            logger.warning(
                "[backtest] 行情包含非交易日数据，已从回测交易日中剔除: %s", dropped
            )
        return filtered, dropped

    def _get_lookback_days(self) -> int:
        """按因子注册表推导回测回望自然日数，与实时模式口径一致。

        回望窗口取注册表所有因子 lookback_days 的最大值，避免长周期因子
        （return_120d / ma_60d / 估值百分位 / ERP 百分位等）在回测前段
        因回望不足而全部为 None，导致前段结果失真。

        Returns:
            最大回望自然日数。
        """
        return max_lookback_days(self._registry)

    def _load_all_index_bars(
        self, trading_dates: list[date], index_codes: list[str]
    ) -> dict[tuple[str, date], Any]:
        """批量加载回测区间及回望窗口的指数行情数据。

        回望窗口由注册表最大因子 lookback_days 推导（默认兜底 90 天），
        确保回测首日即可为长周期因子提供足够的行情历史。
        """
        if not trading_dates:
            return {}
        lookback_start = trading_dates[0] - timedelta(days=self._get_lookback_days())
        return self._index_bar_repo.find_all_date_range(
            lookback_start, trading_dates[-1], index_codes=index_codes
        )

    def _load_all_valuation(
        self, trading_dates: list[date], index_codes: list[str]
    ) -> dict[tuple[str, date], Any]:
        """批量加载指数估值数据（含回望窗口），按 index_codes 过滤。

        与行情数据使用相同回望窗口，供 erp_percentile / pe_percentile 等
        需要历史分布计算的因子在回测首日即可取到完整历史。
        """
        if not trading_dates:
            return {}
        lookback_start = trading_dates[0] - timedelta(days=self._get_lookback_days())
        return IndexValuationRepository(self._db).find_range(
            lookback_start, trading_dates[-1], index_codes
        )

    def _load_all_macro(self) -> dict[str, dict[str, float]]:
        """加载全部宏观指标数据（LPR 等），用于因子计算。

        Returns:
            key=indicator_code, value={period: value} 的字典。
        """
        return MacroIndicatorRepository(self._db).find_all_as_map()

    def _collect_missing_factor_warnings(
        self,
        precomputed: dict[date, dict[tuple[str, str], float | None]],
        trading_dates: list[date],
        index_codes: list[str],
        factor_ids: list[str],
    ) -> list[BacktestWarning]:
        """按因子聚合回测区间内的缺失情况，生成 MISSING_FACTOR 警告。

        计数口径（F-6）：``缺失天数`` 仍是"任一指数缺值即算一天"，但**必须
        同时给出按指数排序的明细**——历史实现只列字母序前 5 个指数，导致
        "只有一只后上市指数缺数据"被读成"整个资产池数据损坏"。

        Args:
            precomputed: 预计算因子值，date → (index_code, factor_id) → 数值。
            trading_dates: 回测交易日列表。
            index_codes: 参与因子计算的指数代码列表（含择时代理指数）。
            factor_ids: 策略实际引用的因子 ID 列表。

        Returns:
            结构化警告列表，最多 5 条（按因子），避免刷屏。
        """
        warnings: list[BacktestWarning] = []
        for factor_id in factor_ids:
            missing_days = 0
            missing_days_by_code: dict[str, int] = {}
            first_missing: date | None = None
            last_missing: date | None = None
            for trade_date in trading_dates:
                day_values = precomputed.get(trade_date, {})
                missing_codes = [
                    code for code in index_codes if day_values.get((code, factor_id)) is None
                ]
                if missing_codes:
                    missing_days += 1
                    for code in missing_codes:
                        missing_days_by_code[code] = missing_days_by_code.get(code, 0) + 1
                    if first_missing is None:
                        first_missing = trade_date
                    last_missing = trade_date
            if missing_days > 0:
                ranked = sorted(
                    missing_days_by_code.items(), key=lambda item: (-item[1], item[0])
                )
                detail = "、".join(f"{code} {days} 天" for code, days in ranked[:5])
                if len(ranked) > 5:
                    detail += f" 等 {len(ranked)} 个指数"
                warnings.append(
                    BacktestWarning(
                        level="warning",
                        code="MISSING_FACTOR",
                        message=(
                            f"因子 {factor_id} 在 {missing_days} 个交易日存在缺失"
                            f"（{first_missing}~{last_missing}）；"
                            f"按缺失天数排序：{detail}"
                        ),
                    )
                )
        return warnings[:5]

    def _collect_data_gap_warnings(
        self,
        trading_dates: list[date],
        index_codes: list[str],
        all_bars: dict,
        execution_model: str = "t_plus_1_open",
        data_quality_mode: str = "warn",
    ) -> list[BacktestWarning]:
        """按指数统计回测区间内缺行情数据的交易日，生成 DATA_GAP 警告。

        兼容模式下提示实际可能影响收益的行情缺口；严格模式下，已由
        DATA_EXCLUDED 覆盖的开盘价缺失不重复提示，避免把“已排除”误解为
        “仍参与收益计算”。

        Args:
            trading_dates: 回测交易日列表。
            index_codes: 回测标的指数代码列表。
            all_bars: 预加载行情，key=(index_code, trade_date)。
            execution_model: 执行模型，决定是否检查开盘价。
            data_quality_mode: 数据质量模式，严格模式不重复报告已排除的开盘价缺失。

        Returns:
            结构化警告列表，最多 5 条（按指数）。
        """
        warnings: list[BacktestWarning] = []
        for code in index_codes:
            missing_days: list[date] = []
            open_missing_days: list[date] = []
            for d in trading_dates:
                bar = all_bars.get((code, d))
                if bar is None or _price_invalid(getattr(bar, "close_price", None)):
                    # 整日缺行情或收盘价不可用：该日收益无法按收盘口径计算
                    missing_days.append(d)
                    continue
                if (
                    execution_model == "t_plus_1_open"
                    and data_quality_mode != "strict"
                    and _price_invalid(getattr(bar, "open_price", None))
                ):
                    # bar 存在但开盘价缺失：T+1 开盘执行模型的隔夜/日内段受影响
                    open_missing_days.append(d)
            parts: list[str] = []
            if missing_days:
                parts.append(
                    f"缺行情数据 {len(missing_days)} 个交易日"
                    f"（{missing_days[0]}~{missing_days[-1]}）"
                )
            if open_missing_days:
                parts.append(
                    f"开盘价缺失 {len(open_missing_days)} 个交易日"
                    f"（{open_missing_days[0]}~{open_missing_days[-1]}，影响 T+1 开盘执行日）"
                )
            if parts:
                warnings.append(
                    BacktestWarning(
                        level="warning",
                        code="DATA_GAP",
                        message=(
                            f"指数 {code} 回测区间内{'、'.join(parts)}，"
                            "相关日期的因子与收益按缺失处理"
                        ),
                        index_code=code,
                    )
                )
        return warnings[:5]

    def _estimate_warmup_trading_days(
        self,
        precomputed: dict[date, dict[tuple[str, str], float | None]],
        trading_dates: list[date],
        index_codes: list[str],
        factor_ids: list[str],
        first_bar_by_code: dict[str, date] | None = None,
    ) -> int:
        """估算回测前段因子数据不足的预热交易日数。

        口径（F-6）：对每个 (因子, 指数) 组合，从**该指数自己的首根 K 线**
        开始数"该因子还没有有效值"的天数，取最大值作为预热期——这才是
        真正影响策略的可交易起点。

        历史实现要求"全部指数都有有效值"才算预热结束，单只后上市指数
        （如 000688 行情自 2020 年才有）会把整个回测的前 1038 个交易日
        （约 4.3 年）判成预热期，读起来像"前四年结果无效"，实际只是
        一只指数缺数据。指数上市前的空缺属于数据缺口（DATA_GAP），
        不在这里计数。

        Args:
            precomputed: 预计算因子值，date → (index_code, factor_id) → 数值。
            trading_dates: 回测交易日列表。
            index_codes: 参与因子计算的指数代码列表（含择时代理指数）。
            factor_ids: 策略实际引用的因子 ID 列表。
            first_bar_by_code: 指数 → 回测区间内首根 K 线日期；提供后每个指数
                只从自己的首根 K 线起算（None 时等价于从区间首日起算）。

        Returns:
            前 N 个交易日中因子数据不足的天数；无预热期时返回 0。
        """
        if not factor_ids or not trading_dates:
            return 0
        warmup = 0
        for factor_id in factor_ids:
            for code in index_codes:
                start_index = 0
                if first_bar_by_code is not None:
                    first_bar = first_bar_by_code.get(code)
                    if first_bar is None:
                        # 该指数在窗口内完全没有行情，整体缺失由 DATA_GAP 报告
                        continue
                    start_index = bisect_left(trading_dates, first_bar)
                for i in range(start_index, len(trading_dates)):
                    if precomputed.get(trading_dates[i], {}).get((code, factor_id)) is not None:
                        # 只计"该指数自己序列开头"的缺口，指数上市前的空缺不算
                        warmup = max(warmup, i - start_index)
                        break
                else:
                    # 该指数在窗口内始终没有该因子值：结构性缺失，
                    # 由 MISSING_FACTOR / 数据质量检查负责，不算预热
                    continue
        return warmup

    def _get_index_return(
        self,
        index_code: str,
        trade_date: date,
        next_date: date,
        all_bars: dict,
    ) -> float | None:
        """获取指数 T+1 日收益率（%）（委托领域函数）。"""
        return get_index_return(index_code, trade_date, next_date, all_bars)

    # ── Schema 转换辅助 ────────────────────────────────────────────────────

    def _row_to_summary(
        self,
        row: BacktestRunModel,
        queue_info: dict[str, Any] | None = None,
        net_metrics: dict[str, Any] | None = None,
    ) -> BacktestSummary:
        """将 ORM 行转换为 BacktestSummary。

        Args:
            row: 回测 ORM 行。
            queue_info: 可选的队列观测信息（排队位置/等待与执行耗时，B3）。
            net_metrics: 可选的净口径指标覆盖（F-12，列表路径现算）
        """
        metrics = None
        if row.metrics:
            try:
                metrics = BacktestMetrics(**{**row.metrics, **(net_metrics or {})})
            except Exception:
                try:
                    metrics = BacktestMetrics(**row.metrics)
                except Exception:
                    pass
        info = queue_info or {}
        return BacktestSummary(
            backtest_id=row.backtest_id,
            strategy_id=row.strategy_id,
            start_date=row.start_date,
            end_date=row.end_date,
            status=row.status,
            metrics=metrics,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error_message=row.error_message,
            progress=getattr(row, "progress", 0) or 0,
            purpose=getattr(row, "purpose", None) or PURPOSE_RESEARCH,
            job_status=info.get("job_status"),
            queued_seconds=info.get("queued_seconds"),
            elapsed_seconds=info.get("elapsed_seconds"),
            queue_position=info.get("queue_position"),
            job_priority=info.get("job_priority"),
            batch_id=info.get("batch_id"),
        )

    def _row_to_detail(
        self, row: BacktestRunModel, queue_info: dict[str, Any] | None = None
    ) -> BacktestDetail:
        """将 ORM 行转换为 BacktestDetail（含结构化警告）。"""
        summary = self._row_to_summary(row, queue_info)
        try:
            warnings = [BacktestWarning(**w) for w in (row.warnings or [])]
        except Exception:
            warnings = []
        return BacktestDetail(
            **summary.model_dump(),
            universe_filter=row.universe_filter or {"mode": "all"},
            params=row.params,
            warnings=warnings,
            config_snapshot=row.config_snapshot,
            config_hash=row.config_hash,
            data_cutoff_date=getattr(row, "data_cutoff_date", None),
            optimization_id=getattr(row, "optimization_id", None),
            purpose_reason=getattr(row, "purpose_reason", None),
        )

    # ── 策略对比回测 ────────────────────────────────────────────────────

    def create_comparison(self, req: BacktestComparisonCreateRequest) -> BacktestComparisonSummary:
        """创建策略对比回测，生成两个子回测和一个对比记录。

        两个子回测共享同一时间区间，但标的范围各自独立。
        若策略自身已配置 index_codes，则强制使用策略配置（不可修改）；
        否则使用请求中对应策略的 a_index_codes / b_index_codes。

        Args:
            req: 对比回测创建请求。

        Returns:
            BacktestComparisonSummary 对比摘要。

        Raises:
            ValueError: 策略不存在或两策略相同。
        """
        comparison_id = str(uuid4())
        now = utcnow_aware()

        # 校验两个策略不同
        if req.strategy_a_id == req.strategy_b_id:
            raise ValueError("请选择两个不同的策略进行对比")

        # 校验两个策略都存在且配置了 portfolio
        config_svc = StrategyConfigService(self._db)
        for sid in [req.strategy_a_id, req.strategy_b_id]:
            cfg = config_svc.get_parsed_config(sid)
            if cfg is None:
                raise ValueError(f"策略 {sid} 配置不存在")
        # 创建子回测 A（create_backtest 内部会处理策略自身的 index_codes 限定）
        a_mode = "subset" if req.a_index_codes else "all"
        bt_a_summary = self.create_backtest(
            BacktestCreateRequest(
                strategy_id=req.strategy_a_id,
                start_date=req.start_date,
                end_date=req.end_date,
                universe_mode=a_mode,  # type: ignore[arg-type]
                index_codes=req.a_index_codes,
                enable_benchmark=req.enable_benchmark,
                benchmark_index_code=req.benchmark_index_code,
                execution_model=req.execution_model,
                data_quality_mode=req.data_quality_mode,
            )
        )

        # 创建子回测 B
        b_mode = "subset" if req.b_index_codes else "all"
        bt_b_summary = self.create_backtest(
            BacktestCreateRequest(
                strategy_id=req.strategy_b_id,
                start_date=req.start_date,
                end_date=req.end_date,
                universe_mode=b_mode,  # type: ignore[arg-type]
                index_codes=req.b_index_codes,
                enable_benchmark=req.enable_benchmark,
                benchmark_index_code=req.benchmark_index_code,
                execution_model=req.execution_model,
                data_quality_mode=req.data_quality_mode,
            )
        )

        # 创建对比记录
        comp_row = BacktestComparisonModel(
            comparison_id=comparison_id,
            name=req.name,
            strategy_a_id=req.strategy_a_id,
            strategy_b_id=req.strategy_b_id,
            backtest_a_id=bt_a_summary.backtest_id,
            backtest_b_id=bt_b_summary.backtest_id,
            start_date=req.start_date,
            end_date=req.end_date,
            status="pending",
            params={
                "_enable_benchmark": req.enable_benchmark,
                "_benchmark_index_code": req.benchmark_index_code,
                "_execution_model": req.execution_model,
                "_data_quality_mode": req.data_quality_mode,
                "a_index_codes": req.a_index_codes,
                "b_index_codes": req.b_index_codes,
            },
            created_at=now,
        )
        self._db.add(comp_row)
        self._db.commit()

        return self._comp_row_to_summary(comp_row)

    def list_comparisons(
        self, offset: int = 0, limit: int = 50
    ) -> tuple[list[BacktestComparisonSummary], int]:
        """分页返回对比回测列表，按创建时间倒序。"""
        try:
            rows, total = self._backtest_repo.find_all_comparisons(offset=offset, limit=limit)
            items = [self._comp_row_to_summary(r) for r in rows]
            return items, total
        except Exception:
            logger.warning("list_comparisons DB query failed", exc_info=True)
            return [], 0

    def get_comparison(self, comparison_id: str) -> BacktestComparisonDetail | None:
        """返回对比回测详情，含两个子回测的完整信息。"""
        try:
            comp = self._backtest_repo.find_comparison_by_id(comparison_id)
            if comp is None:
                return None

            bt_a = self._backtest_repo.find_by_id(comp.backtest_a_id)
            bt_b = self._backtest_repo.find_by_id(comp.backtest_b_id)

            return BacktestComparisonDetail(
                **self._comp_row_to_summary(comp).model_dump(),
                backtest_a=self._row_to_detail(bt_a) if bt_a else None,
                backtest_b=self._row_to_detail(bt_b) if bt_b else None,
                params=comp.params,
            )
        except Exception:
            logger.warning("get_comparison DB query failed", exc_info=True)
            return None

    def get_comparison_daily(self, comparison_id: str) -> ComparisonDailyResponse:
        """返回两个策略的每日组合绩效摘要（仅图表渲染所需字段），用于叠加图表渲染。"""
        try:
            comp = self._backtest_repo.find_comparison_by_id(comparison_id)
            if comp is None:
                return ComparisonDailyResponse(a_daily=[], b_daily=[])

            def _to_point(daily: BacktestDailyResult) -> ComparisonDailyPoint:
                return ComparisonDailyPoint(
                    trade_date=daily.trade_date,
                    portfolio_return=daily.portfolio_return,
                    cumulative_return=daily.cumulative_return,
                    drawdown=daily.drawdown,
                )

            return ComparisonDailyResponse(
                a_daily=[_to_point(d) for d in self.get_daily_results(comp.backtest_a_id)],
                b_daily=[_to_point(d) for d in self.get_daily_results(comp.backtest_b_id)],
            )
        except Exception:
            logger.warning("get_comparison_daily DB query failed", exc_info=True)
            return ComparisonDailyResponse(a_daily=[], b_daily=[])

    def launch_comparison(self, comparison_id: str) -> tuple[str, str] | None:
        """标记对比回测为运行中，返回两个子回测 ID。

        由 comparison 任务处理器调用：对比任务不再等待子回测完成，
        而是由子回测完成后的 finalize_comparison_if_ready 触发汇总，
        避免嵌套线程池与父任务空等占用 worker。

        Args:
            comparison_id: 对比回测 ID。

        Returns:
            (backtest_a_id, backtest_b_id)；对比记录不存在时返回 None。
        """
        comp = self._backtest_repo.find_comparison_by_id(comparison_id)
        if comp is None:
            logger.error("launch_comparison: comparison_id %s not found", comparison_id)
            return None

        comp.status = "running"
        comp.started_at = utcnow_aware()
        self._db.commit()
        return comp.backtest_a_id, comp.backtest_b_id

    def finalize_comparison_if_ready(self, comparison_id: str) -> None:
        """子回测完成后尝试汇总对比结果，两个子回测均终态时执行。

        单个子回测完成即调用本方法；若另一个子回测仍在执行则直接返回，
        待其完成后再次触发。汇总逻辑与旧的 run_comparison 保持一致。

        Args:
            comparison_id: 对比回测 ID。
        """
        comp = self._backtest_repo.find_comparison_by_id(comparison_id)
        if comp is None:
            return
        bt_a = self._backtest_repo.find_by_id(comp.backtest_a_id)
        bt_b = self._backtest_repo.find_by_id(comp.backtest_b_id)
        if bt_a is None or bt_b is None:
            return

        statuses = [bt_a.status, bt_b.status]
        if any(s not in ("success", "failed") for s in statuses):
            # 仍有子回测未完成，等待另一个子回测完成时再次触发
            return

        errors: dict[str, str] = {}
        if bt_a.status == "failed":
            errors["a"] = bt_a.error_message or "策略A回测失败"
        if bt_b.status == "failed":
            errors["b"] = bt_b.error_message or "策略B回测失败"

        if len(errors) == 2:
            self._backtest_repo.mark_comparison_failed(
                comparison_id,
                f"策略A: {errors['a']}; 策略B: {errors['b']}",
            )
        elif len(errors) == 1:
            self._backtest_repo.mark_comparison_partial(comparison_id, str(errors))
        else:
            self._compute_and_save_comparison_metrics(comparison_id)

    def _compute_and_save_comparison_metrics(self, comparison_id: str) -> None:
        """从两个子回测的 metrics JSON 汇总对比指标。

        读取两个 backtest_run 的 metrics 字段，
        逐指标提取 A/B 值并计算差值，写入 comparison_metrics。
        """
        comp = self._backtest_repo.find_comparison_by_id(comparison_id)
        if comp is None:
            return

        bt_a = self._backtest_repo.find_by_id(comp.backtest_a_id)
        bt_b = self._backtest_repo.find_by_id(comp.backtest_b_id)
        if bt_a is None or bt_b is None:
            return

        ma = bt_a.metrics or {}
        mb = bt_b.metrics or {}

        def _get(key: str, metrics: dict, default: Any = 0.0) -> Any:
            return metrics.get(key, default)

        # 读取各项指标并计算差值。
        # 指标值经 _sanitize_metric_value 可能为 None，复制项统一回退 0.0
        # 保证 ComparisonMetrics 必需 float 字段可解析；差值用 _safe_metric_diff 容错。
        a_cum = _get("cumulative_return_pct", ma) or 0.0
        b_cum = _get("cumulative_return_pct", mb) or 0.0
        a_ann = _get("annualized_return_pct", ma) or 0.0
        b_ann = _get("annualized_return_pct", mb) or 0.0
        a_dd = _get("max_drawdown_pct", ma) or 0.0
        b_dd = _get("max_drawdown_pct", mb) or 0.0
        a_sharpe = _get("sharpe_ratio", ma) or 0.0
        b_sharpe = _get("sharpe_ratio", mb) or 0.0
        a_sortino = _get("sortino_ratio", ma) or 0.0
        b_sortino = _get("sortino_ratio", mb) or 0.0
        a_calmar = _get("calmar_ratio", ma) or 0.0
        b_calmar = _get("calmar_ratio", mb) or 0.0
        a_win = _get("win_rate_pct", ma) or 0.0
        b_win = _get("win_rate_pct", mb) or 0.0
        a_sig = _get("signal_accuracy_pct", ma) or 0.0
        b_sig = _get("signal_accuracy_pct", mb) or 0.0
        a_days = _get("total_trading_days", ma, 0)
        b_days = _get("total_trading_days", mb, 0)
        a_active = _get("active_days", ma, 0)
        b_active = _get("active_days", mb, 0)

        comparison_metrics = {
            "a_cumulative_return_pct": a_cum,
            "b_cumulative_return_pct": b_cum,
            "a_annualized_return_pct": a_ann,
            "b_annualized_return_pct": b_ann,
            "a_max_drawdown_pct": a_dd,
            "b_max_drawdown_pct": b_dd,
            "a_sharpe_ratio": a_sharpe,
            "b_sharpe_ratio": b_sharpe,
            "a_sortino_ratio": a_sortino,
            "b_sortino_ratio": b_sortino,
            "a_calmar_ratio": a_calmar,
            "b_calmar_ratio": b_calmar,
            "a_win_rate_pct": a_win,
            "b_win_rate_pct": b_win,
            "a_signal_accuracy_pct": a_sig,
            "b_signal_accuracy_pct": b_sig,
            "a_total_trading_days": a_days,
            "b_total_trading_days": b_days,
            "a_active_days": a_active,
            "b_active_days": b_active,
            # 差值
            "cumulative_return_diff_pct": _safe_metric_diff(a_cum, b_cum),
            "annualized_return_diff_pct": _safe_metric_diff(a_ann, b_ann),
            "max_drawdown_diff_pct": _safe_metric_diff(a_dd, b_dd),
            "sharpe_diff": _safe_metric_diff(a_sharpe, b_sharpe, 4),
            "sortino_diff": _safe_metric_diff(a_sortino, b_sortino, 4),
            "calmar_diff": _safe_metric_diff(a_calmar, b_calmar, 4),
            "win_rate_diff_pct": _safe_metric_diff(a_win, b_win),
            "signal_accuracy_diff_pct": _safe_metric_diff(a_sig, b_sig),
            # 基准对比
            "a_benchmark_return_pct": _get("benchmark_return_pct", ma),
            "b_benchmark_return_pct": _get("benchmark_return_pct", mb),
            "a_excess_return_pct": _get("excess_return_pct", ma),
            "b_excess_return_pct": _get("excess_return_pct", mb),
            "a_alpha": _get("alpha", ma),
            "b_alpha": _get("alpha", mb),
            "a_beta": _get("beta", ma),
            "b_beta": _get("beta", mb),
            "a_information_ratio": _get("information_ratio", ma),
            "b_information_ratio": _get("information_ratio", mb),
        }

        self._backtest_repo.mark_comparison_success(comparison_id, comparison_metrics)

    def _comp_row_to_summary(self, row: BacktestComparisonModel) -> BacktestComparisonSummary:
        """将 ORM 行转换为 BacktestComparisonSummary。"""
        comp_metrics = None
        if row.comparison_metrics:
            try:
                comp_metrics = ComparisonMetrics(**row.comparison_metrics)
            except Exception:
                pass
        return BacktestComparisonSummary(
            comparison_id=row.comparison_id,
            name=row.name,
            strategy_a_id=row.strategy_a_id,
            strategy_b_id=row.strategy_b_id,
            backtest_a_id=row.backtest_a_id,
            backtest_b_id=row.backtest_b_id,
            start_date=row.start_date,
            end_date=row.end_date,
            status=row.status,
            comparison_metrics=comp_metrics,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error_message=row.error_message,
            progress=row.progress or 0,
        )


def _period_boundaries() -> PeriodBoundaries:
    """从系统配置读取研究期/验证期边界。

    Returns:
        PeriodBoundaries 实例（研究期 2016-01-01 ~ 2025-12-31，验证期自 2026-01-01 起）。
    """
    settings = get_settings()
    return PeriodBoundaries(
        research_start=date.fromisoformat(settings.research_period_start),
        research_end=date.fromisoformat(settings.research_period_end),
        validation_start=date.fromisoformat(settings.validation_period_start),
    )


def _merge_net_metrics(
    metrics: BacktestMetrics | None, stability: BacktestStability
) -> BacktestMetrics | None:
    """把净成本口径指标并入回测汇总指标。

    Args:
        metrics: 原始（毛口径）回测指标，可为 None。
        stability: 读取路径计算出的稳健性指标（含净口径结果）。

    Returns:
        补齐净口径字段后的指标对象；原始指标缺失时返回 None。
    """
    if metrics is None:
        return None
    return metrics.model_copy(
        update={
            "net_annualized_return_pct": stability.net_annualized_return_pct,
            "net_sharpe_ratio": stability.net_sharpe_ratio,
            "net_excess_return_pct": stability.net_excess_return_pct,
            "annualized_turnover": stability.annualized_turnover,
        }
    )
