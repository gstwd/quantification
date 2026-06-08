"""统一的引擎上下文构建器。

提供单一 build() 方法，同时支持实时和回测两种模式。
与旧 ContextBuilder（services/context_builder.py）的核心区别：
- 因子来源：通过 FactorProvider 加载预计算值，不再硬编码计算
- 因子集：由 StrategyConfig 推导，实时和回测完全一致
- 方法统一：build() 替代 build_live_context() + build_backtest_context()
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.factor_provider import FactorProvider

logger = logging.getLogger(__name__)


class ContextBuilder:
    """统一的引擎上下文构建器。

    通过 FactorProvider 加载预计算因子值，消除硬编码因子计算。
    实时和回测模式共用 build() 入口，由参数区分模式。
    """

    def __init__(
        self,
        db: Session,
        factor_provider: FactorProvider | None = None,
    ) -> None:
        """初始化上下文构建器。

        Args:
            db: SQLAlchemy 同步 Session。
            factor_provider: 因子供应器，未提供时自动创建。
        """
        self._db = db
        self._factor_provider = factor_provider or FactorProvider(db=db)

    def build(
        self,
        config: StrategyConfig,
        trade_date: date,
        index_codes: list[str] | None = None,
        all_bars: dict[tuple[str, date], Any] | None = None,
        all_valuation: dict[tuple[str, date], Any] | None = None,
        precomputed_factors: dict[tuple[str, str], float | None] | None = None,
    ) -> EngineContext:
        """构建引擎上下文（实时和回测统一入口）。

        实时模式：index_codes=None，从 DB 查询全量指数。
        回测模式：传入 index_codes + all_bars + all_valuation + precomputed_factors。

        Args:
            config: 策略配置，用于推导因子集和过滤资产范围。
            trade_date: 交易日。
            index_codes: 指数代码列表，None 表示实时模式（从 DB 查询全量）。
            all_bars: 预加载的指数日线数据（回测模式）。
            all_valuation: 预加载的估值数据（回测模式）。
            precomputed_factors: 预计算的因子值字典（回测模式）。

        Returns:
            填充完成的 EngineContext。
        """
        is_backtest = all_bars is not None

        if is_backtest:
            return self._build_backtest(
                config, trade_date, index_codes, all_bars, all_valuation, precomputed_factors
            )
        return self._build_live(config, trade_date)

    # ==================================================================
    # 实时模式
    # ==================================================================

    def _build_live(self, config: StrategyConfig, trade_date: date) -> EngineContext:
        """实时模式：从 DB 加载全量指数数据和预计算因子。"""
        from quant_etf_api.infra.db.models.core import (
            BenchmarkIndexModel,
            IndexDailyBarModel,
            IndexValuationModel,
        )

        # 获取全量活跃指数（排除已退市/停发的指数，避免幸存者偏差）
        indexes = (
            self._db.query(BenchmarkIndexModel)
            .filter(BenchmarkIndexModel.is_active.is_(True))
            .all()
        )
        index_codes = [idx.index_code for idx in indexes]

        # 应用 index_codes 过滤
        index_codes = self._filter_by_scope(indexes, config.index_codes)

        universe = [
            {"etf_code": idx.index_code, "name_cn": idx.name_cn, "category": "broad_index"}
            for idx in indexes
            if idx.index_code in index_codes
        ]
        asset_metadata = {
            idx.index_code: {"name_cn": idx.name_cn, "category": "broad_index"}
            for idx in indexes
            if idx.index_code in index_codes
        }

        # 加载日线（90 天回望）
        lookback = trade_date - timedelta(days=90)
        bars = (
            self._db.query(IndexDailyBarModel)
            .filter(
                IndexDailyBarModel.trade_date >= lookback,
                IndexDailyBarModel.trade_date <= trade_date,
                IndexDailyBarModel.index_code.in_(index_codes),
            )
            .all()
        )
        local_bars: dict[tuple[str, date], Any] = {(r.index_code, r.trade_date): r for r in bars}

        # 加载估值
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

        # 通过 FactorProvider 加载因子值
        asset_factors = self._factor_provider.load_asset_factors(config, trade_date, index_codes)

        # 补充原始行情数据（change_pct、close_price 不是因子，是原始字段）
        for code in index_codes:
            bar = local_bars.get((code, trade_date))
            if bar is None:
                continue
            if (code, "change_pct") not in asset_factors:
                asset_factors[(code, "change_pct")] = bar.change_pct or 0.0
            if (code, "close_price") not in asset_factors:
                asset_factors[(code, "close_price")] = bar.close_price

            # 估值因子直接从估值表补充（如果 FactorProvider 未返回）
            val = index_valuation.get(code, {})
            if (code, "pe_percentile") not in asset_factors:
                asset_factors[(code, "pe_percentile")] = val.get("pe_percentile")
            if (code, "pb_percentile") not in asset_factors:
                asset_factors[(code, "pb_percentile")] = val.get("pb_percentile")

        # 市场级择时因子
        market_factors = self._factor_provider.load_market_factors(config, trade_date)

        # 补充市场因子：如果 FactorProvider 未返回估值因子，从估值表获取
        if config.timing:
            for rep_code in config.timing.proxy_index_codes:
                val = index_valuation.get(rep_code, {})
                if "pe_percentile" not in market_factors and val.get("pe_percentile") is not None:
                    market_factors["pe_percentile"] = val["pe_percentile"]
                    market_factors["pb_percentile"] = val.get("pb_percentile")
                if "change_pct" not in market_factors:
                    bar = local_bars.get((rep_code, trade_date))
                    if bar and bar.change_pct is not None:
                        market_factors["change_pct"] = bar.change_pct
                if market_factors:
                    break

        return EngineContext(
            trade_date=trade_date,
            universe=universe,
            asset_factors=asset_factors,
            market_factors=market_factors,
            asset_metadata=asset_metadata,
            extra={"index_valuation": index_valuation},
        )

    # ==================================================================
    # 回测模式
    # ==================================================================

    def _build_backtest(
        self,
        config: StrategyConfig,
        trade_date: date,
        index_codes: list[str] | None,
        all_bars: dict[tuple[str, date], Any],
        all_valuation: dict[tuple[str, date], Any] | None,
        precomputed_factors: dict[tuple[str, str], float | None] | None,
    ) -> EngineContext:
        """回测模式：使用预加载数据构建上下文。

        如果策略的 index_codes 非空，对传入的 codes 做交集过滤，
        确保回测标的范围不超出策略设计范围。
        """
        codes = index_codes or []

        # 策略 index_codes 过滤：取回测标的与策略限定标的的交集
        if config.index_codes:
            strategy_codes = set(config.index_codes)
            codes = [c for c in codes if c in strategy_codes]

        universe = [
            {"etf_code": code, "name_cn": code, "category": "broad_index"} for code in codes
        ]
        asset_metadata = {code: {"name_cn": code, "category": "broad_index"} for code in codes}

        # 使用预计算的因子值
        asset_factors: dict[tuple[str, str], float | None] = (
            dict(precomputed_factors) if precomputed_factors else {}
        )

        # 补充原始行情数据
        for code in codes:
            bar = all_bars.get((code, trade_date))
            if bar is None:
                continue
            if (code, "change_pct") not in asset_factors:
                asset_factors[(code, "change_pct")] = bar.change_pct or 0.0
            if (code, "close_price") not in asset_factors:
                asset_factors[(code, "close_price")] = bar.close_price

            # 估值因子从估值数据补充
            if all_valuation:
                val_row = all_valuation.get((code, trade_date))
                if val_row:
                    if (code, "pe_percentile") not in asset_factors:
                        asset_factors[(code, "pe_percentile")] = val_row.pe_percentile
                    if (code, "pb_percentile") not in asset_factors:
                        asset_factors[(code, "pb_percentile")] = val_row.pb_percentile

        # 市场级择时因子
        market_factors: dict[str, float | None] = {}
        if config.timing and all_valuation:
            for rep_code in config.timing.proxy_index_codes:
                if rep_code not in codes:
                    continue
                val_row = all_valuation.get((rep_code, trade_date))
                if val_row and val_row.pe_percentile is not None:
                    market_factors["pe_percentile"] = val_row.pe_percentile
                    market_factors["pb_percentile"] = val_row.pb_percentile
                    break

        return EngineContext(
            trade_date=trade_date,
            universe=universe,
            asset_factors=asset_factors,
            market_factors=market_factors,
            asset_metadata=asset_metadata,
            extra={},
        )

    # ==================================================================
    # 内部方法
    # ==================================================================

    @staticmethod
    def _filter_by_scope(indexes: list[Any], index_codes: list[str] | None = None) -> list[str]:
        """根据 index_codes 过滤指数代码列表。

        Args:
            indexes: BenchmarkIndexModel 列表。
            index_codes: 指定的指数代码列表，非空时仅保留这些指数。

        Returns:
            过滤后的指数代码列表。
        """
        if index_codes:
            all_codes = {idx.index_code for idx in indexes}
            return [c for c in index_codes if c in all_codes]
        return [idx.index_code for idx in indexes]
