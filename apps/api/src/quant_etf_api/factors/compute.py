"""因子现算编排：按模板实例从原始数据计算因子值。

本模块是因子值的唯一来源，不读写任何因子值表。每次实时分配、因子查询或研究任务
都先按本次实际参数推导最大回望窗口，一次性预加载所需原始数据，再由模板工厂创建的
计算器批量计算，结果只在本进程的本次执行内复用。

缺失语义收敛为三态：模板引用无法解析（TEMPLATE_UNKNOWN）、计算抛异常
（COMPUTE_FAILED）、计算正常返回 None（INSUFFICIENT_DATA）。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.factors.base import (
    BatchFactorComputer,
    FactorComputer,
    FactorContext,
)
from quant_etf_api.factors.catalog import FactorInstance, FactorTemplateRegistry
from quant_etf_api.factors.macro_period import macro_indicators_as_of
from quant_etf_api.infra.db.models.core import BenchmarkIndexModel
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.schemas.factor import CrossSectionRow
from quant_etf_api.schemas.signal import FactorRow

logger = logging.getLogger(__name__)

# 指数扩散的 160 日均线、150 日快线与 25 日慢线需要 333 个连续交易日（含目标日）。
_PANEL_FACTOR_WINDOW_DAYS = 333
# 面板装配的回看折算天数：333 个交易日约需 550 自然日，加缓冲取 800。
_PANEL_LOOKBACK_CALENDAR_DAYS = 800

_PANEL_DATA_NAMES = {
    "index_membership",
    "stock_closes",
    "industry_selection",
    "index_industry_exposure",
}
# 行业面板类依赖：仅这些依赖需要装配 RRG 行业选择与暴露
_INDUSTRY_PANEL_DATA_NAMES = {"industry_selection", "index_industry_exposure"}

# 因子计算所需的宏观指标口径
_MACRO_CODES = ["lpr1y", "lpr5y", "cpi", "pmi"]


@dataclass
class FactorMatrix:
    """一次现算的因子值矩阵与失败信息。

    Attributes:
        values: 交易日 → (index_code, instance_id) → 因子数值。
            None 表示计算器正常返回空值（基础数据不足）。
        failed: 计算抛出异常的实例 ID 集合。
    """

    values: dict[date, dict[tuple[str, str], float | None]] = field(default_factory=dict)
    failed: set[str] = field(default_factory=set)

    def day(self, trade_date: date) -> dict[tuple[str, str], float | None]:
        """返回指定交易日的扁平因子值字典。

        Args:
            trade_date: 交易日。

        Returns:
            key=(index_code, instance_id), value=因子数值。
        """
        return self.values.get(trade_date, {})


class FactorComputeService:
    """按模板实例从原始数据现算因子值的服务。

    Args:
        db: SQLAlchemy 同步 Session。
        registry: 因子模板注册表。
    """

    def __init__(self, db: Session, registry: FactorTemplateRegistry) -> None:
        """初始化现算服务。

        Args:
            db: SQLAlchemy 同步 Session。
            registry: 因子模板注册表。
        """
        self._db = db
        self._registry = registry

    # ==================================================================
    # 查询辅助
    # ==================================================================

    def registry(self) -> FactorTemplateRegistry:
        """返回本服务使用的模板注册表。"""
        return self._registry

    def resolve(
        self,
        ref: str,
        factor_aliases: Mapping[str, Any] | None = None,
    ) -> FactorInstance:
        """把因子引用解析为可执行实例。

        Args:
            ref: 因子引用名（策略别名或模板 ID）。
            factor_aliases: 策略别名声明映射。

        Returns:
            FactorInstance。
        """
        return self._registry.resolve(ref, factor_aliases)

    def latest_bar_date(self) -> date | None:
        """返回全部指数日线中的最新交易日。"""
        return IndexDailyBarRepository(self._db).get_latest_trade_date()

    def active_index_codes(self) -> list[str]:
        """返回全部活跃指数代码（升序）。"""
        rows = (
            self._db.query(BenchmarkIndexModel.index_code)
            .filter(BenchmarkIndexModel.is_active.is_(True))
            .order_by(BenchmarkIndexModel.index_code)
            .all()
        )
        return [row[0] for row in rows]

    # ==================================================================
    # 上下文装配
    # ==================================================================

    def build_context(
        self,
        instances: Sequence[FactorInstance],
        index_codes: Sequence[str],
        dates: Sequence[date],
        *,
        panel_dates: Sequence[date] | None = None,
        include_panels: bool = True,
    ) -> FactorContext:
        """按本次实际参数装配因子计算上下文。

        回望窗口由本次实例的最大 lookback 推导；出现市场级模板（如市场宽度）时，
        日线数据额外加载全部活跃指数，保证实时与回测口径一致。

        Args:
            instances: 本次计算的因子实例。
            index_codes: 指数代码列表。
            dates: 需要产出因子值的交易日（升序）。
            panel_dates: 面板装配所需的连续交易日轴；None 时按复合因子窗口推导。
            include_panels: 是否装配成分股、行业归属等复合因子的高成本面板。

        Returns:
            填充了 index_bars / index_valuation / macro_indicators / panels 的 FactorContext。
        """
        instance_list = list(instances)
        if not dates or not index_codes:
            return FactorContext()
        lookback_days = FactorTemplateRegistry.max_lookback_days(instance_list)
        end_date = max(dates)
        start_date = min(dates) - timedelta(days=lookback_days)

        bar_codes = list(index_codes)
        if any(instance.template.market_scope for instance in instance_list):
            bar_codes = sorted(set(bar_codes) | set(self.active_index_codes()))

        ctx = FactorContext(
            index_bars=IndexDailyBarRepository(self._db).find_all_date_range(
                start_date, end_date, bar_codes
            ),
            index_valuation=IndexValuationRepository(self._db).find_range(
                start_date, end_date, list(index_codes)
            ),
            macro_indicators=self._load_macro_indicators(),
        )
        if not include_panels:
            return ctx

        panel_instances = [
            instance
            for instance in instance_list
            if _PANEL_DATA_NAMES.intersection(instance.template.required_data)
        ]
        if not panel_instances:
            return ctx

        axis = list(panel_dates) if panel_dates else self._panel_calculation_dates(end_date)
        ctx.panels = self.build_panel_context(instance_list, index_codes, axis, lookback_days)
        return ctx

    def build_panel_context(
        self,
        instances: Sequence[FactorInstance],
        index_codes: Sequence[str],
        dates: Sequence[date],
        lookback_days: int | None = None,
    ) -> dict[str, Any]:
        """按本次实例装配复合因子面板。

        供已自行预加载行情、不再走 build_context 的回测路径复用。

        Args:
            instances: 本次计算的因子实例。
            index_codes: 指数代码列表。
            dates: 面板所需的连续交易日轴。
            lookback_days: 本次最大回望自然日数；None 时按实例推导。

        Returns:
            面板字典；无面板类实例或输入不完整时为空字典。
        """
        instance_list = list(instances)
        panel_instances = [
            instance
            for instance in instance_list
            if _PANEL_DATA_NAMES.intersection(instance.template.required_data)
        ]
        if not panel_instances or not index_codes or not dates:
            return {}
        lookback = (
            lookback_days
            if lookback_days is not None
            else FactorTemplateRegistry.max_lookback_days(instance_list)
        )
        include_industry = any(
            _INDUSTRY_PANEL_DATA_NAMES.intersection(instance.template.required_data)
            for instance in panel_instances
        )
        return self._build_panels(list(index_codes), list(dates), lookback, include_industry)

    def _load_macro_indicators(self) -> dict[str, dict[str, float]]:
        """加载宏观指标全量历史，供逐日时点过滤使用。

        Returns:
            key=indicator_code, value={period: value} 的映射。
        """
        indicators: dict[str, dict[str, float]] = {}
        for row in MacroIndicatorRepository(self._db).find_by_codes(_MACRO_CODES):
            code = row.indicator_code
            indicators.setdefault(code, {})[str(row.period_date or row.period)] = row.value
        return indicators

    def _panel_calculation_dates(self, end_date: date) -> list[date]:
        """获取复合因子所需的连续交易日计算轴。

        交易日以已落库的指数日线为准，避免请求线程调用外部交易日历。

        Args:
            end_date: 计算轴的截止交易日。

        Returns:
            升序交易日列表，最多包含截止日及此前 333 个交易日。
        """
        dates = IndexDailyBarRepository(self._db).find_all_trading_dates(
            end_date - timedelta(days=_PANEL_LOOKBACK_CALENDAR_DAYS), end_date
        )
        return dates[-_PANEL_FACTOR_WINDOW_DAYS:]

    def _build_panels(
        self,
        index_codes: list[str],
        dates: list[date],
        lookback_days: int,
        include_industry_panels: bool,
    ) -> dict[str, Any]:
        """装配复合因子面板。

        Args:
            index_codes: 指数代码列表。
            dates: 面板所需的连续交易日轴。
            lookback_days: 本次最大回望自然日数。
            include_industry_panels: 是否装配 RRG 所需的行业选择与暴露面板。

        Returns:
            面板字典；输入不完整时为空字典。
        """
        if not index_codes or not dates:
            return {}
        from quant_etf_api.services.index_factor_panel_service import IndexFactorPanelService

        return IndexFactorPanelService(self._db).build_panels(
            index_codes=index_codes,
            dates=dates,
            lookback_natural_days=lookback_days,
            include_industry_panels=include_industry_panels,
        )

    # ==================================================================
    # 计算
    # ==================================================================

    def compute_matrix(
        self,
        instances: Sequence[FactorInstance],
        index_codes: Sequence[str],
        dates: Sequence[date],
        *,
        ctx: FactorContext | None = None,
        calculation_dates: Sequence[date] | None = None,
        include_panels: bool = True,
        panel_dates: Sequence[date] | None = None,
        calculation_ctx: FactorContext | None = None,
    ) -> FactorMatrix:
        """批量计算因子值矩阵。

        同一模板 + 同一规范化参数的多个别名只创建一个计算器、只算一次，结果按实例
        ID 展开。支持批量协议的模板一次遍历覆盖所有交易日，其余模板按交易日逐点计算。

        Args:
            instances: 本次计算的因子实例。
            index_codes: 指数代码列表。
            dates: 需要产出因子值的交易日（升序）。
            ctx: 已装配的上下文；None 时按本次参数装配。
            calculation_dates: 计算轴（可覆盖 dates 之外的预热交易日）；
                None 时按本次需求推导。
            include_panels: 是否装配复合因子面板。
            panel_dates: 面板装配的连续交易日轴。
            calculation_ctx: 批量计算使用的上下文；None 时复用 ctx。

        Returns:
            FactorMatrix。
        """
        instance_list = list(instances)
        target_dates = sorted(set(dates))
        if not instance_list or not index_codes or not target_dates:
            return FactorMatrix()
        matrix = FactorMatrix(values={day: {} for day in target_dates})

        context = ctx or self.build_context(
            instance_list,
            index_codes,
            target_dates,
            panel_dates=panel_dates,
            include_panels=include_panels,
        )
        base_axis = sorted(calculation_dates) if calculation_dates else target_dates
        batch_ctx = calculation_ctx or context
        target_set = set(target_dates)

        groups = _group_instances(instance_list)
        batch_groups = [
            (key, computer, ids)
            for key, computer, ids in groups
            if isinstance(computer, BatchFactorComputer)
        ]
        point_groups = [
            (key, computer, ids)
            for key, computer, ids in groups
            if not isinstance(computer, BatchFactorComputer)
        ]

        panel_axis = list(context.panels.get("calculation_dates") or [])
        start = time.perf_counter()

        if batch_groups:
            for code in index_codes:
                for _key, computer, instance_ids in batch_groups:
                    axis = (
                        panel_axis
                        if _needs_panels(instance_list, instance_ids) and panel_axis
                        else base_axis
                    )
                    try:
                        results = computer.compute_batch(code, axis, batch_ctx)
                    except Exception:
                        logger.warning(
                            "批量因子计算失败: code=%s factor=%s",
                            code,
                            computer.spec.factor_id,
                            exc_info=True,
                        )
                        matrix.failed.update(instance_ids)
                        continue
                    for trade_date, value in results.items():
                        if trade_date in target_set:
                            for instance_id in instance_ids:
                                matrix.values[trade_date][(code, instance_id)] = value.numeric

        if point_groups:
            for trade_date in target_dates:
                dated_ctx = _as_of_context(context, trade_date)
                for code in index_codes:
                    for _key, computer, instance_ids in point_groups:
                        try:
                            value = computer.compute(code, trade_date, dated_ctx)
                        except Exception:
                            logger.warning(
                                "因子计算失败: date=%s code=%s factor=%s",
                                trade_date,
                                code,
                                computer.spec.factor_id,
                                exc_info=True,
                            )
                            matrix.failed.update(instance_ids)
                            continue
                        for instance_id in instance_ids:
                            matrix.values[trade_date][(code, instance_id)] = value.numeric

        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        filled = sum(
            1 for day in matrix.values.values() for value in day.values() if value is not None
        )
        total = len(target_dates) * len(index_codes) * len(instance_list)
        logger.info(
            "[factor] 现算完成: dates=%d index=%d factors=%d 覆盖率=%.2f%% 失败=%d 耗时=%sms",
            len(target_dates),
            len(index_codes),
            len(instance_list),
            round(filled / total * 100, 2) if total else 0.0,
            len(matrix.failed),
            elapsed_ms,
        )
        return matrix

    def compute_asset_factors(
        self,
        instances: Sequence[FactorInstance],
        trade_date: date,
        index_codes: Sequence[str],
        *,
        include_panels: bool = True,
    ) -> FactorMatrix:
        """计算单个交易日、多个指数的资产因子值。

        Args:
            instances: 本次计算的因子实例。
            trade_date: 交易日。
            index_codes: 指数代码列表。
            include_panels: 是否装配复合因子面板。

        Returns:
            FactorMatrix，仅含 trade_date 一天。
        """
        return self.compute_matrix(
            instances, index_codes, [trade_date], include_panels=include_panels
        )

    def compute_market_factors(
        self,
        instances: Sequence[FactorInstance],
        trade_date: date,
        proxy_codes: Sequence[str],
    ) -> dict[str, float | None]:
        """计算市场级择时因子：按代理指数顺序取第一个有值的口径。

        Args:
            instances: 择时引用的因子实例。
            trade_date: 交易日。
            proxy_codes: 择时代理指数代码列表（按优先级）。

        Returns:
            {instance_id: 因子值}；代理指数全无数据时为 None。
        """
        result: dict[str, float | None] = {}
        if not instances:
            return result
        matrix = self.compute_matrix(instances, list(proxy_codes), [trade_date])
        day = matrix.day(trade_date)
        for instance in instances:
            value: float | None = None
            for code in proxy_codes:
                candidate = day.get((code, instance.instance_id))
                if candidate is not None:
                    value = candidate
                    break
            result[instance.instance_id] = value
        return result

    # ==================================================================
    # 因子查询
    # ==================================================================

    def compute_cross_section(
        self,
        instance: FactorInstance,
        trade_date: date | None = None,
        index_codes: Sequence[str] | None = None,
    ) -> tuple[date, list[CrossSectionRow]]:
        """计算指定因子实例在单个交易日的横截面（含指数中文名）。

        Args:
            instance: 因子实例。
            trade_date: 交易日；None 时取最新有行情的交易日。
            index_codes: 指数代码列表；None 时取全部活跃指数。

        Returns:
            (实际交易日, 横截面行列表) 二元组。

        Raises:
            ValueError: 库中没有任何指数行情数据。
        """
        target = trade_date or self.latest_bar_date()
        if target is None:
            raise ValueError("无任何指数行情数据，无法计算因子")
        codes = list(index_codes) if index_codes else self.active_index_codes()
        if not codes:
            return target, []
        matrix = self.compute_matrix([instance], codes, [target])
        day = matrix.day(target)
        names = self._index_names(codes)
        rows = [
            CrossSectionRow(
                index_code=code,
                name_cn=names.get(code, code),
                factor_value_numeric=day.get((code, instance.instance_id)),
            )
            for code in sorted(codes)
        ]
        return target, rows

    def compute_series(
        self,
        instance: FactorInstance,
        index_code: str,
        start_date: date,
        end_date: date,
    ) -> list[FactorRow]:
        """计算单因子实例在单指数上的时间序列。

        Args:
            instance: 因子实例。
            index_code: 指数代码。
            start_date: 起始日期（含）。
            end_date: 截止日期（含）。

        Returns:
            按交易日升序排列的 FactorRow 列表。
        """
        if start_date > end_date:
            return []
        dates = IndexDailyBarRepository(self._db).find_trading_dates(
            start_date, end_date, [index_code]
        )
        if not dates:
            return []
        matrix = self.compute_matrix([instance], [index_code], dates)
        rows: list[FactorRow] = []
        for trade_date in dates:
            rows.append(
                FactorRow(
                    trade_date=trade_date,
                    index_code=index_code,
                    factor_id=instance.template_id,
                    params=dict(instance.params),
                    factor_value_numeric=matrix.day(trade_date).get(
                        (index_code, instance.instance_id)
                    ),
                )
            )
        return rows

    def _index_names(self, index_codes: Sequence[str]) -> dict[str, str]:
        """查询指数中文名。

        Args:
            index_codes: 指数代码列表。

        Returns:
            {index_code: name_cn} 映射。
        """
        if not index_codes:
            return {}
        rows = (
            self._db.query(BenchmarkIndexModel.index_code, BenchmarkIndexModel.name_cn)
            .filter(BenchmarkIndexModel.index_code.in_(list(index_codes)))
            .all()
        )
        return {row[0]: row[1] for row in rows}


def _needs_panels(instances: Sequence[FactorInstance], instance_ids: Sequence[str]) -> bool:
    """判断给定实例 ID 中是否存在依赖复合面板的模板。

    Args:
        instances: 本次全部因子实例。
        instance_ids: 待判定的实例 ID 列表。

    Returns:
        存在依赖面板的实例时返回 True。
    """
    wanted = set(instance_ids)
    return any(
        instance.instance_id in wanted
        and bool(_PANEL_DATA_NAMES.intersection(instance.template.required_data))
        for instance in instances
    )


def _group_instances(
    instances: Sequence[FactorInstance],
) -> list[tuple[tuple[str, str], FactorComputer, list[str]]]:
    """按「模板 + 规范参数」归并实例，同参只创建一个计算器。

    Args:
        instances: 本次计算的因子实例。

    Returns:
        (去重键, 计算器, 实例 ID 列表) 列表。
    """
    groups: dict[tuple[str, str], tuple[FactorComputer, list[str]]] = {}
    for instance in instances:
        entry = groups.get(instance.dedup_key)
        if entry is None:
            groups[instance.dedup_key] = (instance.computer, [instance.instance_id])
        else:
            entry[1].append(instance.instance_id)
    return [(key, computer, instance_ids) for key, (computer, instance_ids) in groups.items()]


def _as_of_context(ctx: FactorContext, trade_date: date) -> FactorContext:
    """构建截止 trade_date 的时点化上下文，避免历史日期使用未来数据。

    估值与宏观指标按公布/落库日期过滤；日线保留（计算器自行按日期切片）。

    Args:
        ctx: 全量上下文。
        trade_date: 目标交易日。

    Returns:
        过滤后的 FactorContext（与原上下文共用日线与面板对象）。
    """
    return FactorContext(
        index_bars=ctx.index_bars,
        index_valuation={
            (code, bar_date): value
            for (code, bar_date), value in ctx.index_valuation.items()
            if bar_date <= trade_date
        },
        macro_indicators=macro_indicators_as_of(ctx.macro_indicators, trade_date),
        panels=ctx.panels,
    )
