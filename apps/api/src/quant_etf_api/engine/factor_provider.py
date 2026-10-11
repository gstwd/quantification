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

from quant_etf_api.engine.config import iter_factor_refs

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
    def collect_required_factor_ids(
        config: "StrategyConfig", include_display: bool = False
    ) -> list[str]:
        """从策略配置中收集所有需要的因子引用。

        引用清单统一来自 :func:`iter_factor_refs`（评分、择时、过滤及 regime 覆盖）。
        排名模块的动量/估值子因子只用于结果展示，默认不收集：回测不展示子排名，
        也不应把展示字段缺失误报为策略因子缺失；实时模式需要展示时传
        ``include_display=True``。

        Args:
            config: 策略配置。
            include_display: 是否包含仅用于展示的排名子因子。

        Returns:
            去重后的因子引用名列表（别名或模板 ID）。
        """
        return sorted(
            {
                ref
                for module_key, _label, ref in iter_factor_refs(config)
                if include_display or module_key != "rank"
            }
        )

    @staticmethod
    def display_only_factor_ids(config: "StrategyConfig") -> set[str]:
        """返回仅用于展示（不参与决策）的因子引用。

        Args:
            config: 策略配置。

        Returns:
            只出现在排名子因子位置的引用集合。
        """
        decision_refs = set(FactorProvider.collect_required_factor_ids(config))
        display_refs = set(FactorProvider.collect_required_factor_ids(config, include_display=True))
        return display_refs - decision_refs

    def resolve_instances(
        self, config: "StrategyConfig", include_display: bool = False
    ) -> dict[str, FactorInstance]:
        """把策略中的全部因子引用解析为模板实例。

        Args:
            config: 策略配置。
            include_display: 是否包含仅用于展示的排名子因子。

        Returns:
            {引用名: FactorInstance}；引用无法解析时抛 FactorResolutionError。
        """
        if self._registry is None:
            return {}
        return self._registry.resolve_all(
            self.collect_required_factor_ids(config, include_display=include_display),
            config.factor_aliases,
        )

    def market_shape_refs(self, config: "StrategyConfig") -> set[str]:
        """返回解析结果为市场级（非资产级）模板的因子引用。

        引擎按资产检查"因子缺失即剔除"时只针对资产级引用；市场级模板在资产维度
        没有逐资产取值，不能作为剔除依据（与回测历史候选池口径一致）。

        Args:
            config: 策略配置。

        Returns:
            市场级引用名集合；未注入注册表时为空集。
        """
        return {
            ref
            for ref, instance in self.resolve_instances(config).items()
            if instance.template.value_shape != "asset"
        }

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
            FactorMatrix，仅含 trade_date 一天。实时模式同时计算排名展示子因子。
        """
        instances = list(self.resolve_instances(config, include_display=True).values())
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
    ) -> FactorMatrix:
        """回测模式：用预加载数据一次性计算区间内全部因子值。

        Args:
            config: 策略配置。
            dates: 回测交易日列表。
            index_codes: 回测指数代码列表。
            all_bars: 预加载的指数日线数据，key=(index_code, trade_date)。
            all_valuation: 预加载的估值数据，key=(index_code, trade_date)。
            all_macro: 预加载的宏观指标数据，key=indicator_code, value={period: value}。
                计算器按发布可见日（月度指标叠加发布滞后）只取截止目标日已公布的记录，
                避免前视偏差。

        Returns:
            FactorMatrix：``values`` 为 date → (index_code, 引用名) → 因子数值，
            ``failed`` 为计算抛异常的引用名集合（供回测生成"计算失败"告警，
            与"数据不足"区分）。
        """
        if self._compute is None:
            logger.warning("FactorProvider 未注入 db 或 registry，回测因子计算不可用")
            return FactorMatrix()
        instances = list(self.resolve_instances(config).values())
        if not instances or not dates or not index_codes:
            return FactorMatrix()

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
        return matrix

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
