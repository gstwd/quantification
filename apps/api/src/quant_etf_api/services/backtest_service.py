"""回测引擎服务，负责创建、执行和查询回测任务。

重构后使用统一的 _run_backtest_loop 替代 signal/allocation 双模式分支。
集成 FactorProvider、专业绩效指标和基准对比。
回测收益按毛收益口径输出：系统当前阶段不考虑实盘交易与交易成本，
仅研究策略理想效果。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from quant_etf_api.domain.common.signal_level import determine_signal_level
from quant_etf_api.domain.portfolio.accounting import BacktestDayAccumulator
from quant_etf_api.domain.portfolio.returns import (
    compute_allocation_return,
    compute_rebalance_day_return,
    get_index_return,
)
from quant_etf_api.domain.portfolio.turnover import compute_turnover
from quant_etf_api.domain.portfolio.universe import (
    build_universe_items,
    filter_universe_rows,
)
from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.rebalance import DefaultRebalanceScheduler
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.factors.registry import max_lookback_days
from quant_etf_api.infra.db.models.core import (
    BacktestComparisonModel,
    BacktestDailyResultModel,
    BacktestIndexResultModel,
    BacktestRunModel,
)
from quant_etf_api.infra.db.base import utcnow
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.schemas.backtest import (
    BacktestComparisonCreateRequest,
    BacktestComparisonDetail,
    BacktestComparisonSummary,
    BacktestCreateRequest,
    BacktestDailyResult,
    BacktestDetail,
    BacktestIndexResult,
    BacktestMetrics,
    BacktestSummary,
    BacktestWarning,
    ComparisonDailyPoint,
    ComparisonDailyResponse,
    ComparisonMetrics,
)
from quant_etf_api.services.benchmark import compute_buy_hold_benchmark
from quant_etf_api.services.metrics import compute_performance_metrics
from quant_etf_api.services.strategy_config_service import StrategyConfigService

logger = logging.getLogger(__name__)


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
        now = utcnow()

        # 加载策略配置，检查是否有 index_codes 限定
        config_svc = StrategyConfigService(self._db)
        strategy_config = config_svc.get_parsed_config(req.strategy_id)
        if strategy_config is None:
            raise ValueError(f"策略 {req.strategy_id} 配置不存在或解析失败，无法执行回测")

        if strategy_config.portfolio is None:
            raise ValueError(
                "策略未配置 portfolio 模块，无法执行回测。请在策略配置中添加 portfolio。"
            )

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
        """返回回测每日每指数信号与收益。

        口径与实时信号保持一致：signal_score 为综合得分，target_weight 为信号目标仓位权重。
        """
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
                    target_weight=getattr(r, "target_weight", None),
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

            # 状态流转统一走仓库（与 RunService 模式一致，避免绕过 repository）
            self._backtest_repo.mark_running(backtest_id)

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

    # ── 统一回测主循环 ────────────────────────────────────────────────────

    def _run_backtest_loop(
        self,
        backtest_id: str,
        row: BacktestRunModel,
        config: StrategyConfig,
    ) -> None:
        """统一回测主循环，替代旧的 signal/allocation 双分支。

        流程：
        1. 准备数据（标的、交易日、行情、估值，回望窗口按因子 lookback 推导）
        2. 预计算全区间因子值
        3. 逐日执行引擎管线
        4. 按仓位/排名计算组合收益（毛收益，不扣除交易成本）
        5. 计算基准收益（如启用）
        6. 写入每日结果和指数结果
        7. 计算汇总绩效指标
        """
        universe, index_codes, trading_dates, all_bars, all_valuation, all_macro = (
            self._prepare_backtest_data(row)
        )

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

        # 预计算所有因子值（含择时代理指数）
        precomputed = self._factor_provider.precompute_backtest_factors(
            config, trading_dates, factor_index_codes, all_bars, all_valuation, all_macro
        )

        # 记录实际回望天数与因子预热期（"前 N 个交易日因子数据不足"提示），
        # 写入回测 params 元数据，随 mark_success 一并持久化
        lookback_days = self._get_lookback_days()
        warmup_trading_days = self._estimate_warmup_trading_days(
            precomputed,
            trading_dates,
            factor_index_codes,
            FactorProvider.collect_required_factor_ids(config),
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
            "回望=%s天 预热=%s个交易日 执行模型=T+1开盘 基准=%s",
            backtest_id,
            row.strategy_id,
            trading_dates[0],
            trading_dates[-1],
            len(index_codes),
            lookback_days,
            warmup_trading_days,
            benchmark_index_code if enable_benchmark else "禁用",
        )

        # ORM 行列表：用于 checkpoint 提交后释放对象引用
        daily_results: list[BacktestDailyResultModel] = []
        # 账户累积器：累计净值/回撤等账务状态由领域对象跟踪，
        # 主循环只负责生成当日持仓与收益（执行与绩效关注点分离）
        accumulator = BacktestDayAccumulator()
        prev_positions: dict[str, float] = {}
        last_rebalance_date: date | None = None
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
        cached_universe = build_universe_items([{"index_code": c, "name_cn": c} for c in codes])
        cached_metadata = {code: {"name_cn": code, "category": "broad_index"} for code in codes}

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
                cached_universe=cached_universe,
                cached_metadata=cached_metadata,
            )

            # 检查调仓日
            should_rebalance = self._check_rebalance(config, trade_date, last_rebalance_date)

            # 执行引擎（回测模式跳过详细的 StrategyResult 构建以提升性能）
            result = self._engine.run(config, context, include_details=False)

            next_date = trading_dates[i + 1] if i + 1 < len(trading_dates) else None

            # 确定持仓与收益（T+1 开盘执行模型：T 日收盘出信号，T+1 日开盘成交，无滑点）
            if should_rebalance:
                # 调仓日：旧仓位持有至 T+1 开盘（隔夜段），新仓位自 T+1 开盘买入（日内段），
                # 当日收益 = 旧仓位 × [close_T, open_{T+1}] + 新仓位 × [open_{T+1}, close_{T+1}]
                new_positions = dict(result.positions) if result.positions else {}
                portfolio_return = compute_rebalance_day_return(
                    prev_positions, new_positions, trade_date, next_date, all_bars
                )
                positions = new_positions
                day_total_exposure = round(sum(positions.values()), 4)
                day_cash_ratio = round(1.0 - day_total_exposure, 4)
                # 换手率基于新旧目标仓位计算
                turnover = 0.0
                if prev_positions and new_positions:
                    turnover = compute_turnover(prev_positions, new_positions)
                last_rebalance_date = trade_date
            else:
                # 非调仓日：沿用上次持仓，按收盘对收盘计算收益
                positions = dict(prev_positions)
                day_total_exposure = round(sum(positions.values()), 4)
                day_cash_ratio = round(1.0 - day_total_exposure, 4)
                turnover = 0.0
                portfolio_return = compute_allocation_return(
                    positions, trade_date, next_date, all_bars
                )

            # 持仓统计
            high_cnt = mid_cnt = low_cnt = 0
            has_positions = bool(positions)
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
                high_signal_count=0,  # 占位，下面从 _write_index_results 获取实际值
                mid_signal_count=0,
                low_signal_count=0,
                timing_regime=result.timing.regime if result.timing else None,
                total_exposure=day_total_exposure,
                cash_ratio=day_cash_ratio,
                positions=positions if positions else None,
                benchmark_return=round(benchmark_ret, 4) if benchmark_ret is not None else None,
                turnover=round(turnover, 4) if turnover > 0 else None,
            )
            self._backtest_repo.add_daily_result(daily_row)
            daily_results.append(daily_row)

            # 写入指数结果并获取信号计数
            high_cnt, mid_cnt, low_cnt, day_pos_count, day_pos_positive = self._write_index_results(
                backtest_id,
                trade_date,
                next_date,
                universe,
                result,
                all_bars,
                result.positions if result.positions else {},
                timing_regime=result.timing.regime if result.timing else None,
                scoring_mode=config.score.scoring_mode,
            )
            # 更新每日行的信号计数
            daily_row.high_signal_count = high_cnt
            daily_row.mid_signal_count = mid_cnt
            daily_row.low_signal_count = low_cnt
            total_in_pos_count += day_pos_count
            total_in_pos_positive += day_pos_positive

            logger.debug(
                "[backtest] 日结果: backtest_id=%s date=%s regime=%s exposure=%s "
                "持仓=%s 收益=%s%% 高/中/低=%s/%s/%s",
                backtest_id,
                trade_date,
                result.timing.regime if result.timing else None,
                day_total_exposure,
                {k: round(v, 4) for k, v in positions.items()},
                round(portfolio_return, 4),
                high_cnt,
                mid_cnt,
                low_cnt,
            )

            prev_positions = positions

            # 每 10% 交易日更新一次进度（最少 1 天触发）
            if total_dates > 0:
                new_progress = int((i + 1) / total_dates * 100)
                if new_progress - last_progress >= 10:
                    self._backtest_repo.update_progress(backtest_id, new_progress)
                    last_progress = new_progress
                    logger.info(
                        "[backtest] 进度: backtest_id=%s %s/%s (%s%%) date=%s "
                        "累计收益=%s%% 高/中/低=%s/%s/%s",
                        backtest_id,
                        i + 1,
                        total_dates,
                        new_progress,
                        trade_date,
                        round(cumulative_return_pct, 2),
                        high_cnt,
                        mid_cnt,
                        low_cnt,
                    )

            # 每 100 天 checkpoint 提交一次：
            # - 释放事务大小，避免长区间回测的单一巨大事务
            # - 中途失败时已提交的分段结果保留，前端可查看部分权益曲线
            # - progress 裸 SQL 更新随本次 commit 一起对其它连接可见
            if (i + 1) % 100 == 0:
                self._db.flush()
                self._db.commit()
                daily_results.clear()  # 释放 ORM 对象，账户累积器已保留关键数据

        self._db.flush()

        # 计算汇总指标（使用账户累积器与内存中的信号准确率，避免依赖已 flush 的 ORM 对象）
        metrics = self._compute_summary_metrics(
            accumulator,
            benchmark_returns,
            total_in_pos_count=total_in_pos_count,
            total_in_pos_positive=total_in_pos_positive,
        )
        # 收集结构化警告：预热期 / 因子缺失 / 数据缺口 / 基准缺失，
        # 随成功状态一并持久化，前端轮询时按 key 去重弹窗。
        run_warnings: list[BacktestWarning] = []
        if warmup_trading_days > 0:
            run_warnings.append(
                BacktestWarning(
                    level="warning",
                    code="WARMUP",
                    message=(
                        f"回测前 {warmup_trading_days} 个交易日长周期因子数据不足"
                        "（预热期），前段信号与绩效参考价值有限"
                    ),
                )
            )
        run_warnings.extend(
            self._collect_missing_factor_warnings(
                precomputed,
                trading_dates,
                factor_index_codes,
                FactorProvider.collect_required_factor_ids(config),
            )
        )
        run_warnings.extend(
            self._collect_data_gap_warnings(trading_dates, index_codes, all_bars)
        )
        if enable_benchmark:
            bench_missing = [
                d for d in trading_dates if (benchmark_index_code, d) not in all_bars
            ]
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
            "[backtest] 回测完成: backtest_id=%s 累计收益=%s%% 最大回撤=%s%% 夏普=%s "
            "胜率=%s%% 信号准确率=%s%%",
            backtest_id,
            metrics.get("cumulative_return_pct", 0.0),
            metrics.get("max_drawdown_pct", 0.0),
            metrics.get("sharpe_ratio", 0.0),
            metrics.get("win_rate_pct", 0.0),
            metrics.get("signal_accuracy_pct", 0.0),
        )
        self._backtest_repo.mark_success(
            backtest_id,
            metrics,
            warnings=[w.model_dump() for w in run_warnings],
        )

    # ── 数据准备 ───────────────────────────────────────────────────────────

    def _prepare_backtest_data(
        self, row: BacktestRunModel
    ) -> tuple[
        list[dict[str, Any]], list[str], list[date], dict, dict, dict[str, dict[str, float]]
    ]:
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
        signal_positions: dict[str, float],
        timing_regime: str | None = None,
        scoring_mode: str = "absolute",
    ) -> tuple[int, int, int, int, int]:
        """写入每日每指数的回测结果，使用与实时一致的信号等级判定逻辑。

        与实时 `_build_strategy_results` 共用同一 `determine_signal_level` 输入：
        score=当日综合得分、target_weight=当日目标权重（result.positions）、
        timing_regime=当日择时 regime、scoring_mode=策略评分模式。

        Returns:
            (high_cnt, mid_cnt, low_cnt, in_portfolio_count, in_portfolio_positive_count) 元组。
        """
        score_map = result.scores
        high = mid = low = 0
        in_pos_count = 0
        in_pos_positive = 0

        for item in universe:
            code = item["index_code"]
            target_weight = signal_positions.get(code, 0.0)
            score = score_map.get(code, 0.0)

            level, _ = determine_signal_level(
                score=score,
                target_weight=target_weight,
                has_positions=bool(signal_positions),
                timing_regime=timing_regime,
                scoring_mode=scoring_mode,
            )
            if level == "HIGH":
                high += 1
            elif level == "MID":
                mid += 1
            else:
                low += 1

            in_portfolio = target_weight > 0
            signal_score = round(score, 2)

            idx_ret = None
            if next_date and in_portfolio:
                idx_ret = self._get_index_return(code, trade_date, next_date, all_bars)
                in_pos_count += 1
                if idx_ret is not None and idx_ret > 0:
                    in_pos_positive += 1

            self._backtest_repo.add_index_result(
                BacktestIndexResultModel(
                    backtest_id=backtest_id,
                    trade_date=trade_date,
                    index_code=code,
                    signal_score=signal_score,
                    signal_level=level,
                    in_portfolio=in_portfolio,
                    index_return=idx_ret,
                    target_weight=round(target_weight, 4),
                )
            )

        return high, mid, low, in_pos_count, in_pos_positive

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
        accumulator: BacktestDayAccumulator,
        benchmark_returns: list[float],
        total_in_pos_count: int = 0,
        total_in_pos_positive: int = 0,
    ) -> dict[str, Any]:
        """计算回测汇总绩效指标，集成专业指标和基准对比。

        Args:
            accumulator: 账户累积器（每日收益 + 有持仓收益）。
            benchmark_returns: 基准日收益率序列。
            total_in_pos_count: 持仓指数总次数（主循环累积）。
            total_in_pos_positive: 持仓指数正收益总次数（主循环累积）。
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
        )

        # 信号准确率（从主循环累积的内存计数器获取，无需 DB 查询）
        signal_accuracy_pct = 0.0
        if total_in_pos_count > 0:
            signal_accuracy_pct = round(total_in_pos_positive / total_in_pos_count * 100, 2)

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
        ).model_dump()

    # ── 辅助方法 ───────────────────────────────────────────────────────────

    def _resolve_index_universe(self, universe_filter: dict[str, Any]) -> list[dict[str, Any]]:
        """根据 universe_filter 查询回测指数列表。

        仅返回活跃指数（is_active=True），排除已退市/停发的指数，
        避免回测中的幸存者偏差。读取走仓库，过滤走领域纯函数。
        """
        rows = BenchmarkIndexRepository(self._db).find_active()
        rows = filter_universe_rows(rows, universe_filter)
        return build_universe_items(rows)

    def _get_index_trading_dates(
        self, start: date, end: date, index_codes: list[str]
    ) -> list[date]:
        """从 index_daily_bar 中提取区间内的交易日列表（读取走仓库）。"""
        return self._index_bar_repo.find_trading_dates(start, end, index_codes)

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
            affected_codes: set[str] = set()
            first_missing: date | None = None
            last_missing: date | None = None
            for trade_date in trading_dates:
                day_values = precomputed.get(trade_date, {})
                missing_codes = [
                    code for code in index_codes if day_values.get((code, factor_id)) is None
                ]
                if missing_codes:
                    missing_days += 1
                    affected_codes.update(missing_codes)
                    if first_missing is None:
                        first_missing = trade_date
                    last_missing = trade_date
            if missing_days > 0:
                warnings.append(
                    BacktestWarning(
                        level="warning",
                        code="MISSING_FACTOR",
                        message=(
                            f"因子 {factor_id} 在 {missing_days} 个交易日存在缺失"
                            f"（{first_missing}~{last_missing}，"
                            f"涉及指数 {sorted(affected_codes)[:5]}{' 等' if len(affected_codes) > 5 else ''}）"
                        ),
                    )
                )
        return warnings[:5]

    def _collect_data_gap_warnings(
        self,
        trading_dates: list[date],
        index_codes: list[str],
        all_bars: dict,
    ) -> list[BacktestWarning]:
        """按指数统计回测区间内缺行情数据的交易日，生成 DATA_GAP 警告。

        仅透传提示，不修正组合收益（B10 的数值修复另行跟踪）。

        Args:
            trading_dates: 回测交易日列表。
            index_codes: 回测标的指数代码列表。
            all_bars: 预加载行情，key=(index_code, trade_date)。

        Returns:
            结构化警告列表，最多 5 条（按指数）。
        """
        warnings: list[BacktestWarning] = []
        for code in index_codes:
            missing = [d for d in trading_dates if (code, d) not in all_bars]
            if missing:
                warnings.append(
                    BacktestWarning(
                        level="warning",
                        code="DATA_GAP",
                        message=(
                            f"指数 {code} 在回测区间内有 {len(missing)} 个交易日缺行情数据"
                            f"（{missing[0]}~{missing[-1]}），相关日期的因子与收益按缺失处理"
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
    ) -> int:
        """估算回测前段因子数据不足的预热交易日数。

        对策略实际需要的每个因子，找到首个"全部指数均有有效值"的交易日，
        取各因子首次全覆盖日期的最大值作为预热期。从未达到全指数覆盖的
        因子（如某指数无估值数据导致的永久缺失）不参与统计，避免把结构性
        数据缺口误判为回望不足（后者由 B10 数据质量检查单独处理）。

        Args:
            precomputed: 预计算因子值，date → (index_code, factor_id) → 数值。
            trading_dates: 回测交易日列表。
            index_codes: 参与因子计算的指数代码列表（含择时代理指数）。
            factor_ids: 策略实际引用的因子 ID 列表。

        Returns:
            前 N 个交易日中因子数据不足的天数；无预热期时返回 0。
        """
        if not factor_ids or not trading_dates:
            return 0
        warmup = 0
        for factor_id in factor_ids:
            first_full: int | None = None
            for i, trade_date in enumerate(trading_dates):
                day_values = precomputed.get(trade_date, {})
                if all(day_values.get((code, factor_id)) is not None for code in index_codes):
                    first_full = i
                    break
            if first_full is not None:
                warmup = max(warmup, first_full)
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
        """将 ORM 行转换为 BacktestDetail（含结构化警告）。"""
        summary = self._row_to_summary(row)
        try:
            warnings = [BacktestWarning(**w) for w in (row.warnings or [])]
        except Exception:
            warnings = []
        return BacktestDetail(
            **summary.model_dump(),
            universe_filter=row.universe_filter or {"mode": "all"},
            params=row.params,
            warnings=warnings,
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
            ValueError: 策略不存在、未配置 portfolio 或两策略相同。
        """
        comparison_id = str(uuid4())
        now = utcnow()

        # 校验两个策略不同
        if req.strategy_a_id == req.strategy_b_id:
            raise ValueError("请选择两个不同的策略进行对比")

        # 校验两个策略都存在且配置了 portfolio
        config_svc = StrategyConfigService(self._db)
        for sid in [req.strategy_a_id, req.strategy_b_id]:
            cfg = config_svc.get_parsed_config(sid)
            if cfg is None:
                raise ValueError(f"策略 {sid} 配置不存在")
            if cfg.portfolio is None:
                raise ValueError(f"策略 {sid} 未配置 portfolio 模块，无法执行回测")

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
        comp.started_at = utcnow()
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

        # 读取各项指标并计算差值
        a_cum = _get("cumulative_return_pct", ma)
        b_cum = _get("cumulative_return_pct", mb)
        a_ann = _get("annualized_return_pct", ma)
        b_ann = _get("annualized_return_pct", mb)
        a_dd = _get("max_drawdown_pct", ma)
        b_dd = _get("max_drawdown_pct", mb)
        a_sharpe = _get("sharpe_ratio", ma)
        b_sharpe = _get("sharpe_ratio", mb)
        a_sortino = _get("sortino_ratio", ma)
        b_sortino = _get("sortino_ratio", mb)
        a_calmar = _get("calmar_ratio", ma)
        b_calmar = _get("calmar_ratio", mb)
        a_win = _get("win_rate_pct", ma)
        b_win = _get("win_rate_pct", mb)
        a_sig = _get("signal_accuracy_pct", ma)
        b_sig = _get("signal_accuracy_pct", mb)
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
            "cumulative_return_diff_pct": round(a_cum - b_cum, 2),
            "annualized_return_diff_pct": round(a_ann - b_ann, 2),
            "max_drawdown_diff_pct": round(a_dd - b_dd, 2),
            "sharpe_diff": round(a_sharpe - b_sharpe, 4),
            "sortino_diff": round(a_sortino - b_sortino, 4),
            "calmar_diff": round(a_calmar - b_calmar, 4),
            "win_rate_diff_pct": round(a_win - b_win, 2),
            "signal_accuracy_diff_pct": round(a_sig - b_sig, 2),
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
