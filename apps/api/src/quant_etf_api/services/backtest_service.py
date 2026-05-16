from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_
from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    BacktestDailyResultModel,
    BacktestEtfResultModel,
    BacktestRunModel,
    EtfDailyBarModel,
    EtfDailyShareModel,
    EtfUniverseModel,
    IndexDailyBarModel,
)
from quant_etf_api.plugins.base import StrategyContextData, StrategyResult
from quant_etf_api.plugins.registry import StrategyRegistry
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

    def __init__(self, db: Session, registry: StrategyRegistry) -> None:
        self._db = db
        self._registry = registry

    def create_backtest(self, req: BacktestCreateRequest) -> BacktestSummary:
        """创建回测记录，状态为 pending，立即返回。"""
        backtest_id = str(uuid4())
        now = datetime.utcnow()
        universe_filter = (
            {"mode": "all"}
            if req.universe_mode == "all"
            else {"mode": "subset", "etf_codes": req.etf_codes}
        )
        try:
            row = BacktestRunModel(
                backtest_id=backtest_id,
                strategy_id=req.strategy_id,
                start_date=req.start_date,
                end_date=req.end_date,
                universe_filter=universe_filter,
                params=req.params,
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
            created_at=now,
        )

    def list_backtests(self, limit: int = 50) -> list[BacktestSummary]:
        """返回最近的回测列表，按创建时间倒序。"""
        try:
            rows = (
                self._db.query(BacktestRunModel)
                .order_by(BacktestRunModel.created_at.desc())
                .limit(limit)
                .all()
            )
            return [self._row_to_summary(r) for r in rows]
        except Exception:
            logger.warning("list_backtests DB query failed", exc_info=True)
            return []

    def get_backtest(self, backtest_id: str) -> BacktestDetail | None:
        """返回回测详情，含配置信息。"""
        try:
            row = (
                self._db.query(BacktestRunModel)
                .filter(BacktestRunModel.backtest_id == backtest_id)
                .first()
            )
            if row is None:
                return None
            return self._row_to_detail(row)
        except Exception:
            logger.warning("get_backtest DB query failed", exc_info=True)
            return None

    def get_daily_results(self, backtest_id: str) -> list[BacktestDailyResult]:
        """返回回测每日组合绩效，按日期升序。"""
        try:
            rows = (
                self._db.query(BacktestDailyResultModel)
                .filter(BacktestDailyResultModel.backtest_id == backtest_id)
                .order_by(BacktestDailyResultModel.trade_date.asc())
                .all()
            )
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
            q = self._db.query(BacktestEtfResultModel).filter(
                BacktestEtfResultModel.backtest_id == backtest_id
            )
            if etf_code:
                q = q.filter(BacktestEtfResultModel.etf_code == etf_code)
            rows = q.order_by(
                BacktestEtfResultModel.trade_date.asc(), BacktestEtfResultModel.etf_code.asc()
            ).all()
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
        """
        回测执行入口，在后台线程中同步运行。
        逐日调用策略插件，计算组合收益，写入结果表，最后更新汇总指标。
        """
        try:
            row = (
                self._db.query(BacktestRunModel)
                .filter(BacktestRunModel.backtest_id == backtest_id)
                .first()
            )
            if row is None:
                logger.error("run_backtest: backtest_id %s not found", backtest_id)
                return

            # 标记为执行中
            row.status = "running"
            row.started_at = datetime.utcnow()
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

            daily_results: list[BacktestDailyResultModel] = []
            cumulative = 1.0  # 累计净值，初始为 1
            peak = 1.0

            for i, trade_date in enumerate(trading_dates):
                context = self._build_historical_context(
                    trade_date, etf_codes, all_bars, all_shares, all_index_bars
                )
                results = plugin.run_for_universe(trade_date, universe, context, row.params)

                # 计算 T+1 收益（末日无 T+1 数据，etf_return=None）
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

                # 写入每 ETF 结果
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

            # 计算汇总指标
            metrics = self._compute_summary_metrics(daily_results)

            row.status = "success"
            row.finished_at = datetime.utcnow()
            row.metrics = metrics
            self._db.commit()

        except Exception as exc:
            self._db.rollback()
            logger.exception("run_backtest failed for %s", backtest_id)
            try:
                row = (
                    self._db.query(BacktestRunModel)
                    .filter(BacktestRunModel.backtest_id == backtest_id)
                    .first()
                )
                if row:
                    row.status = "failed"
                    row.error_message = str(exc)
                    row.finished_at = datetime.utcnow()
                    self._db.commit()
            except Exception:
                self._db.rollback()

    # ── 内部辅助方法 ──────────────────────────────────────────────────────────

    def _resolve_universe(self, universe_filter: dict) -> list[dict]:
        """根据 universe_filter 查询回测标的列表。"""
        q = self._db.query(EtfUniverseModel).filter(EtfUniverseModel.is_active == True)  # noqa: E712
        if universe_filter.get("mode") == "subset":
            codes = universe_filter.get("etf_codes", [])
            if codes:
                q = q.filter(EtfUniverseModel.etf_code.in_(codes))
        return [{"etf_code": r.etf_code, "name_cn": r.name_cn} for r in q.all()]

    def _get_trading_dates(self, start: date, end: date, etf_codes: list[str]) -> list[date]:
        """从 etf_daily_bar 中提取区间内的交易日列表（升序）。"""
        rows = (
            self._db.query(EtfDailyBarModel.trade_date)
            .filter(
                and_(
                    EtfDailyBarModel.trade_date >= start,
                    EtfDailyBarModel.trade_date <= end,
                    EtfDailyBarModel.etf_code.in_(etf_codes),
                )
            )
            .distinct()
            .order_by(EtfDailyBarModel.trade_date.asc())
            .all()
        )
        return [r.trade_date for r in rows]

    def _load_all_bars(
        self, trading_dates: list[date], etf_codes: list[str]
    ) -> dict[tuple[str, date], EtfDailyBarModel]:
        """批量加载回测区间及前 25 日的行情数据，返回 (etf_code, trade_date) → row 映射。"""
        if not trading_dates:
            return {}
        lookback_start = trading_dates[0] - timedelta(days=35)
        rows = (
            self._db.query(EtfDailyBarModel)
            .filter(
                and_(
                    EtfDailyBarModel.trade_date >= lookback_start,
                    EtfDailyBarModel.trade_date <= trading_dates[-1],
                    EtfDailyBarModel.etf_code.in_(etf_codes),
                )
            )
            .all()
        )
        return {(r.etf_code, r.trade_date): r for r in rows}

    def _load_all_shares(
        self, trading_dates: list[date], etf_codes: list[str]
    ) -> dict[tuple[str, date], EtfDailyShareModel]:
        """批量加载回测区间的份额数据，返回 (etf_code, trade_date) → row 映射。"""
        if not trading_dates:
            return {}
        rows = (
            self._db.query(EtfDailyShareModel)
            .filter(
                and_(
                    EtfDailyShareModel.trade_date >= trading_dates[0],
                    EtfDailyShareModel.trade_date <= trading_dates[-1],
                    EtfDailyShareModel.etf_code.in_(etf_codes),
                )
            )
            .all()
        )
        return {(r.etf_code, r.trade_date): r for r in rows}

    def _load_all_index_bars(
        self, trading_dates: list[date]
    ) -> dict[tuple[str, date], IndexDailyBarModel]:
        """批量加载回测区间及前 10 日的指数行情数据。"""
        if not trading_dates:
            return {}
        lookback_start = trading_dates[0] - timedelta(days=15)
        rows = (
            self._db.query(IndexDailyBarModel)
            .filter(
                and_(
                    IndexDailyBarModel.trade_date >= lookback_start,
                    IndexDailyBarModel.trade_date <= trading_dates[-1],
                )
            )
            .all()
        )
        return {(r.index_code, r.trade_date): r for r in rows}

    def _build_historical_context(
        self,
        trade_date: date,
        etf_codes: list[str],
        all_bars: dict,
        all_shares: dict,
        all_index_bars: dict,
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
            index_5d_return[index_code] = self._calc_5d_return_index(
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
        etf_bars: dict[str, dict] = {}
        for code in etf_codes:
            bar = all_bars.get((code, trade_date))
            if bar is None:
                continue
            volume_ratio_20d = self._calc_volume_ratio_20d(code, trade_date, all_bars)
            etf_5d_return = self._calc_5d_return_etf(code, trade_date, all_bars)
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

    def _calc_volume_ratio_20d(self, etf_code: str, trade_date: date, all_bars: dict) -> float:
        """计算 20 日量比：当日成交量 / 近 20 日平均成交量。"""
        today_bar = all_bars.get((etf_code, trade_date))
        if today_bar is None or today_bar.volume is None:
            return 1.0
        # 收集 trade_date 之前的 20 个交易日数据
        past_volumes = [
            v.volume
            for (code, dt), v in all_bars.items()
            if code == etf_code and dt < trade_date and v.volume is not None
        ]
        past_volumes.sort()
        recent_20 = past_volumes[-20:] if len(past_volumes) >= 20 else past_volumes
        if not recent_20:
            return 1.0
        avg = sum(recent_20) / len(recent_20)
        return round(today_bar.volume / avg, 4) if avg > 0 else 1.0

    def _calc_5d_return_etf(self, etf_code: str, trade_date: date, all_bars: dict) -> float:
        """计算 ETF 近 5 日收益率（%）。"""
        today_bar = all_bars.get((etf_code, trade_date))
        if today_bar is None or today_bar.close_price is None:
            return 0.0
        past_closes = sorted(
            [
                (dt, v.close_price)
                for (code, dt), v in all_bars.items()
                if code == etf_code and dt < trade_date and v.close_price is not None
            ],
            key=lambda x: x[0],
        )
        if len(past_closes) < 5:
            return 0.0
        base_close = past_closes[-5][1]
        return round((today_bar.close_price / base_close - 1) * 100, 4) if base_close > 0 else 0.0

    def _calc_5d_return_index(
        self, index_code: str, trade_date: date, all_index_bars: dict
    ) -> float:
        """计算指数近 5 日收益率（%）。"""
        today_bar = all_index_bars.get((index_code, trade_date))
        if today_bar is None or today_bar.close_price is None:
            return 0.0
        past_closes = sorted(
            [
                (dt, v.close_price)
                for (code, dt), v in all_index_bars.items()
                if code == index_code and dt < trade_date and v.close_price is not None
            ],
            key=lambda x: x[0],
        )
        if len(past_closes) < 5:
            return 0.0
        base_close = past_closes[-5][1]
        return round((today_bar.close_price / base_close - 1) * 100, 4) if base_close > 0 else 0.0

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

    def _compute_summary_metrics(self, daily_results: list[BacktestDailyResultModel]) -> dict:
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
        return BacktestSummary(
            backtest_id=row.backtest_id,
            strategy_id=row.strategy_id,
            start_date=row.start_date,
            end_date=row.end_date,
            status=row.status,
            weighting=row.weighting,
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
