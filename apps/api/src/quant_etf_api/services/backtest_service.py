"""回测引擎服务，负责创建、执行和查询回测任务。

重构后使用统一的 _run_backtest_loop 替代 signal/allocation 双模式分支。
集成 FactorProvider、专业绩效指标、基准对比和交易成本模型。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_
from sqlalchemy.orm import Session

from quant_etf_api.domain.common.constants import (
    SIGNAL_THRESHOLD_HIGH,
    SIGNAL_THRESHOLD_MID,
)
from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.infra.db.models.core import (
    BacktestDailyResultModel,
    BacktestIndexResultModel,
    BacktestRunModel,
    BenchmarkIndexModel,
    IndexDailyBarModel,
    IndexValuationModel,
)
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.schemas.backtest import (
    BacktestCreateRequest,
    BacktestDailyResult,
    BacktestDetail,
    BacktestIndexResult,
    BacktestMetrics,
    BacktestSummary,
)
from quant_etf_api.services.benchmark import compute_buy_hold_benchmark
from quant_etf_api.services.metrics import compute_performance_metrics
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)

# 默认交易日基准（cover_20d 等短窗口无需此值，仅用于需要大量回望的计算）
_BACKTEST_LOOKBACK_DAYS = 90


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

        # 构建 FactorRegistry 供回测因子预计算使用
        from quant_etf_api.factors.registry import build_default_factor_registry

        self._registry = build_default_factor_registry()
        self._factor_provider = FactorProvider(db=db, registry=self._registry)
        self._context_builder = ContextBuilder(db, factor_provider=self._factor_provider)

    def create_backtest(self, req: BacktestCreateRequest) -> BacktestSummary:
        """创建回测记录，状态为 pending，立即返回。

        如果策略配置了 index_codes（非空），则强制将回测标的范围限定为这些指数，
        忽略请求中的 universe_mode 和 index_codes。空 index_codes 表示全指数通用策略。
        """
        backtest_id = str(uuid4())
        now = datetime.now(timezone.utc)

        # 加载策略配置，检查是否有 index_codes 限定
        config_svc = StrategyConfigService(self._db)
        strategy_config = config_svc.get_parsed_config(req.strategy_id)
        strategy_index_codes = strategy_config.index_codes if strategy_config else []

        if strategy_index_codes:
            # 策略有指数范围限定，强制使用策略的 index_codes
            universe_filter = {"mode": "subset", "index_codes": strategy_index_codes}
            logger.info(
                "回测 %s：策略 %s 限定指数范围 %s，强制应用",
                backtest_id, req.strategy_id, strategy_index_codes,
            )
        else:
            universe_filter = (
                {"mode": "all"}
                if req.universe_mode == "all"
                else {"mode": "subset", "index_codes": req.index_codes}
            )

        params = dict(req.params) if req.params else {}
        # 保存基准配置到 params，供执行时读取
        params["_enable_benchmark"] = req.enable_benchmark
        params["_benchmark_index_code"] = req.benchmark_index_code

        try:
            row = BacktestRunModel(
                backtest_id=backtest_id,
                strategy_id=req.strategy_id,
                start_date=req.start_date,
                end_date=req.end_date,
                backtest_mode=req.backtest_mode,
                universe_filter=universe_filter,
                params=params,
                weighting=req.weighting,
                status="pending",
                created_at=now,
            )
            self._db.add(row)
            self._db.commit()
        except Exception:
            self._db.rollback()
            logger.warning("create_backtest DB insert failed", exc_info=True)

        return BacktestSummary(
            backtest_id=backtest_id,
            strategy_id=req.strategy_id,
            start_date=req.start_date,
            end_date=req.end_date,
            status="pending",
            weighting=req.weighting,
            backtest_mode=req.backtest_mode,
            created_at=now,
        )

    def list_backtests(
        self, offset: int = 0, limit: int = 50
    ) -> tuple[list[BacktestSummary], int]:
        """分页返回回测列表，按创建时间倒序。"""
        try:
            rows, total = self._backtest_repo.find_all(offset=offset, limit=limit)
            items = [self._row_to_summary(r) for r in rows]
            return items, total
        except Exception:
            logger.warning("list_backtests DB query failed", exc_info=True)
            return [], 0

    def get_backtest(self, backtest_id: str) -> BacktestDetail | None:
        """返回回测详情。"""
        try:
            row = self._backtest_repo.find_by_id(backtest_id)
            if row is None:
                return None
            return self._row_to_detail(row)
        except Exception:
            logger.warning("get_backtest DB query failed", exc_info=True)
            return None

    def get_daily_results(self, backtest_id: str) -> list[BacktestDailyResult]:
        """返回回测每日组合绩效。"""
        try:
            rows = self._backtest_repo.find_daily_results(backtest_id)
            return [
                BacktestDailyResult(
                    trade_date=r.trade_date,
                    portfolio_return=r.portfolio_return,
                    cumulative_return=r.cumulative_return,
                    drawdown=r.drawdown,
                    high_signal_count=r.high_signal_count,
                    mid_signal_count=r.mid_signal_count,
                    low_signal_count=r.low_signal_count,
                    timing_regime=r.timing_regime,
                    total_exposure=r.total_exposure,
                    cash_ratio=r.cash_ratio,
                    positions=r.positions,
                    benchmark_return=getattr(r, "benchmark_return", None),
                    turnover=getattr(r, "turnover", None),
                )
                for r in rows
            ]
        except Exception:
            logger.warning("get_daily_results DB query failed", exc_info=True)
            return []

    def get_index_results(
        self, backtest_id: str, index_code: str | None = None
    ) -> list[BacktestIndexResult]:
        """返回回测每日每指数信号与收益。"""
        try:
            rows = (
                self._db.query(BacktestIndexResultModel)
                .filter(BacktestIndexResultModel.backtest_id == backtest_id)
            )
            if index_code:
                rows = rows.filter(BacktestIndexResultModel.index_code == index_code)
            rows = rows.order_by(
                BacktestIndexResultModel.trade_date.asc(),
                BacktestIndexResultModel.index_code.asc(),
            ).all()
            return [
                BacktestIndexResult(
                    trade_date=r.trade_date,
                    index_code=r.index_code,
                    signal_score=r.signal_score,
                    signal_level=r.signal_level,
                    in_portfolio=r.in_portfolio,
                    index_return=r.index_return,
                    original_score=getattr(r, "original_score", None),
                )
                for r in rows
            ]
        except Exception:
            logger.warning("get_index_results DB query failed", exc_info=True)
            return []

    def run_backtest(self, backtest_id: str) -> None:
        """回测执行入口，统一使用 _run_backtest_loop。"""
        try:
            row = self._backtest_repo.find_by_id(backtest_id)
            if row is None:
                logger.error("run_backtest: backtest_id %s not found", backtest_id)
                return

            row.status = "running"
            row.started_at = datetime.now(timezone.utc)
            self._db.commit()

            # 加载策略配置
            config_svc = StrategyConfigService(self._db)
            config = config_svc.get_parsed_config(row.strategy_id)
            if config is None:
                raise ValueError(f"策略 {row.strategy_id} 配置不存在")

            self._run_backtest_loop(backtest_id, row, config)

        except Exception as exc:
            self._db.rollback()
            logger.exception("run_backtest failed for %s", backtest_id)
            try:
                self._backtest_repo.mark_failed(backtest_id, str(exc))
            except Exception:
                self._db.rollback()

    # ── 统一回测主循环 ────────────────────────────────────────────────────

    def _run_backtest_loop(
        self,
        backtest_id: str,
        row: BacktestRunModel,
        config: StrategyConfig,
    ) -> None:
        """统一回测主循环，替代旧的 signal/allocation 双分支。

        流程：
        1. 准备数据（标的、交易日、行情、估值）
        2. 预计算全区间因子值
        3. 逐日执行引擎管线
        4. 按仓位/排名计算组合收益
        5. 计算基准收益（如启用）
        6. 扣除交易成本
        7. 写入每日结果和指数结果
        8. 计算汇总绩效指标
        """
        universe, index_codes, trading_dates, all_bars, all_valuation = (
            self._prepare_backtest_data(row)
        )

        # 预计算所有因子值
        precomputed = self._factor_provider.precompute_backtest_factors(
            config, trading_dates, index_codes, all_bars, all_valuation
        )

        # 基准配置
        params = row.params or {}
        enable_benchmark = params.get("_enable_benchmark", True)
        benchmark_index_code = params.get("_benchmark_index_code", "000300")

        # 基准日收益率序列
        benchmark_returns: list[float] = []
        if enable_benchmark:
            benchmark_returns = compute_buy_hold_benchmark(
                all_bars, benchmark_index_code, trading_dates
            )

        daily_results: list[BacktestDailyResultModel] = []
        cumulative = 1.0
        peak = 1.0
        prev_positions: dict[str, float] = {}
        last_rebalance_date: date | None = None

        for i, trade_date in enumerate(trading_dates):
            day_factors = precomputed.get(trade_date, {})

            # 构建上下文
            context = self._context_builder.build(
                config,
                trade_date,
                index_codes=index_codes,
                all_bars=all_bars,
                all_valuation=all_valuation,
                precomputed_factors=day_factors,
            )

            # 检查调仓日
            should_rebalance = self._check_rebalance(config, trade_date, last_rebalance_date)

            # 执行引擎
            result = self._engine.run(config, context)

            next_date = trading_dates[i + 1] if i + 1 < len(trading_dates) else None

            # 确定持仓
            if should_rebalance:
                positions = dict(result.positions) if result.positions else {}
                last_rebalance_date = trade_date
            else:
                # 非调仓日沿用上次持仓
                positions = dict(prev_positions)

            # 计算组合收益
            if positions:
                portfolio_return = self._compute_allocation_return(
                    positions, trade_date, next_date, all_bars
                )
            else:
                # 信号模式：使用排名 + top_n
                portfolio_return, high_cnt, mid_cnt, low_cnt = self._compute_topn_return(
                    result, row.weighting, config.rank.top_n, next_date, all_bars
                )

            # 交易成本
            turnover = 0.0
            if config.transaction_cost and prev_positions and should_rebalance:
                turnover = self._compute_turnover(prev_positions, positions)
                cost = self._apply_transaction_costs(
                    turnover, config.transaction_cost, positions
                )
                portfolio_return -= cost

            # 更新累计和回撤
            cumulative *= 1 + portfolio_return / 100
            cumulative_return_pct = (cumulative - 1) * 100
            peak = max(peak, cumulative)
            drawdown = (cumulative / peak - 1) * 100

            # 信号统计
            if positions:
                high_cnt = len(positions)
                mid_cnt = 0
                low_cnt = 0
            elif not result.rankings:
                high_cnt, mid_cnt, low_cnt = 0, 0, 0

            # 基准收益
            benchmark_ret = benchmark_returns[i] if i < len(benchmark_returns) else None

            daily_row = BacktestDailyResultModel(
                backtest_id=backtest_id,
                trade_date=trade_date,
                portfolio_return=round(portfolio_return, 4),
                cumulative_return=round(cumulative_return_pct, 4),
                drawdown=round(drawdown, 4),
                high_signal_count=high_cnt if not positions else len(positions),
                mid_signal_count=mid_cnt if not positions else 0,
                low_signal_count=low_cnt if not positions else 0,
                timing_regime=result.timing.regime if result.timing else None,
                total_exposure=result.total_exposure if positions else None,
                cash_ratio=result.cash_ratio if positions else None,
                positions=positions if positions else None,
                benchmark_return=round(benchmark_ret, 4) if benchmark_ret is not None else None,
                turnover=round(turnover, 4) if turnover > 0 else None,
            )
            self._db.add(daily_row)
            daily_results.append(daily_row)

            self._write_index_results(
                backtest_id, trade_date, next_date, universe, result, all_bars, positions
            )

            prev_positions = positions

        self._db.flush()

        # 计算汇总指标
        metrics = self._compute_summary_metrics(daily_results, benchmark_returns)
        self._backtest_repo.mark_success(backtest_id, metrics)

    # ── 数据准备 ───────────────────────────────────────────────────────────

    def _prepare_backtest_data(
        self, row: BacktestRunModel
    ) -> tuple[list[dict[str, Any]], list[str], list[date], dict, dict]:
        """准备回测通用数据：标的、交易日、行情、估值。

        Returns:
            (universe, index_codes, trading_dates, all_bars, all_valuation) 元组。
        """
        universe = self._resolve_index_universe(row.universe_filter)
        if not universe:
            raise ValueError("回测标的范围为空，请检查 universe_filter 配置")

        index_codes = [u["index_code"] for u in universe]
        trading_dates = self._get_index_trading_dates(
            row.start_date, row.end_date, index_codes
        )
        if not trading_dates:
            raise ValueError(f"区间 {row.start_date} ~ {row.end_date} 内无交易日数据")

        all_bars = self._load_all_index_bars(trading_dates, index_codes)
        all_valuation = self._load_all_valuation(trading_dates)

        return universe, index_codes, trading_dates, all_bars, all_valuation

    def _write_index_results(
        self,
        backtest_id: str,
        trade_date: date,
        next_date: date | None,
        universe: list[dict[str, Any]],
        result: Any,
        all_bars: dict,
        positions: dict[str, float],
    ) -> None:
        """写入每日每指数的回测结果，使用统一的信号等级判定逻辑。"""
        score_map = result.scores
        rank_map = {r.etf_code: r for r in result.rankings}

        for item in universe:
            code = item["index_code"]
            target_weight = positions.get(code, 0.0)
            score = score_map.get(code, 0.0)
            ranking = rank_map.get(code)
            original_score = score

            if positions:
                # 配置模式：使用统一信号等级判定
                in_portfolio = target_weight > 0
                if in_portfolio:
                    level = "HIGH" if score >= SIGNAL_THRESHOLD_HIGH else "MID"
                else:
                    level = "LOW"
                signal_score = round(target_weight * 100, 2)
                # 配置模式下保留原始得分
                original_score = round(score, 2)
            else:
                # 信号模式：使用领域常量阈值
                if ranking:
                    if ranking.score >= SIGNAL_THRESHOLD_HIGH:
                        level = "HIGH"
                    elif ranking.score >= SIGNAL_THRESHOLD_MID:
                        level = "MID"
                    else:
                        level = "LOW"
                    in_portfolio = level == "HIGH"
                else:
                    in_portfolio = False
                    level = "LOW"
                signal_score = round(score, 2)

            idx_ret = None
            if next_date and in_portfolio:
                idx_ret = self._get_index_return(
                    code, trade_date, next_date, all_bars
                )

            self._db.add(
                BacktestIndexResultModel(
                    backtest_id=backtest_id,
                    trade_date=trade_date,
                    index_code=code,
                    signal_score=signal_score,
                    signal_level=level,
                    in_portfolio=in_portfolio,
                    index_return=idx_ret,
                    original_score=original_score,
                )
            )

    # ── 收益计算 ───────────────────────────────────────────────────────────

    def _compute_allocation_return(
        self,
        positions: dict[str, float],
        trade_date: date,
        next_date: date | None,
        all_bars: dict,
    ) -> float:
        """按仓位分配方案计算指数组合 T+1 收益。"""
        if next_date is None or not positions:
            return 0.0
        total_return = 0.0
        for code, weight in positions.items():
            ret = self._get_index_return(code, trade_date, next_date, all_bars)
            if ret is not None:
                total_return += weight * ret
        return round(total_return, 4)

    def _compute_topn_return(
        self,
        result: Any,
        weighting: str,
        top_n: int | None,
        next_date: date | None,
        all_bars: dict,
    ) -> tuple[float, int, int, int]:
        """信号模式：按排名取 Top N 资产计算组合收益。

        与旧 _compute_signal_return 的核心区别：
        尊重 RankConfig.top_n 而非硬编码 HIGH 信号等级为入选条件。

        Args:
            result: 引擎执行结果。
            weighting: 加权方式，equal 或 signal_weighted。
            top_n: 取前 N 名，None 时取全部 HIGH 信号。
            next_date: T+1 日期。
            all_bars: 预加载行情数据。

        Returns:
            (portfolio_return, high_count, mid_count, low_count) 元组。
        """
        high = [r for r in result.strategy_results if r.signal_level == "HIGH"]
        mid = [r for r in result.strategy_results if r.signal_level == "MID"]
        low = [r for r in result.strategy_results if r.signal_level == "LOW"]

        # 按得分降序排序，取 top_n
        selected = sorted(high, key=lambda r: r.signal_score, reverse=True)
        if top_n is not None and len(selected) > top_n:
            selected = selected[:top_n]

        if not selected or next_date is None:
            return 0.0, len(high), len(mid), len(low)

        returns = []
        weights = []
        for r in selected:
            ret = self._get_index_return(
                r.etf_code, r.trade_date, next_date, all_bars
            )
            if ret is not None:
                returns.append(ret)
                weights.append(
                    r.signal_score if weighting == "signal_weighted" else 1.0
                )

        if not returns:
            return 0.0, len(high), len(mid), len(low)

        total_weight = sum(weights)
        portfolio_return = (
            sum(r * w for r, w in zip(returns, weights)) / total_weight
        )
        return round(portfolio_return, 4), len(high), len(mid), len(low)

    # ── 交易成本 ───────────────────────────────────────────────────────────

    @staticmethod
    def _compute_turnover(
        prev_positions: dict[str, float],
        curr_positions: dict[str, float],
    ) -> float:
        """计算换手率（两日仓位变动的绝对值之和 / 2）。

        Args:
            prev_positions: 前日仓位权重。
            curr_positions: 当日目标仓位权重。

        Returns:
            换手率，0-1。
        """
        all_codes = set(prev_positions.keys()) | set(curr_positions.keys())
        turnover = 0.0
        for code in all_codes:
            prev_w = prev_positions.get(code, 0.0)
            curr_w = curr_positions.get(code, 0.0)
            turnover += abs(curr_w - prev_w)
        return turnover / 2.0

    @staticmethod
    def _apply_transaction_costs(
        turnover: float,
        cost_config: Any,
        positions: dict[str, float],
    ) -> float:
        """计算交易成本对组合收益的影响（%）。

        Args:
            turnover: 换手率，0-1。
            cost_config: TransactionCostConfig 实例。
            positions: 当前仓位权重，用于判断是否按 turnover 计费。

        Returns:
            应扣减的收益百分比。
        """
        from quant_etf_api.engine.config import TransactionCostConfig

        cfg: TransactionCostConfig = cost_config
        total_cost_rate = cfg.commission_rate + cfg.slippage_rate

        if cfg.apply_to_turnover:
            # 仅对换仓部分收费
            return round(turnover * total_cost_rate * 100, 4)
        else:
            # 按全仓收费
            total_exposure = sum(positions.values())
            return round(total_exposure * total_cost_rate * 100, 4)

    # ── 调仓检查 ───────────────────────────────────────────────────────────

    @staticmethod
    def _check_rebalance(
        config: StrategyConfig,
        trade_date: date,
        last_rebalance_date: date | None,
    ) -> bool:
        """检查当日是否为调仓日。

        无 rebalance 配置时默认为每日调仓。

        Args:
            config: 策略配置。
            trade_date: 当前交易日。
            last_rebalance_date: 上次调仓日期。

        Returns:
            是否应该调仓。
        """
        if config.rebalance is None:
            return True

        rebalance = config.rebalance
        if rebalance.frequency == "daily":
            return True
        if rebalance.frequency == "weekly":
            target_day = rebalance.day_of_week if rebalance.day_of_week is not None else 4
            return trade_date.weekday() == target_day
        if rebalance.frequency == "monthly":
            target_day = rebalance.day_of_month if rebalance.day_of_month is not None else 1
            return trade_date.day == target_day

        return True

    # ── 汇总指标 ───────────────────────────────────────────────────────────

    def _compute_summary_metrics(
        self,
        daily_results: list[BacktestDailyResultModel],
        benchmark_returns: list[float],
    ) -> dict[str, Any]:
        """计算回测汇总绩效指标，集成专业指标和基准对比。"""
        if not daily_results:
            return BacktestMetrics(
                cumulative_return_pct=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                win_rate_pct=0.0,
                signal_accuracy_pct=0.0,
                total_trading_days=0,
                active_days=0,
            ).model_dump()

        daily_rets = [r.portfolio_return for r in daily_results]
        active_rets = [
            r.portfolio_return for r in daily_results if r.high_signal_count > 0
        ]

        # 专业指标
        perf = compute_performance_metrics(
            daily_rets,
            benchmark_returns=benchmark_returns if benchmark_returns else None,
        )

        # 信号准确率
        signal_accuracy_pct = 0.0
        try:
            backtest_id = daily_results[0].backtest_id
            idx_rows = (
                self._db.query(BacktestIndexResultModel)
                .filter(
                    BacktestIndexResultModel.backtest_id == backtest_id,
                    BacktestIndexResultModel.in_portfolio.is_(True),
                    BacktestIndexResultModel.index_return.isnot(None),
                )
                .all()
            )
            if idx_rows:
                positive = sum(
                    1 for r in idx_rows
                    if r.index_return is not None and r.index_return > 0
                )
                signal_accuracy_pct = round(positive / len(idx_rows) * 100, 2)
        except Exception:
            logger.warning("compute signal_accuracy_pct failed", exc_info=True)

        # 基准对比
        benchmark_return_pct = None
        excess_return_pct = None
        if benchmark_returns:
            bench_cumulative = 1.0
            for r in benchmark_returns:
                bench_cumulative *= 1 + r / 100
            benchmark_return_pct = round((bench_cumulative - 1) * 100, 2)
            excess_return_pct = round(perf.total_return_pct - benchmark_return_pct, 2)

        return BacktestMetrics(
            cumulative_return_pct=perf.total_return_pct,
            max_drawdown_pct=perf.max_drawdown_pct,
            sharpe_ratio=perf.sharpe_ratio,
            win_rate_pct=perf.win_rate_pct,
            signal_accuracy_pct=signal_accuracy_pct,
            total_trading_days=len(daily_results),
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
        ).model_dump()

    # ── 辅助方法 ───────────────────────────────────────────────────────────

    def _resolve_index_universe(
        self, universe_filter: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """根据 universe_filter 查询回测指数列表。

        仅返回活跃指数（is_active=True），排除已退市/停发的指数，
        避免回测中的幸存者偏差。
        """
        base_query = self._db.query(BenchmarkIndexModel).filter(
            BenchmarkIndexModel.is_active.is_(True)
        )
        if universe_filter.get("mode") == "subset":
            codes = universe_filter.get("index_codes", [])
            if codes:
                rows = (
                    base_query.filter(BenchmarkIndexModel.index_code.in_(codes)).all()
                )
                return [
                    {"etf_code": r.index_code, "index_code": r.index_code, "name_cn": r.name_cn}
                    for r in rows
                ]
        rows = base_query.all()
        return [
            {"etf_code": r.index_code, "index_code": r.index_code, "name_cn": r.name_cn}
            for r in rows
        ]

    def _get_index_trading_dates(
        self, start: date, end: date, index_codes: list[str]
    ) -> list[date]:
        """从 index_daily_bar 中提取区间内的交易日列表。"""
        rows = (
            self._db.query(IndexDailyBarModel.trade_date)
            .filter(
                and_(
                    IndexDailyBarModel.trade_date >= start,
                    IndexDailyBarModel.trade_date <= end,
                    IndexDailyBarModel.index_code.in_(index_codes),
                )
            )
            .distinct()
            .order_by(IndexDailyBarModel.trade_date.asc())
            .all()
        )
        return [r.trade_date for r in rows]

    def _load_all_index_bars(
        self, trading_dates: list[date], index_codes: list[str]
    ) -> dict[tuple[str, date], Any]:
        """批量加载回测区间及前 90 日的指数行情数据。"""
        if not trading_dates:
            return {}
        lookback_start = trading_dates[0] - timedelta(days=_BACKTEST_LOOKBACK_DAYS)
        return self._index_bar_repo.find_all_date_range(
            lookback_start, trading_dates[-1]
        )

    def _load_all_valuation(
        self, trading_dates: list[date]
    ) -> dict[tuple[str, date], Any]:
        """批量加载指数估值数据。"""
        if not trading_dates:
            return {}
        rows = (
            self._db.query(IndexValuationModel)
            .filter(
                and_(
                    IndexValuationModel.trade_date >= trading_dates[0],
                    IndexValuationModel.trade_date <= trading_dates[-1],
                )
            )
            .all()
        )
        return {(r.index_code, r.trade_date): r for r in rows}

    def _get_index_return(
        self,
        index_code: str,
        trade_date: date,
        next_date: date,
        all_bars: dict,
    ) -> float | None:
        """获取指数 T+1 日收益率（%）。"""
        today_bar = all_bars.get((index_code, trade_date))
        next_bar = all_bars.get((index_code, next_date))
        if today_bar is None or next_bar is None:
            return None
        if (
            today_bar.close_price is None
            or next_bar.close_price is None
            or today_bar.close_price == 0
        ):
            return None
        return round(
            (next_bar.close_price / today_bar.close_price - 1) * 100, 4
        )

    # ── Schema 转换辅助 ────────────────────────────────────────────────────

    def _row_to_summary(self, row: BacktestRunModel) -> BacktestSummary:
        """将 ORM 行转换为 BacktestSummary。"""
        metrics = None
        if row.metrics:
            try:
                metrics = BacktestMetrics(**row.metrics)
            except Exception:
                pass
        return BacktestSummary(
            backtest_id=row.backtest_id,
            strategy_id=row.strategy_id,
            start_date=row.start_date,
            end_date=row.end_date,
            status=row.status,
            weighting=row.weighting,
            backtest_mode=getattr(row, "backtest_mode", "signal"),
            metrics=metrics,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error_message=row.error_message,
        )

    def _row_to_detail(self, row: BacktestRunModel) -> BacktestDetail:
        """将 ORM 行转换为 BacktestDetail。"""
        summary = self._row_to_summary(row)
        return BacktestDetail(
            **summary.model_dump(),
            universe_filter=row.universe_filter or {"mode": "all"},
            params=row.params,
        )
