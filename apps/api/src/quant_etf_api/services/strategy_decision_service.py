"""统一策略执行服务（层间协作 C1 的收敛点）。

原先策略执行被三处各自编排：
- StrategyService.run_allocation（实时分配）
- StrategyExecutionService.execute（持久化信号）
- BacktestService._run_backtest_loop（回测）

本服务收敛"加载配置 → 校验 → 构建上下文 → 引擎执行"这条公共编排链：
实时分配与策略运行共用同一入口；回测因逐日循环与 checkpoint 语义特殊，
保留自己的主循环，但共享 ContextBuilder 与领域层数据准备。

因子值一律由 ContextBuilder 按本次策略实际参数现算，本服务不做因子值持久化，
也不为缺失因子入队补算任务。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.infra.time import today_cn

from quant_etf_api.domain.strategies.rebalance import select_active_legs
from quant_etf_api.engine.config import RebalanceScheduleConfig, StrategyConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.factors.base import MissingReason
from quant_etf_api.factors.catalog import get_factor_template_registry
from quant_etf_api.infra.db.repositories.index_signal import IndexSignalRepository
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.infra.trading_calendar import TradingCalendar, resolve_trading_calendar
from quant_etf_api.schemas.backtest import BacktestWarning
from quant_etf_api.schemas.strategy import (
    AllocationResponse,
    StarredStrategyItem,
    StarredSummaryResponse,
    StrategyValidationResult,
)
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)


def resolve_effective_date(trade_date: date | None = None) -> date:
    """将指定日期对齐到最近的交易日。

    当 trade_date 为 None 时，使用今天；若今天为非交易日（周末/节假日），
    则回退至最近一个交易日。确保调仓日判断和分配管线基于真实交易日执行。

    严格口径（C1）：日历不可用时抛 ``TradingCalendarUnavailableError``，
    由 HTTP 层转换为 503；调用方可显式传入 trade_date 绕过日历。

    Args:
        trade_date: 指定日期，为 None 时自动对齐。

    Returns:
        对齐后的有效交易日。

    Raises:
        TradingCalendarUnavailableError: 交易日历不可用时抛出。
    """
    if trade_date is not None:
        return trade_date
    cal = TradingCalendar()
    today = today_cn()
    if cal.is_trading_day(today):
        return today
    return cal.latest_trading_day(today)


class StrategyDecisionService:
    """统一策略决策服务。

    实时分配、策略运行、星标摘要共用本服务的编排链；
    结果持久化（index_signal / 策略因子快照）统一走仓库写入门禁。
    """

    def __init__(
        self,
        db: Session,
        registry: Any | None = None,
        context_builder: ContextBuilder | None = None,
        engine: StrategyEngine | None = None,
        run_repo: ResearchRunRepository | None = None,
        signal_repo: IndexSignalRepository | None = None,
    ) -> None:
        """初始化统一策略决策服务。

        Args:
            db: SQLAlchemy Session。
            registry: 因子模板注册表，默认进程级单例。
            context_builder: 上下文构建器，未提供时自动创建。
            engine: 策略引擎，未提供时自动创建。
            run_repo: 运行记录仓库。
            signal_repo: 信号仓库。
        """
        self._db = db
        self._registry = registry or get_factor_template_registry()
        self._engine = engine or StrategyEngine()
        self._context_builder = context_builder or ContextBuilder(db, registry=self._registry)
        self._config_svc = StrategyConfigService(db)
        self._run_repo = run_repo or ResearchRunRepository(db)
        self._signal_repo = signal_repo or IndexSignalRepository(db)

    # ==================================================================
    # 编排链公共步骤
    # ==================================================================

    def get_config(self, strategy_id: str) -> StrategyConfig | None:
        """加载并解析策略配置。

        Args:
            strategy_id: 策略标识。

        Returns:
            解析后的配置，不存在时返回 None。
        """
        return self._config_svc.get_parsed_config(strategy_id)

    def validate(self, config: StrategyConfig) -> StrategyValidationResult:
        """校验策略配置（含因子引用与变换函数校验）。

        Args:
            config: 已解析的策略配置。

        Returns:
            校验结果。
        """
        return self._config_svc.validate_parsed(config)

    def build_live_context(
        self,
        config: StrategyConfig,
        trade_date: date | None = None,
    ) -> Any:
        """构建实时模式引擎上下文。

        Args:
            config: 策略配置。
            trade_date: 指定交易日，None 时对齐到今天。

        Returns:
            EngineContext（有效交易日已回退到有数据的最近交易日）。
        """
        effective_date = resolve_effective_date(trade_date)
        return self._context_builder.build(config, effective_date)

    def run(
        self,
        config: StrategyConfig,
        context: Any,
        include_details: bool = True,
    ) -> Any:
        """执行策略引擎管线。

        Args:
            config: 策略配置。
            context: 引擎上下文。
            include_details: 是否构建详细 StrategyResult（回测模式传 False）。

        Returns:
            EngineResult。
        """
        return self._engine.run(config, context, include_details=include_details)

    # ==================================================================
    # 实时分配
    # ==================================================================

    def run_allocation(
        self,
        strategy_id: str,
        trade_date: date | None = None,
    ) -> AllocationResponse | None:
        """运行资产配置决策管线（实时分配统一入口）。

        Args:
            strategy_id: 策略标识。
            trade_date: 指定交易日，不传则对齐到今天。

        Returns:
            AllocationResponse，策略不存在时返回 None。

        Raises:
            ValueError: 配置校验失败（引用未知/停用因子等）。
        """
        config = self.get_config(strategy_id)
        if config is None:
            return None

        # P4 运行期兜底：引用未知/停用因子时快速失败
        validation = self.validate(config)
        if not validation.valid:
            raise ValueError(f"策略 {strategy_id} 配置校验失败: {'; '.join(validation.errors)}")

        start = time.perf_counter()
        logger.info("[strategy] 实时分配启动: strategy=%s trade_date=%s", strategy_id, trade_date)
        context = self.build_live_context(config, trade_date)
        result = self.run(config, context)

        # 构建结构化警告：未知因子已被配置校验拦截，这里只透传可执行的
        # 计算失败 / 数据不足，避免"看起来正常但结果为空"的静默问题。
        missing = self._context_builder.insufficient_factors(context)
        warnings: list[BacktestWarning] = []
        for factor_ref, reason in missing.items():
            label = "计算失败" if reason == MissingReason.COMPUTE_FAILED.value else "数据不足"
            warnings.append(
                BacktestWarning(
                    level="warning",
                    code="MISSING_FACTOR",
                    message=f"因子 {factor_ref} 缺失（{label}），本次决策中该因子按缺失处理",
                    trade_date=context.trade_date,
                )
            )
        if missing:
            logger.warning(
                "[strategy] 因子值不完整: strategy=%s missing=%s",
                strategy_id,
                missing,
            )

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.info(
            "[strategy] 实时分配完成: strategy=%s date=%s 资产=%s 耗时=%sms",
            strategy_id,
            context.trade_date,
            len(result.rankings),
            elapsed_ms,
        )

        return AllocationResponse(
            timing=asdict(result.timing) if result.timing else {},
            rankings=[asdict(r) for r in result.rankings],
            plan={
                "positions": result.positions,
                "total_exposure": result.total_exposure,
                "cash_ratio": result.cash_ratio,
                "method": config.portfolio.method,
            },
            data_date=context.trade_date,
            pipeline_detail=asdict(result.pipeline_detail) if result.pipeline_detail else None,
            warnings=warnings,
        )

    # ==================================================================
    # 持久化执行（策略运行任务）
    # ==================================================================

    def run_and_persist(
        self,
        config: StrategyConfig,
        trade_date: date,
        run_id: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """执行单日策略信号计算并持久化到 index_signal。

        Args:
            config: 策略配置。
            trade_date: 运行对应的交易日。
            run_id: 研究运行 ID。
            params: 策略参数覆盖（暂未使用）。
        """
        context = self.build_live_context(config, trade_date)
        if not context.universe:
            logger.warning("run_and_persist: 无活跃资产，跳过策略运行")
            return

        try:
            result = self.run(config, context)
        except Exception:
            logger.exception("策略 %s 执行失败", config.strategy_id)
            self._mark_run_failed(run_id, f"策略 {config.strategy_id} 执行异常")
            return

        strategy_id = config.strategy_id
        effective_date = context.trade_date

        # 先删除该策略在有效交易日的旧记录（配置修改后不残留旧数据）
        self._signal_repo.delete_by_strategy_date(strategy_id, effective_date)

        signal_rows: list[dict[str, Any]] = []
        for r in result.strategy_results:
            signal_rows.append(
                {
                    "trade_date": r.trade_date,
                    "index_code": r.index_code,
                    "strategy_id": r.strategy_id,
                    "signal_score": r.signal_score,
                    "signal_level": r.signal_level,
                    "signal_label": r.signal_label,
                    "signal_payload": r.payload,
                    "run_id": run_id,
                }
            )

        self._signal_repo.bulk_insert(signal_rows)
        self._db.commit()

        asset_count = len(context.universe)
        self._run_repo.mark_success(
            run_id,
            metrics={
                "index_count": asset_count,
                "signal_count": len(signal_rows),
            },
        )
        logger.info(
            "策略执行完成: %s signals=%d",
            config.strategy_id,
            len(signal_rows),
        )

    def _mark_run_failed(self, run_id: str, message: str) -> None:
        """标记运行失败。"""
        try:
            self._run_repo.mark_failed(run_id, message)
        except Exception:
            logger.warning("更新失败状态时出错", exc_info=True)

    # ==================================================================
    # 星标摘要
    # ==================================================================

    def get_starred_summary(self, trade_date: date | None = None) -> StarredSummaryResponse:
        """获取所有星标策略的当日执行摘要。

        对每个星标策略运行统一分配管线，判断调仓日并汇总摘要。

        Args:
            trade_date: 指定交易日，不传则使用今天。

        Returns:
            StarredSummaryResponse，含各策略的执行摘要列表。
        """
        from quant_etf_api.engine.rebalance import DefaultRebalanceScheduler

        effective_date = resolve_effective_date(trade_date)
        starred_rows = self._config_svc._repo.find_starred()

        items: list[StarredStrategyItem] = []
        # 严格口径（C1）：解析出真实日历后注入调度器；不可用时上抛由 HTTP 层转 503
        scheduler = DefaultRebalanceScheduler(resolve_trading_calendar(self._db)[0])

        for row in starred_rows:
            try:
                config = self.get_config(row.strategy_id)
                if config is None:
                    continue

                allocation = self.run_allocation(row.strategy_id, trade_date=effective_date)
                if allocation is None:
                    continue

                actual_date = allocation.data_date
                # 两腿口径与回测共用领域实现，避免"回测按双腿、实时按单腿"的口径分叉
                legs = select_active_legs(
                    scheduler, config.rebalance, actual_date, None, None
                )
                rebalance_cfg = config.rebalance
                selection_schedule = (
                    rebalance_cfg.selection if rebalance_cfg is not None else RebalanceScheduleConfig()
                )
                risk_schedule = (
                    rebalance_cfg.risk if rebalance_cfg is not None else RebalanceScheduleConfig()
                )
                items.append(
                    StarredStrategyItem(
                        strategy_id=row.strategy_id,
                        display_name=row.display_name,
                        frequency=row.frequency,
                        is_rebalance_day=legs.selection or legs.risk,
                        is_selection_rebalance_day=legs.selection,
                        is_risk_rebalance_day=legs.risk,
                        rebalance_frequency=selection_schedule.frequency,
                        rebalance_day_of_week=selection_schedule.day_of_week,
                        rebalance_day_of_month=selection_schedule.day_of_month,
                        selection_rebalance_schedule=selection_schedule.model_dump(),
                        risk_rebalance_schedule=risk_schedule.model_dump(),
                        timing=allocation.timing,
                        rankings=allocation.rankings,
                        plan=allocation.plan,
                        data_date=allocation.data_date,
                    )
                )
            except Exception:
                logger.exception("获取星标策略 %s 执行摘要失败，跳过", row.strategy_id)
                continue

        return StarredSummaryResponse(trade_date=effective_date, items=items)
