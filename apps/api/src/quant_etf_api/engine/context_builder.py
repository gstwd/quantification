"""统一的引擎上下文构建器。

提供单一 build() 方法，同时支持实时和回测两种模式：
- 因子来源：通过 FactorProvider 按策略实际参数现算，不做硬编码计算；
- 因子集：由 StrategyConfig 推导，实时和回测完全一致；
- 方法统一：build() 覆盖实时与回测；
- 只读职责：本模块不产生任何写操作。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.portfolio.universe import build_universe_items
from quant_etf_api.engine.base import EngineContext
from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.factors.base import MissingReason
from quant_etf_api.factors.catalog import FactorTemplateRegistry

logger = logging.getLogger(__name__)


class ContextBuilder:
    """统一的引擎上下文构建器。

    通过 FactorProvider 现算因子值。实时和回测模式共用 build() 入口，由参数区分。
    本类保持只读：因子数据不足时只如实反映在 asset_factors 中，不触发任何计算入队。
    """

    def __init__(
        self,
        db: Session,
        factor_provider: FactorProvider | None = None,
        registry: FactorTemplateRegistry | None = None,
    ) -> None:
        """初始化上下文构建器。

        Args:
            db: SQLAlchemy 同步 Session。
            factor_provider: 因子供应器，未提供时自动创建。
            registry: 因子模板注册表，用于解析策略中的因子引用。
        """
        self._db = db
        self._registry = registry
        self._factor_provider = factor_provider or FactorProvider(db=db, registry=registry)

    def build(
        self,
        config: StrategyConfig,
        trade_date: date,
        index_codes: list[str] | None = None,
        all_bars: dict[tuple[str, date], Any] | None = None,
        precomputed_factors: dict[tuple[str, str], float | None] | None = None,
        cached_universe: list[dict[str, Any]] | None = None,
        cached_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> EngineContext:
        """构建引擎上下文（实时和回测统一入口）。

        实时模式：index_codes=None，从 DB 查询全量活跃指数并现算因子。
        回测模式：传入 index_codes + all_bars + precomputed_factors。

        Args:
            config: 策略配置，用于推导因子引用和过滤资产范围。
            trade_date: 交易日。
            index_codes: 指数代码列表，None 表示实时模式（从 DB 查询全量活跃指数）。
            all_bars: 预加载的指数日线数据（回测模式）。
            precomputed_factors: 预计算的因子值字典（回测模式）。
            cached_universe: 预构建的 universe 列表（回测模式复用，避免每日重构）。
            cached_metadata: 预构建的 asset_metadata 字典（回测模式复用）。

        Returns:
            填充完成的 EngineContext。
        """
        if all_bars is not None:
            return self._build_backtest(
                config,
                trade_date,
                index_codes,
                all_bars,
                precomputed_factors,
                cached_universe,
                cached_metadata,
            )
        return self._build_live(config, trade_date)

    def insufficient_factors(self, context: EngineContext) -> dict[str, str]:
        """识别本次决策中数据不足或计算失败的因子，供服务层生成结构化告警。

        引用无法解析属于配置错误，由配置校验期快速失败，不在此处报告。

        Args:
            context: 已构建的引擎上下文。

        Returns:
            {引用名: MissingReason 值} 映射，仅含未取到完整值的因子。
        """
        index_codes = [item["index_code"] for item in context.universe]
        if not index_codes:
            return {}
        if not context.asset_factors and not context.factor_failures:
            return {}

        missing: dict[str, str] = {}
        failed = context.factor_failures
        keys = {factor_ref for _code, factor_ref in context.asset_factors}
        for factor_ref in keys | failed:
            if factor_ref in failed:
                missing[factor_ref] = MissingReason.COMPUTE_FAILED.value
                continue
            if any(
                context.asset_factors.get((code, factor_ref)) is None for code in index_codes
            ):
                missing[factor_ref] = MissingReason.INSUFFICIENT_DATA.value
        if missing:
            logger.info(
                "因子值不完整: trade_date=%s missing=%s", context.trade_date, missing
            )
        return missing

    # ==================================================================
    # 实时模式
    # ==================================================================

    def _build_live(self, config: StrategyConfig, trade_date: date) -> EngineContext:
        """实时模式：按策略实际参数现算全量活跃指数的因子值。

        自动将 trade_date 回退到有数据的最近交易日，避免当天未收盘时取不到数据。
        """
        from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository

        effective_date = self._resolve_effective_date(trade_date)
        indexes = BenchmarkIndexRepository(self._db).find_active()
        index_codes = self._filter_by_scope(indexes, config.index_codes)

        universe = build_universe_items(indexes, index_codes)
        asset_metadata = {
            idx.index_code: {"name_cn": idx.name_cn, "category": "broad_index"}
            for idx in indexes
            if idx.index_code in index_codes
        }

        asset_matrix = self._factor_provider.load_asset_factor_matrix(
            config, effective_date, index_codes
        )
        market_factors = self._factor_provider.load_market_factors(config, effective_date)

        return EngineContext(
            trade_date=effective_date,
            universe=universe,
            asset_factors=asset_matrix.day(effective_date),
            market_factors=market_factors,
            asset_metadata=asset_metadata,
            factor_failures=set(asset_matrix.failed),
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
        precomputed_factors: dict[tuple[str, str], float | None] | None,
        cached_universe: list[dict[str, Any]] | None = None,
        cached_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> EngineContext:
        """回测模式：使用当日预计算的因子值构建上下文。

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
            universe = build_universe_items([{"index_code": c, "name_cn": c} for c in codes])
        if cached_metadata is not None:
            asset_metadata = cached_metadata
        else:
            asset_metadata = {code: {"name_cn": code, "category": "broad_index"} for code in codes}

        asset_factors: dict[tuple[str, str], float | None] = (
            dict(precomputed_factors) if precomputed_factors else {}
        )

        # 市场级择时因子：从当日因子值中提取择时代理指数的值
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
        )

    # ==================================================================
    # 内部方法
    # ==================================================================

    def _resolve_effective_date(self, trade_date: date) -> date:
        """将有数据的最新交易日作为有效交易日。

        查询 index_daily_bar 表中不超过 trade_date 的最大交易日，
        确保后续因子计算能命中数据。

        Args:
            trade_date: 请求的交易日（可能是当天，但数据尚未就绪）。

        Returns:
            有数据的最新交易日。如果 DB 为空则原样返回 trade_date。
        """
        from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository

        latest = IndexDailyBarRepository(self._db).find_latest_trade_date_before(trade_date)
        if latest is not None and latest < trade_date:
            logger.info("trade_date=%s 无数据，回退到最近交易日=%s", trade_date, latest)
            return latest
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
