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
        registry: Any | None = None,
    ) -> None:
        """初始化上下文构建器。

        Args:
            db: SQLAlchemy 同步 Session。
            factor_provider: 因子供应器，未提供时自动创建。
            registry: 因子注册表。提供时支持实时模式的因子按需补算。
        """
        self._db = db
        self._registry = registry
        self._factor_provider = factor_provider or FactorProvider(db=db)

    def build(
        self,
        config: StrategyConfig,
        trade_date: date,
        index_codes: list[str] | None = None,
        all_bars: dict[tuple[str, date], Any] | None = None,
        all_valuation: dict[tuple[str, date], Any] | None = None,
        precomputed_factors: dict[tuple[str, str], float | None] | None = None,
        cached_universe: list[dict[str, Any]] | None = None,
        cached_metadata: dict[str, dict[str, Any]] | None = None,
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
            cached_universe: 预构建的 universe 列表（回测模式复用，避免每日重构）。
            cached_metadata: 预构建的 asset_metadata 字典（回测模式复用）。

        Returns:
            填充完成的 EngineContext。
        """
        is_backtest = all_bars is not None

        if is_backtest:
            return self._build_backtest(
                config,
                trade_date,
                index_codes,
                all_bars,
                all_valuation,
                precomputed_factors,
                cached_universe,
                cached_metadata,
            )
        return self._build_live(config, trade_date)

    # ==================================================================
    # 实时模式
    # ==================================================================

    def _build_live(self, config: StrategyConfig, trade_date: date) -> EngineContext:
        """实时模式：从 DB 加载全量指数数据和预计算因子。

        自动将 trade_date 回退到有数据的最近交易日，
        避免当天未收盘时查询不到任何数据。
        """
        from quant_etf_api.infra.db.models.core import (
            BenchmarkIndexModel,
            IndexDailyBarModel,
            IndexValuationModel,
        )

        # 解析有效交易日：取 DB 中不超过 trade_date 的最大交易日
        effective_date = self._resolve_effective_date(trade_date)

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

        # 加载日线（90 天回望，基于有效交易日）
        lookback = effective_date - timedelta(days=90)
        bars = (
            self._db.query(IndexDailyBarModel)
            .filter(
                IndexDailyBarModel.trade_date >= lookback,
                IndexDailyBarModel.trade_date <= effective_date,
                IndexDailyBarModel.index_code.in_(index_codes),
            )
            .all()
        )
        local_bars: dict[tuple[str, date], Any] = {(r.index_code, r.trade_date): r for r in bars}

        # 加载估值（基于有效交易日范围）
        val_rows = (
            self._db.query(IndexValuationModel)
            .filter(
                IndexValuationModel.trade_date >= lookback,
                IndexValuationModel.trade_date <= effective_date,
            )
            .all()
        )
        index_valuation: dict[str, dict[str, Any]] = {}
        for r in val_rows:
            index_valuation[r.index_code] = {
                "pe_percentile": r.pe_percentile,
                "pb_percentile": r.pb_percentile,
            }

        # 通过 FactorProvider 加载因子值（基于有效交易日）
        asset_factors = self._factor_provider.load_asset_factors(
            config, effective_date, index_codes
        )

        # 按需补算：策略依赖的因子值缺失时自动触发 FactorService 计算
        # 检查分三层：
        # 1. 因子 ID 是否完全不存在（整个因子从未计算过）
        # 2. 部分资产的因子值缺失（调度器计算时部分指数数据未就绪导致跳过）
        # 3. 因子行存在但值为 NULL（调度器计算时行情数据未完全就绪）
        if self._registry is not None:
            factor_ids = self._factor_provider.collect_required_factor_ids(config)
            loaded_ids = {fid for (_, fid) in asset_factors}
            missing = [fid for fid in factor_ids if fid not in loaded_ids]

            # 检查是否存在部分资产因子值缺失（因子 ID 存在但非全量覆盖）
            if not missing and factor_ids and index_codes:
                expected_pairs = len(index_codes) * len(factor_ids)
                if len(asset_factors) < expected_pairs:
                    # 找出缺失的 (code, factor_id) 组合
                    missing_pairs = [
                        (c, f)
                        for c in index_codes
                        for f in factor_ids
                        if (c, f) not in asset_factors
                    ]
                    affected_codes = {c for c, _ in missing_pairs}
                    affected_fids = {f for _, f in missing_pairs}
                    logger.info(
                        "部分资产因子值缺失，触发按需补算: trade_date=%s "
                        "affected_codes=%s affected_factors=%s",
                        effective_date,
                        affected_codes,
                        affected_fids,
                    )
                    missing = list(affected_fids)

            # 检查因子值是否为 NULL（行存在但无实际数值，
            # 常见于调度器在行情数据就绪前计算导致）
            if not missing and factor_ids and index_codes:
                null_pairs = [
                    (c, f)
                    for c in index_codes
                    for f in factor_ids
                    if (c, f) in asset_factors and asset_factors[(c, f)] is None
                ]
                if null_pairs:
                    null_fids = {f for _, f in null_pairs}
                    logger.info(
                        "部分因子值为 NULL，触发按需补算: trade_date=%s "
                        "affected_factors=%s",
                        effective_date,
                        null_fids,
                    )
                    missing = list(null_fids)

            if missing:
                logger.info(
                    "因子数据缺失，触发按需计算: trade_date=%s missing=%s",
                    effective_date,
                    missing[:5],
                )
                from quant_etf_api.factors.service import FactorService

                try:
                    fs = FactorService(self._db, self._registry)
                    fs.sync_factor_definitions()  # 确保 factor_definition 表已同步
                    fs.compute_and_store(effective_date)
                    self._db.commit()
                    asset_factors = self._factor_provider.load_asset_factors(
                        config, effective_date, index_codes
                    )
                    logger.info(
                        "按需因子计算完成: trade_date=%s factor_count=%d",
                        effective_date,
                        len(asset_factors),
                    )
                except Exception:
                    logger.warning("按需因子计算失败", exc_info=True)
                    self._db.rollback()

        # 补充原始行情数据（change_pct、close_price 不是因子，是原始字段）
        for code in index_codes:
            bar = local_bars.get((code, effective_date))
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

        # 市场级择时因子（基于有效交易日）
        market_factors = self._factor_provider.load_market_factors(config, effective_date)

        # 补充市场因子：如果 FactorProvider 未返回估值因子，从估值表获取
        if config.timing:
            for rep_code in config.timing.proxy_index_codes:
                val = index_valuation.get(rep_code, {})
                if "pe_percentile" not in market_factors and val.get("pe_percentile") is not None:
                    market_factors["pe_percentile"] = val["pe_percentile"]
                    market_factors["pb_percentile"] = val.get("pb_percentile")
                if "change_pct" not in market_factors:
                    bar = local_bars.get((rep_code, effective_date))
                    if bar and bar.change_pct is not None:
                        market_factors["change_pct"] = bar.change_pct
                if market_factors:
                    break

        return EngineContext(
            trade_date=effective_date,
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
        cached_universe: list[dict[str, Any]] | None = None,
        cached_metadata: dict[str, dict[str, Any]] | None = None,
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

        # 使用缓存或现场构建 universe 和 metadata
        if cached_universe is not None:
            universe = cached_universe
        else:
            universe = [
                {"etf_code": code, "name_cn": code, "category": "broad_index"} for code in codes
            ]
        if cached_metadata is not None:
            asset_metadata = cached_metadata
        else:
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

        # 市场级择时因子：从预计算因子中提取择时代理指数的因子值
        market_factors: dict[str, float | None] = {}
        if config.timing and precomputed_factors:
            for factor_id in config.timing.factors:
                for rep_code in config.timing.proxy_index_codes:
                    val = precomputed_factors.get((rep_code, factor_id))
                    if val is not None:
                        market_factors[factor_id] = val
                        break
                else:
                    market_factors[factor_id] = None

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

    def _resolve_effective_date(self, trade_date: date) -> date:
        """将有数据的最新交易日作为有效交易日。

        查询 index_daily_bar 表中不超过 trade_date 的最大交易日，
        确保后续因子查询能命中数据。

        Args:
            trade_date: 请求的交易日（可能是当天，但数据尚未就绪）。

        Returns:
            有数据的最新交易日。如果 DB 为空则原样返回 trade_date。
        """
        from quant_etf_api.infra.db.models.core import IndexDailyBarModel

        result = (
            self._db.query(IndexDailyBarModel.trade_date)
            .filter(IndexDailyBarModel.trade_date <= trade_date)
            .order_by(IndexDailyBarModel.trade_date.desc())
            .limit(1)
            .first()
        )
        if result is not None and result[0] < trade_date:
            logger.info("trade_date=%s 无数据，回退到最近交易日=%s", trade_date, result[0])
            return result[0]
        return trade_date

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
