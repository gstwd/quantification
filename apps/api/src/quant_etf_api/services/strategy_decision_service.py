"""统一策略执行服务（层间协作 C1 的收敛点）。

原先策略执行被三处各自编排：
- StrategyService.run_allocation（实时分配）
- StrategyExecutionService.execute（持久化信号与因子值）
- BacktestService._run_backtest_loop（回测）

本服务收敛"加载配置 → 校验 → 构建上下文 → 补算触发 → 引擎执行"这条
公共编排链：实时分配与策略运行共用同一入口；回测因逐日循环与
checkpoint 语义特殊，保留自己的主循环，但共享 ContextBuilder 与
领域层数据准备。

同时承载引擎侧按需补算的迁移（C3）：ContextBuilder 保持只读，
因子缺失检测结果在本服务层转换为 factor_computation 异步任务。
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.factors.base import MissingReason
from quant_etf_api.factors.registry import get_default_factor_registry
from quant_etf_api.infra.db.repositories.index_factor_value import IndexFactorValueRepository
from quant_etf_api.infra.db.repositories.index_signal import IndexSignalRepository
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.infra.trading_calendar import TradingCalendar
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

    Args:
        trade_date: 指定日期，为 None 时自动对齐。

    Returns:
        对齐后的有效交易日。
    """
    if trade_date is not None:
        return trade_date
    cal = TradingCalendar()
    today = date.today()
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
        factor_value_repo: IndexFactorValueRepository | None = None,
    ) -> None:
        """初始化统一策略决策服务。

        Args:
            db: SQLAlchemy Session。
            registry: 因子注册表，默认进程级单例。
            context_builder: 上下文构建器，未提供时自动创建。
            engine: 策略引擎，未提供时自动创建。
            run_repo: 运行记录仓库。
            signal_repo: 信号仓库。
            factor_value_repo: 因子值仓库。
        """
        self._db = db
        self._registry = registry or get_default_factor_registry()
        self._engine = engine or StrategyEngine()
        self._context_builder = context_builder or ContextBuilder(db, registry=self._registry)
        self._config_svc = StrategyConfigService(db)
        self._run_repo = run_repo or ResearchRunRepository(db)
        self._signal_repo = signal_repo or IndexSignalRepository(db)
        self._factor_value_repo = factor_value_repo or IndexFactorValueRepository(db)

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

    def ensure_live_factors(self, config: StrategyConfig, context: Any) -> list[str]:
        """检测实时上下文缺失因子并触发异步补算。

        三种缺失语义中，FACTOR_UNKNOWN（配置引用未知因子）不做补算，
        由配置校验期快速失败兜底；NOT_COMPUTED 与 INSUFFICIENT_DATA
        入队 factor_computation（按交易日去重）。

        Args:
            config: 策略配置。
            context: 实时模式构建的 EngineContext。

        Returns:
            本次触发补算的因子 ID 列表（空表示无需补算）。
        """
        index_codes = [u["index_code"] for u in context.universe]
        missing = self._context_builder.detect_missing_factors(
            config, index_codes, context.asset_factors
        )
        actionable = [
            fid
            for fid, reason in missing.items()
            if reason in (MissingReason.NOT_COMPUTED.value, MissingReason.INSUFFICIENT_DATA.value)
        ]
        if not actionable:
            return []

        logger.info(
            "因子数据缺失，入队异步计算: trade_date=%s missing=%s",
            context.trade_date,
            actionable[:5],
        )
        from quant_etf_api.infra.job_queue.queue import get_job_queue

        get_job_queue().enqueue(
            "factor_computation",
            {"trade_date": context.trade_date.isoformat()},
            job_key=f"factor_computation:{context.trade_date}",
        )
        return actionable

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
        # 缺失因子触发异步补算（只读检测，不阻塞本次返回）
        self.ensure_live_factors(config, context)
        result = self.run(config, context)

        # 构建结构化警告：未知因子已被配置校验拦截，这里只透传可执行的
        # NOT_COMPUTED / INSUFFICIENT_DATA，避免"看起来正常但结果为空"的静默问题。
        index_codes = [u["index_code"] for u in context.universe]
        missing = self._context_builder.detect_missing_factors(
            config, index_codes, context.asset_factors
        )
        warnings: list[BacktestWarning] = []
        for fid, reason in missing.items():
            if reason in (MissingReason.NOT_COMPUTED.value, MissingReason.INSUFFICIENT_DATA.value):
                label = "当日未计算" if reason == MissingReason.NOT_COMPUTED.value else "数据不足"
                warnings.append(
                    BacktestWarning(
                        level="warning",
                        code="MISSING_FACTOR",
                        message=f"因子 {fid} 缺失（{label}），本次决策中该因子按缺失处理",
                        trade_date=context.trade_date,
                    )
                )
        if missing:
            logger.warning(
                "[strategy] 因子缺失: strategy=%s missing=%s",
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
                "method": config.portfolio.method if config.portfolio else "signal_only",
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
        """执行单日策略信号计算并持久化到 index_signal / 因子快照。

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
        self._factor_value_repo.delete_strategy_values(strategy_id, effective_date)

        # 收集待写入的信号和因子值
        signal_rows: list[dict[str, Any]] = []
        factor_rows: list[dict[str, Any]] = []
        for r in result.strategy_results:
            signal_rows.append(
                {
                    "trade_date": r.trade_date,
                    "index_code": r.etf_code,
                    "strategy_id": r.strategy_id,
                    "signal_score": r.signal_score,
                    "signal_level": r.signal_level,
                    "signal_label": r.signal_label,
                    "signal_payload": r.payload,
                    "run_id": run_id,
                }
            )
            for fv in r.factor_values:
                raw = fv.get("value")
                num_val: float | None = None
                txt_val: str | None = None
                if isinstance(raw, (int, float)):
                    num_val = float(raw)
                elif raw is not None:
                    txt_val = str(raw)
                factor_rows.append(
                    {
                        "trade_date": r.trade_date,
                        "index_code": r.etf_code,
                        "factor_id": fv["factor_id"],
                        "factor_value_numeric": num_val,
                        "factor_value_text": txt_val,
                        "factor_payload": fv.get("payload"),
                        "strategy_id": r.strategy_id,
                    }
                )

        # 批量写入信号与因子快照（仓库写入门禁）
        self._signal_repo.bulk_insert(signal_rows)
        self._factor_value_repo.bulk_insert_strategy_values(factor_rows)
        self._db.commit()

        asset_count = len(context.universe)
        self._run_repo.mark_success(
            run_id,
            metrics={
                "etf_count": asset_count,
                "signal_count": len(signal_rows),
                "factor_count": len(factor_rows),
            },
        )
        logger.info(
            "策略执行完成: %s signals=%d factors=%d",
            config.strategy_id,
            len(signal_rows),
            len(factor_rows),
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
        scheduler = DefaultRebalanceScheduler()

        for row in starred_rows:
            try:
                config = self.get_config(row.strategy_id)
                if config is None:
                    continue

                allocation = self.run_allocation(row.strategy_id, trade_date=effective_date)
                if allocation is None:
                    continue

                actual_date = allocation.data_date
                if config.rebalance is not None:
                    is_rebalance_day = scheduler.should_rebalance(
                        config.rebalance, actual_date, None
                    )
                else:
                    # 无调仓配置默认视为每日调仓
                    is_rebalance_day = True

                rebalance_cfg = config.rebalance
                items.append(
                    StarredStrategyItem(
                        strategy_id=row.strategy_id,
                        display_name=row.display_name,
                        frequency=row.frequency,
                        is_rebalance_day=is_rebalance_day,
                        rebalance_frequency=rebalance_cfg.frequency if rebalance_cfg else "daily",
                        rebalance_day_of_week=rebalance_cfg.day_of_week if rebalance_cfg else None,
                        rebalance_day_of_month=rebalance_cfg.day_of_month
                        if rebalance_cfg
                        else None,
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
