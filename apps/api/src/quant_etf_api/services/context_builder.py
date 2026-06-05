"""统一的引擎上下文构建器。

替代 StrategyService._build_allocation_context、
BacktestService._build_index_context、
StrategyExecutionService._build_live_context 三处独立的上下文构建逻辑。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.common.bar_metrics import calc_5d_return, calc_volume_ratio_20d
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.infra.db.models.core import (
    EtfDailyBarModel,
    EtfUniverseModel,
    IndexDailyBarModel,
    IndexFactorValueModel,
    IndexValuationModel,
)

logger = logging.getLogger(__name__)


class ContextBuilder:
    """统一的引擎上下文构建器。"""

    def __init__(self, db: Session) -> None:
        """初始化上下文构建器。

        Args:
            db: SQLAlchemy Session。
        """
        self._db = db

    def build_live_context(self, trade_date: date) -> EngineContext:
        """为实时策略运行构建引擎上下文。

        从 DB 加载 ETF 宇宙、指数行情、估值、预计算因子值，
        构建标准化的 EngineContext。

        Args:
            trade_date: 交易日。

        Returns:
            引擎上下文。
        """
        # 获取活跃 ETF
        etfs = (
            self._db.query(EtfUniverseModel)
            .filter(EtfUniverseModel.is_active.is_(True))
            .all()
        )
        universe = [
            {"etf_code": e.etf_code, "name_cn": e.name_cn, "category": e.category}
            for e in etfs
        ]
        asset_metadata = {
            e.etf_code: {"name_cn": e.name_cn, "category": e.category, "tracking_index_code": e.tracking_index_code}
            for e in etfs
        }
        etf_codes = [e.etf_code for e in etfs]
        asset_index_map = {e.etf_code: e.tracking_index_code for e in etfs if e.tracking_index_code}

        lookback = trade_date - timedelta(days=90)

        # 加载 ETF 日线
        bars = (
            self._db.query(EtfDailyBarModel)
            .filter(
                EtfDailyBarModel.trade_date >= lookback,
                EtfDailyBarModel.trade_date <= trade_date,
                EtfDailyBarModel.etf_code.in_(etf_codes),
            )
            .all()
        )
        all_bars = {(r.etf_code, r.trade_date): r for r in bars}

        # 加载指数日线（用于择时参考指数）
        index_bars_rows = (
            self._db.query(IndexDailyBarModel)
            .filter(
                IndexDailyBarModel.trade_date >= lookback,
                IndexDailyBarModel.trade_date <= trade_date,
            )
            .all()
        )
        all_index_bars = {(r.index_code, r.trade_date): r for r in index_bars_rows}

        # 加载指数估值
        val_rows = (
            self._db.query(IndexValuationModel)
            .filter(IndexValuationModel.trade_date >= lookback)
            .all()
        )
        index_valuation: dict[str, dict[str, Any]] = {}
        for r in val_rows:
            index_valuation[r.index_code] = {
                "pe_percentile": r.pe_percentile,
                "pb_percentile": r.pb_percentile,
            }

        # 加载预计算因子值
        precomputed = self._load_precomputed_factors(trade_date)

        # 构建 asset_factors
        asset_factors: dict[tuple[str, str], float | None] = {}
        for code in etf_codes:
            bar = all_bars.get((code, trade_date))
            if bar is None or bar.close_price is None:
                continue

            # 从预计算因子值获取，缺失时回退计算
            factors = precomputed.get(code, {})
            volume_ratio = factors.get("volume_ratio_20d", calc_volume_ratio_20d(code, trade_date, all_bars))
            return_5d = factors.get("return_5d", calc_5d_return(code, trade_date, all_bars))

            asset_factors[(code, "volume_ratio_20d")] = volume_ratio
            asset_factors[(code, "return_5d")] = return_5d
            asset_factors[(code, "change_pct")] = bar.change_pct or 0.0

            # 关联指数的估值因子
            idx_code = asset_index_map.get(code)
            if idx_code:
                val = index_valuation.get(idx_code, {})
                asset_factors[(code, "pe_percentile")] = val.get("pe_percentile")
                asset_factors[(code, "pb_percentile")] = val.get("pb_percentile")

        # 构建 market_factors（择时用的市场级因子）
        market_factors: dict[str, float | None] = {}
        # 选取代表性指数的估值
        for rep_code in ("000300", "000016", "000905"):
            val = index_valuation.get(rep_code, {})
            if val.get("pe_percentile") is not None:
                market_factors["pe_percentile"] = val["pe_percentile"]
                market_factors["pb_percentile"] = val.get("pb_percentile")
                break

        # 代表性指数的量比和趋势
        for rep_code in ("000300", "000016", "000905"):
            bar = all_index_bars.get((rep_code, trade_date))
            if bar and bar.close_price:
                market_factors["volume_ratio_20d"] = calc_volume_ratio_20d(rep_code, trade_date, all_index_bars)
                # 计算 MA60 偏离度
                closes = sorted(
                    [v.close_price for (c, dt), v in all_index_bars.items()
                     if c == rep_code and dt <= trade_date and v.close_price is not None],
                )
                if len(closes) >= 60:
                    ma60 = sum(closes[-60:]) / 60
                    deviation = (bar.close_price - ma60) / ma60 * 100
                    market_factors["ma60_deviation"] = round(deviation, 2)
                break

        return EngineContext(
            trade_date=trade_date,
            universe=universe,
            asset_factors=asset_factors,
            market_factors=market_factors,
            asset_metadata=asset_metadata,
            extra={
                "asset_index_map": asset_index_map,
                "index_valuation": index_valuation,
            },
        )

    def build_backtest_context(
        self,
        trade_date: date,
        index_codes: list[str],
        all_bars: dict[tuple[str, date], Any],
        all_valuation: dict[tuple[str, date], Any],
    ) -> EngineContext:
        """为回测构建引擎上下文。

        Args:
            trade_date: 交易日。
            index_codes: 回测指数代码列表。
            all_bars: 预加载的指数日线数据。
            all_valuation: 预加载的估值数据。

        Returns:
            引擎上下文。
        """
        universe = [
            {"etf_code": code, "name_cn": code, "category": "broad_index"}
            for code in index_codes
        ]
        asset_metadata = {code: {"name_cn": code, "category": "broad_index"} for code in index_codes}

        # 构建 asset_factors
        asset_factors: dict[tuple[str, str], float | None] = {}
        for code in index_codes:
            bar = all_bars.get((code, trade_date))
            if bar is None:
                continue

            asset_factors[(code, "volume_ratio_20d")] = calc_volume_ratio_20d(code, trade_date, all_bars)
            asset_factors[(code, "return_5d")] = calc_5d_return(code, trade_date, all_bars)
            asset_factors[(code, "change_pct")] = bar.change_pct or 0.0
            asset_factors[(code, "close_price")] = bar.close_price

            # N 日收益率
            asset_factors[(code, "return_20d")] = self._calc_nd_return(code, trade_date, all_bars, 20)
            asset_factors[(code, "return_60d")] = self._calc_nd_return(code, trade_date, all_bars, 60)

            # 波动率
            asset_factors[(code, "volatility_20d")] = None  # 后续可计算

            # MA60 偏离度
            ma60 = self._calc_ma(code, trade_date, all_bars, 60)
            if ma60 and bar.close_price:
                deviation = (bar.close_price - ma60) / ma60 * 100
                asset_factors[(code, "ma60_deviation")] = round(deviation, 2)

            # 估值
            val_row = all_valuation.get((code, trade_date))
            if val_row:
                asset_factors[(code, "pe_percentile")] = val_row.pe_percentile
                asset_factors[(code, "pb_percentile")] = val_row.pb_percentile
            else:
                asset_factors[(code, "pe_percentile")] = None
                asset_factors[(code, "pb_percentile")] = None

        # 构建 market_factors
        market_factors: dict[str, float | None] = {}
        for rep_code in ("000300", "000016", "000905"):
            if rep_code not in index_codes:
                continue
            val_row = all_valuation.get((rep_code, trade_date))
            if val_row and val_row.pe_percentile is not None:
                market_factors["pe_percentile"] = val_row.pe_percentile
                market_factors["pb_percentile"] = val_row.pb_percentile
                break

        for rep_code in ("000300", "000016", "000905"):
            if rep_code not in index_codes:
                continue
            bar = all_bars.get((rep_code, trade_date))
            if bar and bar.close_price:
                market_factors["volume_ratio_20d"] = calc_volume_ratio_20d(rep_code, trade_date, all_bars)
                ma60 = self._calc_ma(rep_code, trade_date, all_bars, 60)
                if ma60:
                    deviation = (bar.close_price - ma60) / ma60 * 100
                    market_factors["ma60_deviation"] = round(deviation, 2)
                break

        return EngineContext(
            trade_date=trade_date,
            universe=universe,
            asset_factors=asset_factors,
            market_factors=market_factors,
            asset_metadata=asset_metadata,
            extra={"index_valuation": {}},
        )

    def _load_precomputed_factors(self, trade_date: date) -> dict[str, dict[str, float]]:
        """从 index_factor_value 加载预计算因子值。"""
        precomputed: dict[str, dict[str, float]] = {}
        for factor_id in ("volume_ratio_20d", "return_5d", "return_20d"):
            rows = (
                self._db.query(
                    IndexFactorValueModel.index_code,
                    IndexFactorValueModel.factor_value_numeric,
                )
                .filter(
                    IndexFactorValueModel.factor_id == factor_id,
                    IndexFactorValueModel.trade_date == trade_date,
                    IndexFactorValueModel.strategy_id.is_(None),
                )
                .all()
            )
            for code, value in rows:
                precomputed.setdefault(code, {})[factor_id] = value
        return precomputed

    @staticmethod
    def _calc_nd_return(
        code: str, trade_date: date, all_bars: dict, n: int
    ) -> float | None:
        """从预加载数据中计算 N 日收益率。"""
        today_bar = all_bars.get((code, trade_date))
        if today_bar is None or today_bar.close_price is None:
            return None
        past_closes = sorted(
            [(dt, v.close_price) for (c, dt), v in all_bars.items()
             if c == code and dt < trade_date and v.close_price is not None],
            key=lambda x: x[0],
        )
        if len(past_closes) < n:
            return None
        base = past_closes[-n][1]
        if base <= 0:
            return None
        return round((today_bar.close_price / base - 1) * 100, 4)

    @staticmethod
    def _calc_ma(
        code: str, trade_date: date, all_bars: dict, n: int
    ) -> float | None:
        """从预加载数据中计算 N 日均线。"""
        closes = sorted(
            [v.close_price for (c, dt), v in all_bars.items()
             if c == code and dt <= trade_date and v.close_price is not None],
        )
        if len(closes) < n:
            return None
        return round(sum(closes[-n:]) / n, 4)
