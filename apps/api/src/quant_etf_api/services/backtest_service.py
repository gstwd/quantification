from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    BacktestDailyResultModel,
    BacktestEtfResultModel,
    BacktestRunModel,
    EtfUniverseModel,
    IndexValuationModel,
)
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.etf_daily_bar import EtfDailyBarRepository
from quant_etf_api.infra.db.repositories.etf_daily_share import EtfDailyShareRepository
from quant_etf_api.infra.db.repositories.etf_universe import EtfUniverseRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.domain.strategies.models import AllocationPlan, AssetRanking, TimingSignal
from quant_etf_api.plugins.base import StrategyContextData, StrategyResult
from quant_etf_api.plugins.registry import StrategyRegistry
from quant_etf_api.domain.common.bar_metrics import (
    calc_5d_return_etf,
    calc_5d_return_index,
    calc_volume_ratio_20d,
)
from quant_etf_api.schemas.backtest import (
    BacktestCreateRequest,
    BacktestDetail,
    BacktestDailyResult,
    BacktestEtfResult,
    BacktestMetrics,
    BacktestSummary,
)

logger = logging.getLogger(__name__)


class BacktestService:
    """回测引擎服务，负责创建、执行和查询回测任务。"""

    def __init__(
        self,
        db: Session,
        registry: StrategyRegistry,
        backtest_repo: BacktestRepository | None = None,
        bar_repo: EtfDailyBarRepository | None = None,
        share_repo: EtfDailyShareRepository | None = None,
        index_bar_repo: IndexDailyBarRepository | None = None,
        universe_repo: EtfUniverseRepository | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._backtest_repo = backtest_repo or BacktestRepository(db)
        self._bar_repo = bar_repo or EtfDailyBarRepository(db)
        self._share_repo = share_repo or EtfDailyShareRepository(db)
        self._index_bar_repo = index_bar_repo or IndexDailyBarRepository(db)
        self._universe_repo = universe_repo or EtfUniverseRepository(db)

    def create_backtest(self, req: BacktestCreateRequest) -> BacktestSummary:
        """创建回测记录，状态为 pending，立即返回。"""
        backtest_id = str(uuid4())
        now = datetime.now(timezone.utc)
        universe_filter = (
            {"mode": "all"}
            if req.universe_mode == "all"
            else {"mode": "subset", "etf_codes": req.etf_codes}
        )
        # 将 backtest_mode 存入 params 字段，避免新增数据库列
        params = dict(req.params) if req.params else {}
        params["_backtest_mode"] = req.backtest_mode

        try:
            row = BacktestRunModel(
                backtest_id=backtest_id,
                strategy_id=req.strategy_id,
                start_date=req.start_date,
                end_date=req.end_date,
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

    def list_backtests(self, offset: int = 0, limit: int = 50) -> tuple[list[BacktestSummary], int]:
        """分页返回回测列表，按创建时间倒序。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        try:
            rows, total = self._backtest_repo.find_all(offset=offset, limit=limit)
            items = [self._row_to_summary(r) for r in rows]
            return items, total
        except Exception:
            logger.warning("list_backtests DB query failed", exc_info=True)
            return [], 0

    def get_backtest(self, backtest_id: str) -> BacktestDetail | None:
        """返回回测详情，含配置信息。"""
        try:
            row = self._backtest_repo.find_by_id(backtest_id)
            if row is None:
                return None
            return self._row_to_detail(row)
        except Exception:
            logger.warning("get_backtest DB query failed", exc_info=True)
            return None

    def get_daily_results(self, backtest_id: str) -> list[BacktestDailyResult]:
        """返回回测每日组合绩效，按日期升序。"""
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
                )
                for r in rows
            ]
        except Exception:
            logger.warning("get_daily_results DB query failed", exc_info=True)
            return []

    def get_etf_results(
        self, backtest_id: str, etf_code: str | None = None
    ) -> list[BacktestEtfResult]:
        """返回回测每日每 ETF 信号与收益，可按 ETF 过滤。"""
        try:
            rows = self._backtest_repo.find_etf_results(backtest_id, etf_code=etf_code)
            return [
                BacktestEtfResult(
                    trade_date=r.trade_date,
                    etf_code=r.etf_code,
                    signal_score=r.signal_score,
                    signal_level=r.signal_level,
                    in_portfolio=r.in_portfolio,
                    etf_return=r.etf_return,
                )
                for r in rows
            ]
        except Exception:
            logger.warning("get_etf_results DB query failed", exc_info=True)
            return []

    def run_backtest(self, backtest_id: str) -> None:
        """回测执行入口，在后台线程中同步运行。

        支持双模式：
        - signal 模式：逐日调用 run_for_universe()，HIGH/MID/LOW 信号评分
        - allocation 模式：逐日调用决策管线（择时 → 排名 → 分配），按仓位比例持有
        """
        try:
            row = self._backtest_repo.find_by_id(backtest_id)
            if row is None:
                logger.error("run_backtest: backtest_id %s not found", backtest_id)
                return

            # 标记为执行中
            row.status = "running"
            row.started_at = datetime.now(timezone.utc)
            self._db.commit()

            plugin = self._registry.get(row.strategy_id)
            if plugin is None:
                raise ValueError(f"策略插件 {row.strategy_id} 未注册")

            # 确定回测标的范围
            universe = self._resolve_universe(row.universe_filter)
            if not universe:
                raise ValueError("回测标的范围为空，请检查 universe_filter 配置")

            etf_codes = [u["etf_code"] for u in universe]

            # 获取回测区间内的所有交易日
            trading_dates = self._get_trading_dates(row.start_date, row.end_date, etf_codes)
            if not trading_dates:
                raise ValueError(f"区间 {row.start_date} ~ {row.end_date} 内无交易日数据")

            # 预加载所有需要的行情数据（批量，避免逐日 N+1 查询）
            all_bars = self._load_all_bars(trading_dates, etf_codes)
            all_shares = self._load_all_shares(trading_dates, etf_codes)
            all_index_bars = self._load_all_index_bars(trading_dates)

            # 判断回测模式：从 params 中读取，或自动检测插件能力
            backtest_mode = (row.params or {}).get("_backtest_mode", "signal")
            use_allocation = backtest_mode == "allocation" and hasattr(plugin, "assess_market_timing")

            if use_allocation:
                self._run_allocation_backtest(
                    backtest_id, row, plugin, universe, etf_codes,
                    trading_dates, all_bars, all_shares, all_index_bars,
                )
            else:
                self._run_signal_backtest(
                    backtest_id, row, plugin, universe, etf_codes,
                    trading_dates, all_bars, all_shares, all_index_bars,
                )

        except Exception as exc:
            self._db.rollback()
            logger.exception("run_backtest failed for %s", backtest_id)
            try:
                self._backtest_repo.mark_failed(backtest_id, str(exc))
            except Exception:
                self._db.rollback()

    def _run_signal_backtest(
        self,
        backtest_id: str,
        row: BacktestRunModel,
        plugin: Any,
        universe: list[dict[str, Any]],
        etf_codes: list[str],
        trading_dates: list[date],
        all_bars: dict,
        all_shares: dict,
        all_index_bars: dict,
    ) -> None:
        """信号评分模式回测（原有逻辑）。"""
        daily_results: list[BacktestDailyResultModel] = []
        cumulative = 1.0
        peak = 1.0

        for i, trade_date in enumerate(trading_dates):
            context = self._build_historical_context(
                trade_date, etf_codes, all_bars, all_shares, all_index_bars
            )
            results = plugin.run_for_universe(trade_date, universe, context, row.params)

            next_date = trading_dates[i + 1] if i + 1 < len(trading_dates) else None
            portfolio_return, high_cnt, mid_cnt, low_cnt = self._compute_portfolio_return(
                results, next_date, all_bars, row.weighting
            )

            cumulative *= 1 + portfolio_return / 100
            cumulative_return_pct = (cumulative - 1) * 100
            peak = max(peak, cumulative)
            drawdown = (cumulative / peak - 1) * 100

            daily_row = BacktestDailyResultModel(
                backtest_id=backtest_id,
                trade_date=trade_date,
                portfolio_return=round(portfolio_return, 4),
                cumulative_return=round(cumulative_return_pct, 4),
                drawdown=round(drawdown, 4),
                high_signal_count=high_cnt,
                mid_signal_count=mid_cnt,
                low_signal_count=low_cnt,
            )
            self._db.add(daily_row)
            daily_results.append(daily_row)

            for r in results:
                in_portfolio = r.signal_level == "HIGH"
                etf_ret = None
                if next_date and in_portfolio:
                    etf_ret = self._get_etf_return(r.etf_code, trade_date, next_date, all_bars)
                self._db.add(
                    BacktestEtfResultModel(
                        backtest_id=backtest_id,
                        trade_date=trade_date,
                        etf_code=r.etf_code,
                        signal_score=r.signal_score,
                        signal_level=r.signal_level,
                        in_portfolio=in_portfolio,
                        etf_return=etf_ret,
                    )
                )

        self._db.flush()
        metrics = self._compute_summary_metrics(daily_results)
        self._backtest_repo.mark_success(backtest_id, metrics)

    def _run_allocation_backtest(
        self,
        backtest_id: str,
        row: BacktestRunModel,
        plugin: Any,
        universe: list[dict[str, Any]],
        etf_codes: list[str],
        trading_dates: list[date],
        all_bars: dict,
        all_shares: dict,
        all_index_bars: dict,
    ) -> None:
        """资产配置模式回测：择时 → 资产轮动 → 仓位分配。"""
        # 加载指数估值数据和 ETF-指数映射
        all_valuation = self._load_all_valuation(trading_dates)
        etf_index_map = self._load_etf_index_map(etf_codes)

        daily_results: list[BacktestDailyResultModel] = []
        cumulative = 1.0
        peak = 1.0

        for i, trade_date in enumerate(trading_dates):
            # 构建增强版上下文（含估值和映射数据）
            context = self._build_allocation_context(
                trade_date, etf_codes, all_bars, all_shares, all_index_bars,
                all_valuation, etf_index_map,
            )

            # 运行决策管线
            timing: TimingSignal = plugin.assess_market_timing(trade_date, context, row.params)
            rankings: list[AssetRanking] = plugin.rank_assets(trade_date, universe, context, row.params)
            plan: AllocationPlan = plugin.allocate_positions(timing, rankings, row.params)

            # 计算 T+1 收益
            next_date = trading_dates[i + 1] if i + 1 < len(trading_dates) else None
            portfolio_return = self._compute_allocation_return(plan, trade_date, next_date, all_bars)

            cumulative *= 1 + portfolio_return / 100
            cumulative_return_pct = (cumulative - 1) * 100
            peak = max(peak, cumulative)
            drawdown = (cumulative / peak - 1) * 100

            # 将分配信息存入 high/mid/low 字段（兼容现有 schema）
            held_count = len(plan.positions)
            regime_map = {"offensive": 0, "neutral": 1, "defensive": 2}
            daily_row = BacktestDailyResultModel(
                backtest_id=backtest_id,
                trade_date=trade_date,
                portfolio_return=round(portfolio_return, 4),
                cumulative_return=round(cumulative_return_pct, 4),
                drawdown=round(drawdown, 4),
                high_signal_count=held_count,
                mid_signal_count=regime_map.get(timing.regime, 1),
                low_signal_count=0,
            )
            self._db.add(daily_row)
            daily_results.append(daily_row)

            # 写入每 ETF 结果
            for item in universe:
                code = item["etf_code"]
                target_weight = plan.positions.get(code, 0.0)
                in_portfolio = target_weight > 0
                etf_ret = None
                if next_date and in_portfolio:
                    etf_ret = self._get_etf_return(code, trade_date, next_date, all_bars)
                self._db.add(
                    BacktestEtfResultModel(
                        backtest_id=backtest_id,
                        trade_date=trade_date,
                        etf_code=code,
                        signal_score=round(target_weight * 100, 2),
                        signal_level=timing.regime,
                        in_portfolio=in_portfolio,
                        etf_return=etf_ret,
                    )
                )

        self._db.flush()
        metrics = self._compute_summary_metrics(daily_results)
        self._backtest_repo.mark_success(backtest_id, metrics)

    # ── 分配模式辅助方法 ────────────────────────────────────────────────────

    def _load_all_valuation(
        self, trading_dates: list[date]
    ) -> dict[tuple[str, date], Any]:
        """批量加载回测区间的指数估值数据，返回 (index_code, trade_date) → row 映射。"""
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

    def _load_etf_index_map(self, etf_codes: list[str]) -> dict[str, str]:
        """构建 ETF 代码到跟踪指数代码的映射。"""
        rows = (
            self._db.query(EtfUniverseModel.etf_code, EtfUniverseModel.tracking_index_code)
            .filter(EtfUniverseModel.etf_code.in_(etf_codes))
            .all()
        )
        return {r[0]: r[1] for r in rows if r[1]}

    def _build_allocation_context(
        self,
        trade_date: date,
        etf_codes: list[str],
        all_bars: dict,
        all_shares: dict,
        all_index_bars: dict,
        all_valuation: dict,
        etf_index_map: dict[str, str],
    ) -> StrategyContextData:
        """为资产配置模式构建增强版上下文，含估值和映射数据。"""
        # 基础上下文
        context = self._build_historical_context(
            trade_date, etf_codes, all_bars, all_shares, all_index_bars
        )

        # 添加指数估值数据
        index_valuation: dict[str, dict[str, Any]] = {}
        for idx_code in set(etf_index_map.values()):
            val_row = all_valuation.get((idx_code, trade_date))
            if val_row:
                index_valuation[idx_code] = {
                    "pe_percentile": val_row.pe_percentile,
                    "pb_percentile": val_row.pb_percentile,
                    "pe": val_row.pe,
                    "pb": val_row.pb,
                }
        context.extra["index_valuation"] = index_valuation
        context.extra["etf_index_map"] = etf_index_map

        # 补充 return_20d 和 return_5d 到 etf_bars
        etf_bars = context.extra.get("etf_bars", {})
        for code in etf_codes:
            if code not in etf_bars:
                etf_bars[code] = {}
            bars = etf_bars[code]
            # 计算 20 日收益
            if "return_20d" not in bars:
                bars["return_20d"] = self._calc_nd_return_from_bars(code, trade_date, all_bars, 20)
            # 计算 5 日收益
            if "return_5d" not in bars:
                bars["return_5d"] = self._calc_nd_return_from_bars(code, trade_date, all_bars, 5)
            # 计算 MA60
            if "ma60" not in bars:
                bars["ma60"] = self._calc_ma_from_bars(code, trade_date, all_bars, 60)
        context.extra["etf_bars"] = etf_bars

        return context

    def _calc_nd_return_from_bars(
        self, etf_code: str, trade_date: date, all_bars: dict, n: int
    ) -> float | None:
        """从预加载数据中计算 N 日收益率。"""
        today_bar = all_bars.get((etf_code, trade_date))
        if today_bar is None or today_bar.close_price is None:
            return None
        past_closes = sorted(
            [(dt, v.close_price) for (code, dt), v in all_bars.items()
             if code == etf_code and dt < trade_date and v.close_price is not None],
            key=lambda x: x[0],
        )
        if len(past_closes) < n:
            return None
        base = past_closes[-n][1]
        if base <= 0:
            return None
        return round((today_bar.close_price / base - 1) * 100, 4)

    def _calc_ma_from_bars(
        self, etf_code: str, trade_date: date, all_bars: dict, n: int
    ) -> float | None:
        """从预加载数据中计算 N 日均线。"""
        closes = sorted(
            [v.close_price for (code, dt), v in all_bars.items()
             if code == etf_code and dt <= trade_date and v.close_price is not None],
        )
        if len(closes) < n:
            return None
        return round(sum(closes[-n:]) / n, 4)

    def _compute_allocation_return(
        self,
        plan: AllocationPlan,
        trade_date: date,
        next_date: date | None,
        all_bars: dict,
    ) -> float:
        """按仓位分配方案计算组合 T+1 收益。"""
        if next_date is None or not plan.positions:
            return 0.0
        total_return = 0.0
        for code, weight in plan.positions.items():
            ret = self._get_etf_return(code, trade_date, next_date, all_bars)
            if ret is not None:
                total_return += weight * ret
        return round(total_return, 4)

    # ── 通用辅助方法 ────────────────────────────────────────────────────────

    def _resolve_universe(self, universe_filter: dict[str, Any]) -> list[dict[str, Any]]:
        """根据 universe_filter 查询回测标的列表。"""
        if universe_filter.get("mode") == "subset":
            codes = universe_filter.get("etf_codes", [])
            if codes:
                rows = self._universe_repo.find_by_codes(codes)
                return [{"etf_code": r.etf_code, "name_cn": r.name_cn} for r in rows]
        rows = self._universe_repo.find_all_active()
        return [{"etf_code": r.etf_code, "name_cn": r.name_cn} for r in rows]

    def _get_trading_dates(self, start: date, end: date, etf_codes: list[str]) -> list[date]:
        """从 etf_daily_bar 中提取区间内的交易日列表（升序）。"""
        return self._bar_repo.get_trading_dates(etf_codes, start, end)

    def _load_all_bars(
        self, trading_dates: list[date], etf_codes: list[str]
    ) -> dict[tuple[str, date], Any]:
        """批量加载回测区间及前 25 日的行情数据，返回 (etf_code, trade_date) → row 映射。"""
        if not trading_dates:
            return {}
        lookback_start = trading_dates[0] - timedelta(days=35)
        return self._bar_repo.find_by_codes_date_range(etf_codes, lookback_start, trading_dates[-1])

    def _load_all_shares(
        self, trading_dates: list[date], etf_codes: list[str]
    ) -> dict[tuple[str, date], Any]:
        """批量加载回测区间的份额数据，返回 (etf_code, trade_date) → row 映射。"""
        if not trading_dates:
            return {}
        return self._share_repo.find_by_codes_date_range(
            etf_codes, trading_dates[0], trading_dates[-1]
        )

    def _load_all_index_bars(self, trading_dates: list[date]) -> dict[tuple[str, date], Any]:
        """批量加载回测区间及前 10 日的指数行情数据。"""
        if not trading_dates:
            return {}
        lookback_start = trading_dates[0] - timedelta(days=15)
        return self._index_bar_repo.find_all_date_range(lookback_start, trading_dates[-1])

    def _build_historical_context(
        self,
        trade_date: date,
        etf_codes: list[str],
        all_bars: dict[tuple[str, date], Any],
        all_shares: dict[tuple[str, date], Any],
        all_index_bars: dict[tuple[str, date], Any],
    ) -> StrategyContextData:
        """
        为指定交易日构建 StrategyContextData，注入真实历史数据到 context.extra["etf_bars"]。
        插件优先读取 extra["etf_bars"]，无数据时回退到内置 stub。
        """
        # 构建 benchmark_changes 和 index_5d_return
        benchmark_changes: dict[str, float] = {}
        index_5d_return: dict[str, float] = {}
        for index_code in ("000300", "000016", "000905"):
            bar = all_index_bars.get((index_code, trade_date))
            if bar and bar.change_pct is not None:
                benchmark_changes[index_code] = bar.change_pct
            # 计算指数近 5 日收益（用 close_price 差值近似）
            index_5d_return[index_code] = calc_5d_return_index(
                index_code, trade_date, all_index_bars
            )

        # 构建 share_changes
        share_changes: dict[str, dict[str, float | None]] = {}
        for code in etf_codes:
            share_row = all_shares.get((code, trade_date))
            share_changes[code] = {
                "share_delta_pct": share_row.shares_delta_pct if share_row else None
            }

        # 构建 etf_bars（供插件读取真实历史量比、涨跌幅等）
        etf_bars: dict[str, dict[str, Any]] = {}
        for code in etf_codes:
            bar = all_bars.get((code, trade_date))
            if bar is None:
                continue
            volume_ratio_20d = calc_volume_ratio_20d(code, trade_date, all_bars)
            etf_5d_return = calc_5d_return_etf(code, trade_date, all_bars)
            etf_bars[code] = {
                "volume_ratio_20d": volume_ratio_20d,
                "change_pct": bar.change_pct or 0.0,
                "etf_5d_return": etf_5d_return,
                "close_price": bar.close_price,
            }

        return StrategyContextData(
            benchmark_changes=benchmark_changes,
            share_changes=share_changes,
            extra={"etf_bars": etf_bars, "index_5d_return": index_5d_return},
        )

    def _get_etf_return(
        self, etf_code: str, trade_date: date, next_date: date, all_bars: dict
    ) -> float | None:
        """获取 ETF 的 T+1 日收益率（%）。"""
        today_bar = all_bars.get((etf_code, trade_date))
        next_bar = all_bars.get((etf_code, next_date))
        if today_bar is None or next_bar is None:
            return None
        if (
            today_bar.close_price is None
            or next_bar.close_price is None
            or today_bar.close_price == 0
        ):
            return None
        return round((next_bar.close_price / today_bar.close_price - 1) * 100, 4)

    def _compute_portfolio_return(
        self,
        results: list[StrategyResult],
        next_date: date | None,
        all_bars: dict,
        weighting: str,
    ) -> tuple[float, int, int, int]:
        """
        计算当日组合收益率和信号分布。
        HIGH 信号 ETF 纳入组合，等权或信号加权。
        无 HIGH 信号时组合收益为 0（空仓）。
        """
        high = [r for r in results if r.signal_level == "HIGH"]
        mid = [r for r in results if r.signal_level == "MID"]
        low = [r for r in results if r.signal_level == "LOW"]

        if not high or next_date is None:
            return 0.0, len(high), len(mid), len(low)

        returns = []
        weights = []
        for r in high:
            ret = self._get_etf_return(r.etf_code, r.trade_date, next_date, all_bars)
            if ret is not None:
                returns.append(ret)
                weights.append(r.signal_score if weighting == "signal_weighted" else 1.0)

        if not returns:
            return 0.0, len(high), len(mid), len(low)

        total_weight = sum(weights)
        portfolio_return = sum(r * w for r, w in zip(returns, weights)) / total_weight
        return round(portfolio_return, 4), len(high), len(mid), len(low)

    def _compute_summary_metrics(
        self, daily_results: list[BacktestDailyResultModel]
    ) -> dict[str, Any]:
        """计算回测汇总绩效指标。"""
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

        daily_returns = [r.portfolio_return for r in daily_results]
        active_returns = [r.portfolio_return for r in daily_results if r.high_signal_count > 0]

        # 累计收益率
        cumulative_return_pct = daily_results[-1].cumulative_return

        # 最大回撤
        max_drawdown_pct = min(r.drawdown for r in daily_results)

        # 夏普比率（年化，无风险利率=0）
        if len(daily_returns) >= 2:
            mean_r = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std_r = math.sqrt(variance) if variance > 0 else 0.0
            sharpe_ratio = round((mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0, 4)
        else:
            sharpe_ratio = 0.0

        # 胜率：有持仓日中收益>0的比例
        win_rate_pct = 0.0
        if active_returns:
            win_rate_pct = round(
                sum(1 for r in active_returns if r > 0) / len(active_returns) * 100, 2
            )

        # 信号准确率：从 backtest_etf_result 查询
        signal_accuracy_pct = 0.0
        try:
            backtest_id = daily_results[0].backtest_id
            etf_rows = (
                self._db.query(BacktestEtfResultModel)
                .filter(
                    and_(
                        BacktestEtfResultModel.backtest_id == backtest_id,
                        BacktestEtfResultModel.in_portfolio == True,  # noqa: E712
                        BacktestEtfResultModel.etf_return.isnot(None),
                    )
                )
                .all()
            )
            if etf_rows:
                positive = sum(1 for r in etf_rows if r.etf_return is not None and r.etf_return > 0)
                signal_accuracy_pct = round(positive / len(etf_rows) * 100, 2)
        except Exception:
            logger.warning("compute signal_accuracy_pct failed", exc_info=True)

        return BacktestMetrics(
            cumulative_return_pct=round(cumulative_return_pct, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            sharpe_ratio=sharpe_ratio,
            win_rate_pct=win_rate_pct,
            signal_accuracy_pct=signal_accuracy_pct,
            total_trading_days=len(daily_results),
            active_days=len(active_returns),
        ).model_dump()

    # ── Schema 转换辅助 ───────────────────────────────────────────────────────

    def _row_to_summary(self, row: BacktestRunModel) -> BacktestSummary:
        """将 ORM 行转换为 BacktestSummary。"""
        metrics = None
        if row.metrics:
            try:
                metrics = BacktestMetrics(**row.metrics)
            except Exception:
                pass
        backtest_mode = (row.params or {}).get("_backtest_mode", "signal")
        return BacktestSummary(
            backtest_id=row.backtest_id,
            strategy_id=row.strategy_id,
            start_date=row.start_date,
            end_date=row.end_date,
            status=row.status,
            weighting=row.weighting,
            backtest_mode=backtest_mode,
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
