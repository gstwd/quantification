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

from quant_etf_api.domain.common.signal_level import determine_signal_level
from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.rebalance import DefaultRebalanceScheduler
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.infra.db.models.core import (
    BacktestDailyResultModel,
    BacktestRunModel,
    BenchmarkIndexModel,
    IndexDailyBarModel,
    IndexValuationModel,
    MacroIndicatorModel,
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
        self._rebalance_scheduler = DefaultRebalanceScheduler()

        # 使用进程级单例注册表，避免每次请求重建
        from quant_etf_api.factors.registry import get_default_factor_registry

        self._registry = get_default_factor_registry()
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

        if strategy_config is not None and strategy_config.portfolio is None:
            raise ValueError("策略未配置 portfolio 模块，无法执行回测。请在策略配置中添加 portfolio。")

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
        # 保存基准配置到 params，供执行时读取
        params["_enable_benchmark"] = req.enable_benchmark
        params["_benchmark_index_code"] = req.benchmark_index_code

        try:
            row = BacktestRunModel(
                backtest_id=backtest_id,
                strategy_id=req.strategy_id,
                start_date=req.start_date,
                end_date=req.end_date,
                universe_filter=universe_filter,
                params=params,
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
            created_at=now,
        )

    def list_backtests(self, offset: int = 0, limit: int = 50) -> tuple[list[BacktestSummary], int]:
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
            rows = self._backtest_repo.find_index_results(backtest_id, index_code)
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
        universe, index_codes, trading_dates, all_bars, all_valuation, all_macro = self._prepare_backtest_data(
            row
        )

        # 预计算所有因子值
        precomputed = self._factor_provider.precompute_backtest_factors(
            config, trading_dates, index_codes, all_bars, all_valuation, all_macro
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
        # 轻量累积器：保存计算汇总指标所需的字段，避免长区间回测内存溢出
        metric_accumulator: list[tuple[float, int]] = []
        cumulative = 1.0
        peak = 1.0
        prev_positions: dict[str, float] = {}
        last_rebalance_date: date | None = None
        # 进度跟踪：每完成约 10% 交易日写一次进度
        last_progress = 0
        total_dates = len(trading_dates)

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
                portfolio_return = 0.0

            # 换手率（仅在调仓日计算）
            turnover = 0.0
            if prev_positions and should_rebalance:
                turnover = self._compute_turnover(prev_positions, positions)

            # 更新累计和回撤
            cumulative *= 1 + portfolio_return / 100
            cumulative_return_pct = (cumulative - 1) * 100
            peak = max(peak, cumulative)
            drawdown = (cumulative / peak - 1) * 100

            # 持仓统计
            high_cnt = len(positions) if positions else 0
            mid_cnt = 0
            low_cnt = 0

            # 基准收益
            benchmark_ret = benchmark_returns[i] if i < len(benchmark_returns) else None

            daily_row = BacktestDailyResultModel(
                backtest_id=backtest_id,
                trade_date=trade_date,
                portfolio_return=round(portfolio_return, 4),
                cumulative_return=round(cumulative_return_pct, 4),
                drawdown=round(drawdown, 4),
                high_signal_count=high_cnt,
                mid_signal_count=mid_cnt,
                low_signal_count=low_cnt,
                timing_regime=result.timing.regime if result.timing else None,
                total_exposure=result.total_exposure if positions else None,
                cash_ratio=result.cash_ratio if positions else None,
                positions=positions if positions else None,
                benchmark_return=round(benchmark_ret, 4) if benchmark_ret is not None else None,
                turnover=round(turnover, 4) if turnover > 0 else None,
            )
            self._db.add(daily_row)
            daily_results.append(daily_row)
            metric_accumulator.append((portfolio_return, high_cnt))

            self._write_index_results(
                backtest_id, trade_date, next_date, universe, result, all_bars, positions,
                scoring_mode=config.score.scoring_mode,
            )

            prev_positions = positions

            # 每 10% 交易日更新一次进度（最少 1 天触发）
            if total_dates > 0:
                new_progress = int((i + 1) / total_dates * 100)
                if new_progress - last_progress >= 10:
                    self._backtest_repo.update_progress(backtest_id, new_progress)
                    last_progress = new_progress

            # 每 100 天 flush 一次，释放内存中的 ORM 对象
            if (i + 1) % 100 == 0:
                self._db.flush()
                daily_results.clear()  # 释放 ORM 对象，metric_accumulator 已保留关键数据

        self._db.flush()

        # 计算汇总指标（使用轻量累积器，避免依赖已 flush 的 ORM 对象）
        metrics = self._compute_summary_metrics(metric_accumulator, benchmark_returns, backtest_id)
        self._backtest_repo.mark_success(backtest_id, metrics)

    # ── 数据准备 ───────────────────────────────────────────────────────────

    def _prepare_backtest_data(
        self, row: BacktestRunModel
    ) -> tuple[list[dict[str, Any]], list[str], list[date], dict, dict, dict[str, dict[str, float]]]:
        """准备回测通用数据：标的、交易日、行情、估值、宏观指标。

        Returns:
            (universe, index_codes, trading_dates, all_bars, all_valuation, all_macro) 元组。
        """
        universe = self._resolve_index_universe(row.universe_filter)
        if not universe:
            raise ValueError("回测标的范围为空，请检查 universe_filter 配置")

        index_codes = [u["index_code"] for u in universe]
        trading_dates = self._get_index_trading_dates(row.start_date, row.end_date, index_codes)
        if not trading_dates:
            raise ValueError(f"区间 {row.start_date} ~ {row.end_date} 内无交易日数据")

        all_bars = self._load_all_index_bars(trading_dates, index_codes)
        all_valuation = self._load_all_valuation(trading_dates, index_codes)
        all_macro = self._load_all_macro()

        return universe, index_codes, trading_dates, all_bars, all_valuation, all_macro

    def _write_index_results(
        self,
        backtest_id: str,
        trade_date: date,
        next_date: date | None,
        universe: list[dict[str, Any]],
        result: Any,
        all_bars: dict,
        positions: dict[str, float],
        scoring_mode: str = "absolute",
    ) -> None:
        """写入每日每指数的回测结果，使用统一的信号等级判定逻辑。"""
        score_map = result.scores

        for item in universe:
            code = item["index_code"]
            target_weight = positions.get(code, 0.0)
            score = score_map.get(code, 0.0)
            original_score = round(score, 2)

            level, _ = determine_signal_level(
                score=score,
                target_weight=target_weight,
                has_positions=True,
                scoring_mode=scoring_mode,
            )
            in_portfolio = target_weight > 0
            signal_score = round(target_weight * 100, 2)

            idx_ret = None
            if next_date and in_portfolio:
                idx_ret = self._get_index_return(code, trade_date, next_date, all_bars)

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

    # ── 调仓检查 ───────────────────────────────────────────────────────────

    def _check_rebalance(
        self,
        config: StrategyConfig,
        trade_date: date,
        last_rebalance_date: date | None,
    ) -> bool:
        """检查当日是否为调仓日。

        委托给 DefaultRebalanceScheduler，与实盘模式使用相同的交易日历对齐逻辑。
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
        return self._rebalance_scheduler.should_rebalance(
            config.rebalance, trade_date, last_rebalance_date
        )

    # ── 汇总指标 ───────────────────────────────────────────────────────────

    def _compute_summary_metrics(
        self,
        metric_accumulator: list[tuple[float, int]],
        benchmark_returns: list[float],
        backtest_id: str,
    ) -> dict[str, Any]:
        """计算回测汇总绩效指标，集成专业指标和基准对比。

        Args:
            metric_accumulator: 轻量累积器，每项为 (portfolio_return, high_signal_count)。
            benchmark_returns: 基准日收益率序列。
            backtest_id: 回测 ID，用于查询 DB 中的指数结果。
        """
        if not metric_accumulator:
            return BacktestMetrics(
                cumulative_return_pct=0.0,
                max_drawdown_pct=0.0,
                sharpe_ratio=0.0,
                win_rate_pct=0.0,
                signal_accuracy_pct=0.0,
                total_trading_days=0,
                active_days=0,
            ).model_dump()

        daily_rets = [r[0] for r in metric_accumulator]
        active_rets = [r[0] for r in metric_accumulator if r[1] > 0]

        # 专业指标
        perf = compute_performance_metrics(
            daily_rets,
            benchmark_returns=benchmark_returns if benchmark_returns else None,
        )

        # 信号准确率
        signal_accuracy_pct = 0.0
        try:
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
                    1 for r in idx_rows if r.index_return is not None and r.index_return > 0
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
            total_trading_days=len(metric_accumulator),
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

    def _resolve_index_universe(self, universe_filter: dict[str, Any]) -> list[dict[str, Any]]:
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
                rows = base_query.filter(BenchmarkIndexModel.index_code.in_(codes)).all()
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
        return self._index_bar_repo.find_all_date_range(lookback_start, trading_dates[-1])

    def _load_all_valuation(
        self, trading_dates: list[date], index_codes: list[str]
    ) -> dict[tuple[str, date], Any]:
        """批量加载指数估值数据，按 index_codes 过滤。"""
        if not trading_dates:
            return {}
        rows = (
            self._db.query(IndexValuationModel)
            .filter(
                and_(
                    IndexValuationModel.trade_date >= trading_dates[0],
                    IndexValuationModel.trade_date <= trading_dates[-1],
                    IndexValuationModel.index_code.in_(index_codes),
                )
            )
            .all()
        )
        return {(r.index_code, r.trade_date): r for r in rows}

    def _load_all_macro(self) -> dict[str, dict[str, float]]:
        """加载全部宏观指标数据（LPR 等），用于因子计算。

        Returns:
            key=indicator_code, value={period: value} 的字典。
        """
        rows = (
            self._db.query(MacroIndicatorModel)
            .filter(
                MacroIndicatorModel.indicator_code.in_(["lpr1y", "lpr5y", "cpi", "pmi"])
            )
            .all()
        )
        result: dict[str, dict[str, float]] = {}
        for row in rows:
            code = row.indicator_code
            if code not in result:
                result[code] = {}
            result[code][row.period] = row.value
        return result

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
        return round((next_bar.close_price / today_bar.close_price - 1) * 100, 4)

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
            metrics=metrics,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error_message=row.error_message,
            progress=getattr(row, "progress", 0) or 0,
        )

    def _row_to_detail(self, row: BacktestRunModel) -> BacktestDetail:
        """将 ORM 行转换为 BacktestDetail。"""
        summary = self._row_to_summary(row)
        return BacktestDetail(
            **summary.model_dump(),
            universe_filter=row.universe_filter or {"mode": "all"},
            params=row.params,
        )
