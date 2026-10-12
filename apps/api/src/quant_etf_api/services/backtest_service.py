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
    sanitize_metric_value as _sanitize_metric_value,
)
from quant_etf_api.domain.common.signal_level import determine_signal_level
from quant_etf_api.domain.common.trading_calendar import TradingCalendarUnavailableError
from quant_etf_api.domain.portfolio.accounting import BacktestDayAccumulator
from quant_etf_api.domain.portfolio.benchmark import compute_buy_hold_benchmark
from quant_etf_api.domain.portfolio.execution import (
    ACCOUNTING_MODEL_DRIFT,
    ACCOUNTING_MODEL_LEGACY,
    BacktestExecutor,
    forward_fill_return_bars,
)
from quant_etf_api.domain.portfolio.returns import get_index_return
from quant_etf_api.domain.portfolio.scaling import compose_two_leg_positions
from quant_etf_api.domain.portfolio.turnover import (
    TURNOVER_MODEL_DELTA_W,
    TURNOVER_MODEL_LEGACY,
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
from quant_etf_api.engine.config import (
    StrategyConfig,
    iter_factor_refs,
)
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.engine.rebalance import DefaultRebalanceScheduler
from quant_etf_api.factors.catalog import OHLC_DEPENDENT_TEMPLATE_IDS, FactorTemplateRegistry
from quant_etf_api.factors.compute import FactorMatrix
from quant_etf_api.infra.db.models.core import (
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
    CostLadderEntry,
    DanglingReferenceItem,
    DanglingReferenceReport,
    ValidationUsageItem,
    ValidationUsageResponse,
)
from quant_etf_api.services.backtest_diagnostics import (
    RunDiagnostics,
    build_candidate_pool_timeline,
    collect_compute_failure_warnings,
    collect_data_gap_warnings,
    collect_missing_factor_warnings,
    collect_run_warnings,
    estimate_warmup_trading_days,
    first_bar_by_code,
)
from quant_etf_api.services.backtest_reference_service import (
    HOLDER_OPTIMIZATION_FOLDS,
    HOLDER_ROBUSTNESS_VARIANTS,
    BacktestReferenceService,
)
from quant_etf_api.services.period_config import load_period_boundaries
from quant_etf_api.services.strategy_config_service import (
    StrategyConfigService,
    compute_config_hash,
)

logger = logging.getLogger(__name__)


@dataclass
class BacktestRunCaches:
    """同一批变体共享的行情快照与因子缓存（D-1 研究批量评估）。

    研究批量评估会在同一窗口上跑几十个变体（参数邻域 / 消融 / 池扰动）。
    这些变体的行情、估值与宏观数据完全相同；因子值只在"所需因子的计算实例"
    相同时才相同。默认每次 ``_run_backtest_loop`` 都会重新查库并重算，
    批量场景下这部分开销会乘以变体数；把两者按窗口键缓存起来即可把
    "批量探索"从数小时压到分钟级，同时因为复用同一条执行路径，
    指标口径与平台回测严格一致。

    Attributes:
        data: 窗口键 → ``_prepare_backtest_data`` 结果元组。
        factors: (窗口键, 计算实例指纹, 参与计算的指数集合) → 预计算因子矩阵
            （逐日因子值 + 计算失败的引用）。
            指纹来自 :meth:`FactorProvider.instance_fingerprint`（模板 ID +
            规范化参数 + 模板版本），**不能只用因子引用名**——参数扰动下
            引用名不变而因子值不同。
    """

    data: dict[str, tuple] = field(default_factory=dict)
    factors: dict[tuple[str, tuple[tuple[str, str, str], ...], str], FactorMatrix] = field(
        default_factory=dict
    )


def _window_cache_key(row: BacktestRunModel) -> str:
    """构造窗口缓存键（区间 + 标的过滤条件）。

    Args:
        row: 回测 ORM 行（或研究批量评估构造的临时行）。

    Returns:
        形如 ``2016-01-01~2018-06-30|{"mode": "subset", ...}`` 的键。
    """
    universe_filter = json.dumps(row.universe_filter or {}, sort_keys=True, ensure_ascii=False)
    return f"{row.start_date.isoformat()}~{row.end_date.isoformat()}|{universe_filter}"


def _round_weights(positions: dict[str, float]) -> dict[str, float]:
    """把持仓权重取整到 4 位小数用于落库与展示，取整后为 0 的资产剔除。

    执行器内部保留全精度漂移权重（避免舍入误差在逐日漂移中累积），
    只在落库时取整。

    Args:
        positions: 持仓权重。

    Returns:
        取整后的持仓权重。
    """
    rounded = {code: round(weight, 4) for code, weight in sorted(positions.items())}
    return {code: weight for code, weight in rounded.items() if weight > 0}


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

        # 使用进程级单例模板注册表，避免每次请求重建
        from quant_etf_api.factors.catalog import get_factor_template_registry

        self._registry: FactorTemplateRegistry = get_factor_template_registry()
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
        validate_backtest_period(load_period_boundaries(), req.purpose, req.start_date, req.end_date)

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
            # 写库失败必须上抛：吞掉异常会让调用方拿到一条不存在的 pending 回测
            # （随后入队、轮询都找不到记录），并错误地留下验证期消费痕迹
            self._db.rollback()
            logger.exception("create_backtest DB insert failed: backtest_id=%s", backtest_id)
            raise

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
            detail = self.row_to_detail(row, self._queue_info([backtest_id]).get(backtest_id))
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
            accounting_model=params.get("_accounting_model") or ACCOUNTING_MODEL_LEGACY,
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
        boundaries = load_period_boundaries()
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
            # 旧回测行无快照时回退到实时配置。快照存在但解析失败时必须报错：
            # 静默改用当前配置会让回测结果与其记录的配置哈希不一致
            config_svc = StrategyConfigService(self._db)
            config = None
            if getattr(row, "config_snapshot", None):
                config = config_svc.parse_snapshot(row.config_snapshot)
                if config is None:
                    raise ValueError(
                        f"回测 {backtest_id} 的配置快照解析失败，拒绝回退到当前配置"
                    )
            else:
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
        4. 逐决策日执行引擎管线，由 ``BacktestExecutor`` 结算持仓、收益与换手
           （毛收益，不扣除交易成本；记账口径见 ``domain.portfolio.execution``）
        5. 计算基准收益（如启用）
        6. 写入每日结果和指数结果
        7. 计算汇总绩效指标与结构化告警

        决策日为除最后一个交易日之外的全部交易日：第 i 行收益是
        [收盘 T_i, 收盘 T_{i+1}] 的实现收益，最后一个交易日没有下一交易日、不产生
        收益窗口，因此不落行（历史口径会落一行人为的 0 收益）。

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
            ValueError: 标的范围为空或区间内交易日不足 2 个时抛出。
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
        # 批量评估的行情快照被多个变体共享，而下面会就地补充代理指数、基准与
        # 全市场行情：先浅拷贝，避免一个变体的补充数据泄漏进其他变体的缓存
        all_bars = dict(all_bars)
        all_valuation = dict(all_valuation)
        # 非交易日行情过滤（F-7）：上游偶尔把行情打在假期日期上，直接当交易日
        # 会多出假调仓日与假收益；日历不可用时保持原行为并留痕
        trading_dates, non_trading_dates = self._filter_non_trading_dates(
            row.start_date, row.end_date, trading_dates
        )
        if len(trading_dates) < 2:
            raise ValueError(
                f"区间 {row.start_date} ~ {row.end_date} 内有效交易日不足 2 个，"
                "无法形成任何收益窗口"
            )
        # params 是普通 JSON 列，不追踪原地修改：必须在新 dict 上改完再整体赋值，
        # 否则口径元数据（预热期、记账口径等）不会写入数据库
        run_params = dict(row.params or {})
        if non_trading_dates:
            run_params["_non_trading_bar_dates"] = [d.isoformat() for d in non_trading_dates]

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
                    all_bars.update(self._load_all_index_bars(trading_dates, [proxy_code]))
                    all_valuation.update(self._load_all_valuation(trading_dates, [proxy_code]))

        # 预计算所有因子值（含择时代理指数）；批量评估时按
        # （窗口, 所需因子的计算实例, 参与计算的指数）复用，避免每个变体重算一遍。
        # 缓存键必须携带模板 ID + 规范化参数 + 模板版本：参数高原扫描只在参数上
        # 做扰动，引用名（别名）不变而因子值不同，只用引用名当键会让所有扰动变体
        # 命中基线那份因子值，表现为"改了参数但指标一字不差"。
        required_factor_ids = FactorProvider.collect_required_factor_ids(config)
        fingerprint = self._factor_provider.instance_fingerprint(config)
        factor_cache_key = (
            _window_cache_key(row),
            fingerprint
            if fingerprint
            else tuple((ref, ref, "") for ref in sorted(required_factor_ids)),
            ",".join(sorted(factor_index_codes)),
        )
        factor_matrix = caches.factors.get(factor_cache_key) if caches is not None else None
        if factor_matrix is None:
            factor_matrix = self._factor_provider.precompute_backtest_factors(
                config, trading_dates, factor_index_codes, all_bars, all_valuation, all_macro
            )
            if caches is not None:
                caches.factors[factor_cache_key] = factor_matrix
        precomputed = factor_matrix.values
        # 资产级"缺失即剔除"豁免市场级引用（与实时路径同一口径，由引擎统一执行）
        market_shape_refs = set(self._factor_provider.market_shape_refs(config))

        # 记录实际回望天数与因子预热期（"前 N 个交易日因子数据不足"提示），
        # 写入回测 params 元数据，随 checkpoint / mark_success 一并持久化
        lookback_days = self._get_lookback_days()
        warmup_trading_days = estimate_warmup_trading_days(
            precomputed,
            trading_dates,
            factor_index_codes,
            required_factor_ids,
            first_bar_by_code(all_bars),
        )
        run_params["_lookback_days"] = lookback_days
        run_params["_warmup_trading_days"] = warmup_trading_days
        # 基准口径标记：策略/基准逐日序列均按"决策日归因"落账
        # （T 日决策 → T+1 行情），历史数据一次性回填命令据此跳过新记录
        run_params["_benchmark_alignment"] = "decision_day"
        # 记账口径指纹：持仓漂移 + 调仓日两段复利 + 收益行情前向填充 + 无终端行，
        # 存量回测无该键（读取时按 constant_mix_v0 标注），两者指标不可直接比较
        run_params["_accounting_model"] = ACCOUNTING_MODEL_DRIFT
        row.params = run_params
        if warmup_trading_days > 0:
            logger.warning(
                "[backtest] 因子预热期: backtest_id=%s 前 %s 个交易日长周期因子可能数据不足",
                backtest_id,
                warmup_trading_days,
            )

        enable_benchmark = run_params.get("_enable_benchmark", True)
        benchmark_index_code = run_params.get("_benchmark_index_code", "000300")
        execution_model = run_params.get("_execution_model", "t_plus_1_open")
        data_quality_mode = run_params.get("_data_quality_mode", "warn")
        # 无风险利率（%）：默认 0（历史口径），预留从回测参数读取的通道
        risk_free_rate_pct = float(run_params.get("_annual_risk_free_rate_pct", 0.0) or 0.0)

        if enable_benchmark and benchmark_index_code not in index_codes:
            # 基准指数可能不在策略 universe 中，单独补充行情
            all_bars.update(self._load_all_index_bars(trading_dates, [benchmark_index_code]))
        # 收益结算专用的前向填充行情：缺口期间的涨跌在缺口后补回，而不是永久丢失；
        # 候选池、因子与缺口统计仍使用原始行情
        return_codes = list(index_codes)
        if enable_benchmark and benchmark_index_code not in return_codes:
            return_codes.append(benchmark_index_code)
        return_bars = forward_fill_return_bars(all_bars, return_codes, trading_dates)

        # 决策日：最后一个交易日没有下一交易日，不形成收益窗口
        decision_dates = trading_dates[:-1]
        # 基准日收益率序列（与决策日等长，第 i 个元素为 [收盘 T_i, 收盘 T_{i+1}]）
        benchmark_returns: list[float] = []
        if enable_benchmark:
            benchmark_returns = compute_buy_hold_benchmark(
                return_bars, benchmark_index_code, trading_dates
            )[: len(decision_dates)]

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
            execution_model,
            data_quality_mode,
            benchmark_index_code if enable_benchmark else "禁用",
        )

        # 不落库模式（D-1）下保留全部逐日结果，供净口径与稳健性指标现算
        memory_rows: list[BacktestDailyResultModel] = []
        # 指数级结果缓冲：按 checkpoint 批量 INSERT，避免逐行构造 ORM 对象
        index_buffer: list[dict[str, Any]] = []
        # 账户累积器：累计净值/回撤等账务状态由领域对象跟踪
        accumulator = BacktestDayAccumulator()
        # 执行器：维护漂移后的实际持仓，按执行模型结算收益与换手
        executor = BacktestExecutor(execution_model)
        # 数据缺口天数：至少一个持仓资产当日收益受缺失行情影响的交易日数（B10）
        data_gap_days = 0
        # 数据缺口剔除记录：两种数据质量口径共用同一候选池，因此剔除集合一致，
        # 仅提示详细程度不同（strict 逐指数、warn 汇总）
        exclusion_dates: dict[str, list[date]] = {}
        exclusion_reasons: dict[str, set[str]] = {}
        # 有效候选池逐日规模（C6）：用于还原"早期实际可交易池只有 N 个"这类事实
        pool_sizes: list[int] = []
        last_progress = 0
        total_dates = len(decision_dates)
        # 信号准确率累积器（替代 DB 查询）
        total_in_pos_count = 0
        total_in_pos_positive = 0
        # benchmark_index 无分类字段：不再伪造 category，排名展示留空
        cached_metadata = {code: {"name_cn": code} for code in index_codes}
        # 择时降级（择时因子全部缺失 → 按中性运行）的交易日，收尾时生成告警
        timing_degraded_dates: list[date] = []
        all_index_codes = set(index_codes)

        for i, trade_date in enumerate(decision_dates):
            # 协作取消检查点（B2）：取消标记有短 TTL 缓存，不会每个交易日都查库
            ensure_not_cancelled()
            next_date = trading_dates[i + 1]

            # 口径统一：候选池与 data_quality_mode 无关，两种模式都使用"当日可执行
            # 候选池"；行情可执行性在此判定，"决策所需因子缺失"的剔除由引擎按生效
            # 配置执行（与实时路径同一实现），引擎执行后再合并进剔除记录
            day_codes, day_exclusion_reasons = self._build_candidate_pool(
                config, trade_date, next_date, index_codes, all_bars, execution_model
            )
            context = self._context_builder.build(
                config,
                trade_date,
                index_codes=day_codes,
                all_bars=all_bars,
                precomputed_factors=precomputed.get(trade_date, {}),
                cached_universe=build_universe_items(
                    [{"index_code": c, "name_cn": c} for c in day_codes]
                ),
                cached_metadata={code: cached_metadata[code] for code in day_codes},
                market_shape_refs=market_shape_refs,
            )

            # 两条腿独立到期：选股腿改成分，风险腿仅按择时等比缩放总仓位。
            # 两腿同频（旧配置升级后的默认形态）时，两腿同日命中，合成为改造前的
            # "新成分 + 择时目标仓位"。
            legs = self._select_legs(config, trade_date)

            # 执行引擎（回测模式跳过详细的 StrategyResult 与调试详情构建以提升性能）
            result = self._engine.run(config, context, include_details=False)

            # 合并引擎的因子缺失剔除，得到当日最终有效候选池
            for excluded_code, reason in result.excluded.items():
                day_exclusion_reasons.setdefault(excluded_code, set()).add(reason)
            eligible_codes = [code for code in day_codes if code not in result.excluded]
            for excluded_code in all_index_codes - set(eligible_codes):
                exclusion_dates.setdefault(excluded_code, []).append(trade_date)
                exclusion_reasons.setdefault(excluded_code, set()).update(
                    day_exclusion_reasons.get(excluded_code, {"DATA_EXCLUDED"})
                )
            pool_sizes.append(len(eligible_codes))
            if result.timing is not None and result.timing.factors.get("degraded"):
                timing_degraded_dates.append(trade_date)

            # 以"调仓前实际持仓"（已随行情漂移）合成当日目标；收盘模式先成交昨日
            # 指令，仅风险腿到期时沿用今日收盘成交后的成分
            target_positions = compose_two_leg_positions(
                previous=executor.composition_base,
                selection_target=dict(result.positions) if result.positions else {},
                timing_target_exposure=result.timing_target_exposure,
                should_select=legs.selection,
                should_scale_risk=legs.risk,
                max_asset_weight=result.max_asset_weight,
            )
            day = executor.step(
                trade_date=trade_date,
                next_date=next_date,
                target_positions=target_positions,
                should_rebalance=legs.selection or legs.risk,
                return_bars=return_bars,
                quality_bars=all_bars,
            )
            if day.missing_bar_count > 0:
                data_gap_days += 1

            accumulator.apply_day(day.portfolio_return, bool(day.executed_positions))
            day_total_exposure = round(sum(day.positions.values()), 4)
            positions = _round_weights(day.positions)
            executed_positions = _round_weights(day.executed_positions)
            benchmark_ret = benchmark_returns[i] if i < len(benchmark_returns) else None
            timing_regime = result.timing.regime if result.timing else None

            daily_row = BacktestDailyResultModel(
                backtest_id=backtest_id,
                trade_date=trade_date,
                portfolio_return=day.portfolio_return,
                cumulative_return=round(accumulator.cumulative_return_pct, 4),
                drawdown=round(accumulator.drawdown_pct, 4),
                timing_regime=timing_regime,
                total_exposure=day_total_exposure,
                cash_ratio=round(1.0 - day_total_exposure, 4),
                positions=positions or None,
                executed_positions=executed_positions or None,
                benchmark_return=round(benchmark_ret, 4) if benchmark_ret is not None else None,
                turnover=round(day.turnover, 4) if round(day.turnover, 4) > 0 else None,
                missing_bar_count=day.missing_bar_count,
            )
            if persist:
                self._backtest_repo.add_daily_result(daily_row)
            else:
                memory_rows.append(daily_row)

            # 写入指数结果并获取持仓统计（指数收益与组合收益共用前向填充行情）
            day_pos_count, day_pos_positive = self._write_index_results(
                backtest_id,
                trade_date,
                next_date,
                universe,
                result,
                return_bars,
                result.positions if result.positions else {},
                held_positions=day.executed_positions,
                timing_regime=timing_regime,
                scoring_mode=result.effective_scoring_mode,
                selection_rebalanced=legs.selection,
                persist=persist,
                index_buffer=index_buffer,
            )
            total_in_pos_count += day_pos_count
            total_in_pos_positive += day_pos_positive

            logger.debug(
                "[backtest] 日结果: backtest_id=%s date=%s regime=%s exposure=%s 持仓=%s 收益=%s%%",
                backtest_id,
                trade_date,
                timing_regime,
                day_total_exposure,
                positions,
                day.portfolio_return,
            )

            # 每 10% 决策日更新一次进度
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
                    round(accumulator.cumulative_return_pct, 2),
                )

            # 每 100 天 checkpoint 提交一次：
            # - 释放事务大小，避免长区间回测的单一巨大事务
            # - 中途失败时已提交的分段结果保留，前端可查看部分权益曲线
            # - progress 裸 SQL 更新与 params 口径元数据随本次 commit 一起可见
            if persist and (i + 1) % 100 == 0:
                self._flush_results(index_buffer)
                self._db.commit()

        finalize_started = perf_counter()
        if persist:
            self._flush_results(index_buffer)

        # 计算汇总指标（使用账户累积器与内存中的信号准确率，避免依赖已 flush 的 ORM 对象）
        metrics = self._compute_summary_metrics(
            accumulator,
            benchmark_returns,
            total_in_pos_count=total_in_pos_count,
            total_in_pos_positive=total_in_pos_positive,
            data_gap_days=data_gap_days,
            annual_risk_free_rate_pct=risk_free_rate_pct,
        )

        # 收集结构化警告，随成功状态一并持久化，前端轮询时按 key 去重弹窗
        candidate_pool = build_candidate_pool_timeline(
            decision_dates, pool_sizes, len(index_codes), exclusion_dates, exclusion_reasons
        )
        factor_warnings = collect_missing_factor_warnings(
            precomputed, trading_dates, factor_index_codes, required_factor_ids
        ) + collect_compute_failure_warnings(set(factor_matrix.failed) & set(required_factor_ids))
        run_warnings = collect_run_warnings(
            RunDiagnostics(
                calendar_source=run_params.get("_calendar_source"),
                non_trading_dates=run_params.get("_non_trading_bar_dates") or [],
                delisted_codes=sorted(
                    item["index_code"]
                    for item in universe
                    if item.get("is_active") is False and item["index_code"] in all_index_codes
                ),
                warmup_trading_days=warmup_trading_days,
                candidate_pool=candidate_pool,
                timing_degraded_dates=timing_degraded_dates,
                exclusion_dates=exclusion_dates,
                exclusion_reasons=exclusion_reasons,
                data_quality_mode=data_quality_mode,
            ),
            factor_warnings,
        )
        run_warnings.extend(
            collect_data_gap_warnings(
                trading_dates,
                index_codes,
                all_bars,
                execution_model=execution_model,
                data_quality_mode=data_quality_mode,
            )
        )
        if enable_benchmark:
            bench_missing = [d for d in trading_dates if (benchmark_index_code, d) not in all_bars]
            if bench_missing:
                run_warnings.append(
                    BacktestWarning(
                        level="warning",
                        code="BENCHMARK_MISSING",
                        message=(
                            f"基准指数 {benchmark_index_code} 有 {len(bench_missing)} 个交易日"
                            f"缺行情数据（{bench_missing[0]}~{bench_missing[-1]}），"
                            "缺口期间按前值平盘、缺口后补回涨跌"
                        ),
                        index_code=benchmark_index_code,
                    )
                )
        logger.info(
            "[backtest] 回测完成: backtest_id=%s 累计收益=%s%% 最大回撤=%s%% 夏普=%s "
            "胜率=%s%% 信号准确率=%s%% 告警=%s 收尾耗时=%.3fs",
            backtest_id,
            metrics.get("cumulative_return_pct", 0.0),
            metrics.get("max_drawdown_pct", 0.0),
            metrics.get("sharpe_ratio", 0.0),
            metrics.get("win_rate_pct", 0.0),
            metrics.get("signal_accuracy_pct", 0.0),
            len(run_warnings),
            perf_counter() - finalize_started,
        )
        if persist:
            self._backtest_repo.mark_success(
                backtest_id,
                metrics,
                warnings=[w.model_dump() for w in run_warnings],
                candidate_pool=candidate_pool,
            )
        return {
            "metrics": metrics,
            "daily_rows": memory_rows,
            "warnings": [w.model_dump() for w in run_warnings],
            "candidate_pool": candidate_pool,
            "trading_dates": trading_dates,
            "params": dict(row.params or {}),
        }

    def _flush_results(self, index_buffer: list[dict[str, Any]]) -> None:
        """把缓冲的指数级结果批量写入，并 flush 逐日结果（不提交）。

        Args:
            index_buffer: 指数级结果缓冲，写入后清空。
        """
        self._backtest_repo.bulk_insert_index_results(list(index_buffer))
        index_buffer.clear()
        self._db.flush()

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

        当策略引用的任一模板声明 market_scope 为 True 时，
        额外加载全市场指数的日线数据作为因子上下文；这些数据只参与
        因子计算，不进入回测标的池，避免影响组合收益口径。

        集合口径与回测标的池一致，按 point-in-time 取"区间起点仍在存续"的指数
        （``find_for_period``）：否则市场级因子（如市场宽度）只统计活到今天的
        指数，历史上退市的成分被整段抹掉。实时路径只能用当日活跃集合
        （实时无法知道未来），这是模式固有的差异。

        Args:
            config: 策略配置，用于推导所需因子引用。
            trading_dates: 回测交易日列表。
            all_bars: 已加载的行情数据字典，就地补充全市场数据。
        """
        instances = self._factor_provider.resolve_instances(config)
        need_market = any(
            instance.template.market_scope for instance in instances.values()
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
        held_positions: dict[str, float] | None = None,
        timing_regime: str | None = None,
        scoring_mode: str = "absolute",
        selection_rebalanced: bool = True,
        persist: bool = True,
        index_buffer: list[dict[str, Any]] | None = None,
    ) -> tuple[int, int]:
        """写入每日每指数的回测结果，使用与实时一致的信号等级判定逻辑。

        与实时 `_build_strategy_results` 共用同一 `determine_signal_level` 输入：
        score=当日综合得分、target_weight=当日目标权重（result.positions）、
        timing_regime=当日择时 regime、scoring_mode=regime 覆盖后生效的评分模式。

        ``in_portfolio`` 与信号准确率按**当日实际持仓**（``held_positions``）统计，
        与"持仓资产次日正收益占比"的指标定义一致；非调仓日引擎目标与实际持仓
        不同，``target_weight`` 列仍记录引擎目标权重供诊断。

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
            all_bars: 收益结算用行情（主循环传入前向填充后的行情，与组合收益同口径）。
            signal_positions: 当日目标权重。
            held_positions: 当日实际持仓（None 时退化为目标权重，兼容旧调用）。
            timing_regime: 当日择时 regime。
            scoring_mode: regime 覆盖后生效的评分模式。
            selection_rebalanced: 当日是否执行选股调仓；组合分数 IC 只评估这类日期。
            persist: False 时只统计持仓命中数（信号准确率），不写指数结果行
                （D-1 研究批量评估路径）。
            index_buffer: 提供时把结果行以字典追加到缓冲，由主循环按 checkpoint
                批量 INSERT；None 时逐行登记 ORM 对象（兼容旧调用）。

        Returns:
            (in_portfolio_count, in_portfolio_positive_count) 元组。
        """
        score_map = result.scores
        in_pos_count = 0
        in_pos_positive = 0
        holdings = signal_positions if held_positions is None else held_positions

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
            in_portfolio = holdings.get(code, 0.0) > 0
            signal_score = round(score, 2)

            idx_ret = None
            if next_date and in_portfolio:
                idx_ret = get_index_return(code, trade_date, next_date, all_bars)
                in_pos_count += 1
                if idx_ret is not None and idx_ret > 0:
                    in_pos_positive += 1

            if not persist:
                continue
            values = {
                "backtest_id": backtest_id,
                "trade_date": trade_date,
                "index_code": code,
                "signal_score": signal_score,
                "scored": scored,
                "selection_rebalanced": selection_rebalanced,
                "signal_level": level,
                "in_portfolio": in_portfolio,
                "index_return": idx_ret,
                "target_weight": round(target_weight, 4),
            }
            if index_buffer is not None:
                index_buffer.append(values)
            else:
                self._backtest_repo.add_index_result(BacktestIndexResultModel(**values))

        return in_pos_count, in_pos_positive

    # ── 调仓检查 ───────────────────────────────────────────────────────────

    def _build_candidate_pool(
        self,
        config: StrategyConfig,
        trade_date: date,
        next_date: date | None,
        index_codes: list[str],
        all_bars: dict,
        execution_model: str,
    ) -> tuple[list[str], dict[str, set[str]]]:
        """构建当日"可执行候选池"（横截面评分/排名/过滤与收益口径的唯一入口）。

        该候选池与 `data_quality_mode` 无关：两种口径都用同一池子选股，避免
        宽松口径把"无法交易/因子缺失"的标的塞进横截面 z-score 池，导致统计量
        变化进而改变选股结果（同一配置出现两套完全不同绩效）。

        入池条件（全部满足）：
        1. 当日 bar 存在且收盘价有效；
        2. （已移至引擎）"决策所需因子缺失"的剔除由 ``StrategyEngine`` 按 regime
           生效配置与各模块缺失语义统一执行，实时与回测共用；
        3. 若引用 ATR/Donchian/月线等因子，则当日最高/最低价有效；
        4. 存在下一交易日时，次日行情可支撑收益结算：次日 bar 与收盘价有效
           （``MISSING_NEXT_BAR``）、需要高低价时次日高低价有效
           （``MISSING_NEXT_HIGH_LOW``）；执行模型为 T+1 开盘时还需次日开盘价有效
           （``MISSING_OPEN``）。

        说明（已知取舍）：条件 1~3 只用当日信息，条件 4 需要"次日行情是否存在"，
        属 ex-ante 可执行域约束——若不剔除，缺次日行情的资产会以 0 收益参与组合，
        反而系统性低估波动。剔除明细逐日记录在候选池时间线（C6）中，可审计。

        T+1 收盘执行下成交价是次日收盘（条件 4 已检查），成交后首个收益窗口还需
        T+2 收盘价。这里**不**再向前多看一天：收益结算使用前向填充行情
        （``forward_fill_return_bars``），T+2 缺行情时该段记 0、缺口后补回涨跌，
        不会永久丢失收益，因此无需为此扩大前视范围。

        `data_quality_mode` 只影响缺口提示的详细程度（strict 逐指数 / warn 汇总），
        不影响候选池、选股结果与收益序列。

        Args:
            config: 策略配置。
            trade_date: 当前信号交易日。
            next_date: 下一交易日。
            index_codes: 候选指数代码。
            all_bars: 预加载行情。
            execution_model: 执行模型。

        Returns:
            当日可参与策略决策和收益计算的指数代码列表，以及每个被排除指数的原因集合。
        """
        # 评分 + 过滤（含 compare_to 与 regime 覆盖）引用的因子：仅用于判断是否
        # 依赖高低价，不再按因子是否有值剔除
        required_ids = {
            ref
            for module_key, _label, ref in iter_factor_refs(config)
            if module_key in ("score", "filter")
        }
        # 因子引用 → 模板 ID：别名按声明解析，未声明的引用名本身就是模板 ID
        template_ids = {
            (
                config.factor_aliases[ref].template_id
                if ref in config.factor_aliases
                else ref
            )
            for ref in required_ids
        }
        asset_template_ids = {
            template.template_id
            for template in self._registry.all()
            if template.value_shape == "asset"
        }
        required_template_ids = template_ids & asset_template_ids
        # 这些模板依赖 high/low 价格：缺失时因子无法计算，需在候选池阶段剔除
        needs_high_low = bool(required_template_ids & OHLC_DEPENDENT_TEMPLATE_IDS)
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

    def _select_legs(
        self,
        config: StrategyConfig,
        trade_date: date,
    ) -> RebalanceLegs:
        """判定当日到期的调仓腿（回测主循环与口径指纹共用）。

        委托给 DefaultRebalanceScheduler，与实时模式使用相同的交易日历对齐逻辑。

        Args:
            config: 策略配置。
            trade_date: 当前交易日。

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
        return select_active_legs(self._rebalance_scheduler, rebalance, trade_date)

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
        filtered: list[date] = []
        dropped: list[date] = []
        for day in trading_dates:
            (filtered if calendar.is_trading_day(day) else dropped).append(day)
        if dropped:
            logger.warning(
                "[backtest] 行情包含非交易日数据，已从回测交易日中剔除: %s", dropped
            )
        return filtered, dropped

    def _get_lookback_days(self) -> int:
        """按因子模板注册表推导回测回望自然日数，与实时模式口径一致。

        回望窗口取注册表全部模板在默认参数下的 lookback 最大值（730 天，
        由估值百分位与 ERP 百分位决定）。模板参数取值范围受同一不变量约束：
        任一合法参数组合推导出的回望都不超过它（最大为 rsrs 的 722 天），
        因此固定窗口足以覆盖任意配置，长周期因子不会在回测前段因回望不足
        而全部为 None。该不变量由单元测试守住。

        Returns:
            最大回望自然日数。
        """
        return max(
            (template.lookback_days() for template in self._registry.all()), default=90
        )

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

    def row_to_detail(
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
