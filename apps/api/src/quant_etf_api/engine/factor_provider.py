"""因子供应器：桥接因子计算层与策略引擎层。

职责：
1. 从 StrategyConfig 中收集全部因子引用（策略别名或模板 ID）。
2. 把引用解析为因子模板实例，并按本次实际参数现算因子值：实时模式计算目标
   交易日，回测模式利用预加载的行情与估值一次性批量计算整个区间。
3. 缺失语义由现算结果派生：COMPUTE_FAILED（计算抛异常）与
   INSUFFICIENT_DATA（计算正常返回 None）。

引擎层通过 ``asset_factors`` 的键读取因子值，键就是策略中的引用名，因此别名与
模板 ID 对引擎完全等价。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.catalog import FactorInstance, FactorTemplateRegistry
from quant_etf_api.factors.compute import FactorComputeService, FactorMatrix

if TYPE_CHECKING:
    from quant_etf_api.engine.config import StrategyConfig

logger = logging.getLogger(__name__)


class FactorProvider:
    """因子供应器：按策略配置现算引擎所需的因子值。

    Args:
        db: SQLAlchemy 同步 Session（现算需要读取原始数据）。
        registry: 因子模板注册表。
    """

    def __init__(
        self,
        db: Session | None = None,
        registry: FactorTemplateRegistry | None = None,
    ) -> None:
        """初始化因子供应器。

        Args:
            db: 数据库会话，现算因子值时必需。
            registry: 因子模板注册表，解析策略引用时必需。
        """
        self._db = db
        self._registry = registry
        self._compute = (
            FactorComputeService(db, registry)
            if db is not None and registry is not None
            else None
        )

    @staticmethod
    def collect_required_factor_ids(config: "StrategyConfig") -> list[str]:
        """从策略配置中收集所有需要的因子引用。

        遍历 timing.factors、score.factors、filters.rules，
        排名模块的动量/估值子因子（rank.momentum_factor / rank.valuation_factor），
        以及 regime_rules 中所有 regime 的 score/filters 配置，
        去重后返回完整的引用名列表。

        Args:
            config: 策略配置。

        Returns:
            去重后的因子引用名列表（别名或模板 ID）。
        """
        factor_ids: set[str] = set()

        # 评分因子
        factor_ids.update(config.score.factors.keys())

        # 择时因子
        if config.timing:
            factor_ids.update(config.timing.factors.keys())

        # 过滤规则引用的因子（含跨因子比较的 compare_to）
        if config.filters:
            for rule in config.filters.rules:
                factor_ids.add(rule.factor)
                if rule.compare_to:
                    factor_ids.add(rule.compare_to)

        # 只有排名模块实际按子排名排序时，子因子才参与策略决策。
        # 默认的动量/估值子排名仅用于结果展示，不应把展示字段缺失误报为策略因子缺失。
        if config.rank.sort_by == "momentum_rank" and config.rank.momentum_factor:
            factor_ids.add(config.rank.momentum_factor)
        elif config.rank.sort_by == "valuation_rank" and config.rank.valuation_factor:
            factor_ids.add(config.rank.valuation_factor)

        # regime 条件化配置中引用的因子
        for regime_rule in config.regime_rules.values():
            if regime_rule.score:
                factor_ids.update(regime_rule.score.factors.keys())
            if regime_rule.filters:
                for rule in regime_rule.filters.rules:
                    factor_ids.add(rule.factor)
                    if rule.compare_to:
                        factor_ids.add(rule.compare_to)

        return sorted(factor_ids)

    def resolve_instances(self, config: "StrategyConfig") -> dict[str, FactorInstance]:
        """把策略中的全部因子引用解析为模板实例。

        Args:
            config: 策略配置。

        Returns:
            {引用名: FactorInstance}；引用无法解析时抛 FactorResolutionError。
        """
        if self._registry is None:
            return {}
        return self._registry.resolve_all(
            self.collect_required_factor_ids(config), config.factor_aliases
        )

    def instance_fingerprint(self, config: "StrategyConfig") -> tuple[tuple[str, str, str], ...]:
        """给出「引用名 → 计算实例」的规范化指纹，用于跨变体缓存分区。

        仅用引用名（如 ``return_17d``）无法区分计算语义：同一个别名把
        ``params.period`` 从 17 改成 10 后引用名不变，但因子值完全不同。
        参数高原扫描恰好只在参数维度上做扰动，因此缓存键必须携带
        「模板 ID + 规范化参数 + 模板版本」，否则所有扰动变体会命中同一份
        因子值，表现为"改了参数但指标一字不差"。

        指纹与 :func:`FactorComputeService.compute_matrix` 的同参去重键同源
        （模板 ID + 规范化参数），并额外带上模板版本：模板升级会改变计算
        语义，即使参数相同也不能复用旧值。

        Args:
            config: 策略配置。

        Returns:
            按引用名排序的 ``(引用名, 模板ID, 规范化参数+版本)`` 元组；
            注册表缺失（未注入）时返回空元组，调用方需退化为只有引用名的键。
        """
        if self._registry is None:
            return ()
        instances = self.resolve_instances(config)
        return tuple(
            sorted(
                (
                    ref,
                    instance.template.template_id,
                    f"{instance.template.normalize_text(instance.params)}@"
                    f"{instance.template.version}",
                )
                for ref, instance in instances.items()
            )
        )

    def load_asset_factor_matrix(
        self,
        config: "StrategyConfig",
        trade_date: date,
        index_codes: list[str],
    ) -> FactorMatrix:
        """现算指定交易日的资产因子值，同时保留计算失败信息。

        Args:
            config: 策略配置，用于推导与解析因子引用。
            trade_date: 交易日。
            index_codes: 指数代码列表。

        Returns:
            FactorMatrix，仅含 trade_date 一天。
        """
        instances = list(self.resolve_instances(config).values())
        if not instances or not index_codes:
            return FactorMatrix()
        return self._service().compute_asset_factors(instances, trade_date, index_codes)

    def load_market_factors(
        self,
        config: "StrategyConfig",
        trade_date: date,
    ) -> dict[str, float | None]:
        """现算市场级择时因子值。

        按 config.timing.proxy_index_codes 的顺序取第一个有值的代理指数口径。

        Args:
            config: 策略配置，需包含 timing 配置。
            trade_date: 交易日。

        Returns:
            key=引用名, value=因子数值 的字典。
        """
        if config.timing is None:
            return {}
        wanted = set(config.timing.factors)
        instances = [
            instance
            for ref, instance in self.resolve_instances(config).items()
            if ref in wanted
        ]
        if not instances:
            return {}
        return self._service().compute_market_factors(
            instances, trade_date, list(config.timing.proxy_index_codes)
        )

    def precompute_backtest_factors(
        self,
        config: "StrategyConfig",
        dates: list[date],
        index_codes: list[str],
        all_bars: dict[tuple[str, date], Any],
        all_valuation: dict[tuple[str, date], Any],
        all_macro: dict[str, dict[str, float]] | None = None,
    ) -> dict[date, dict[tuple[str, str], float | None]]:
        """回测模式：用预加载数据一次性计算区间内全部因子值。

        Args:
            config: 策略配置。
            dates: 回测交易日列表。
            index_codes: 回测指数代码列表。
            all_bars: 预加载的指数日线数据，key=(index_code, trade_date)。
            all_valuation: 预加载的估值数据，key=(index_code, trade_date)。
            all_macro: 预加载的宏观指标数据，key=indicator_code, value={period: value}。
                逐点因子计算时按 period <= trade_date 做时点过滤，避免前视偏差。

        Returns:
            三维映射：date → (index_code, 引用名) → 因子数值。
        """
        if self._compute is None:
            logger.warning("FactorProvider 未注入 db 或 registry，回测因子计算不可用")
            return {}
        instances = list(self.resolve_instances(config).values())
        if not instances or not dates or not index_codes:
            return {}

        # 批量因子使用预加载行情的完整交易日历：回测服务会向前预加载预热行情，
        # 若只用回测区间内的日期，复合因子（如 160 日扩散）会从回测首日重新计数。
        calculation_dates = sorted(
            {trade_date for code, trade_date in all_bars if code in index_codes}
        )
        if not calculation_dates:
            calculation_dates = list(dates)

        panels = self._compute.build_panel_context(
            instances, list(index_codes), calculation_dates
        )
        ctx = FactorContext(
            index_bars=all_bars,
            index_valuation=all_valuation or {},
            macro_indicators=all_macro or {},
            panels=panels,
        )
        matrix = self._compute.compute_matrix(
            instances,
            index_codes,
            dates,
            ctx=ctx,
            calculation_ctx=ctx,
            calculation_dates=calculation_dates,
            include_panels=False,
        )
        filled = sum(
            1 for day in matrix.values.values() for value in day.values() if value is not None
        )
        total_cells = len(dates) * len(index_codes) * len(instances)
        logger.info(
            "[factor] 回测因子现算完成: dates=%d index=%d factors=%d 覆盖率=%.2f%% 失败=%d",
            len(dates),
            len(index_codes),
            len(instances),
            round(filled / total_cells * 100, 2) if total_cells else 0.0,
            len(matrix.failed),
        )
        return matrix.values

    def _service(self) -> FactorComputeService:
        """返回现算服务。

        Returns:
            FactorComputeService。

        Raises:
            RuntimeError: 未注入 db 或 registry。
        """
        if self._compute is None:
            raise RuntimeError("FactorProvider 缺少 db 或 registry，无法现算因子值")
        return self._compute
