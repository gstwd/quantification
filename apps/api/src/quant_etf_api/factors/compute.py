"""因子现算编排：按模板实例从原始数据计算因子值。

本模块是因子值的唯一来源，不读写任何因子值表。每次实时分配、因子查询或研究任务
都先按本次实际参数推导最大回望窗口，一次性预加载所需原始数据，再由模板工厂创建的
计算器批量计算，结果只在本进程的本次执行内复用。

缺失语义收敛为三态：模板引用无法解析（TEMPLATE_UNKNOWN）、计算抛异常
（COMPUTE_FAILED）、计算正常返回 None（INSUFFICIENT_DATA）。
"""

from __future__ import annotations

import bisect
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.strategies.timing_proxy import (
    TimingProxySelection,
    select_grouped_timing_proxy_values,
    select_timing_proxy_values,
)
from quant_etf_api.factors.base import (
    VALUE_SHAPE_MARKET,
    BatchFactorComputer,
    FactorComputer,
    FactorContext,
)
from quant_etf_api.factors.catalog import FactorInstance, FactorTemplateRegistry
from quant_etf_api.factors.macro_period import macro_indicators_as_of
from quant_etf_api.infra.db.models.core import BenchmarkIndexModel
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
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

    def market_index_codes(self, start_date: date) -> list[str]:
        """返回市场级因子统计用的 point-in-time 指数集合（升序）。

        与回测 ``_ensure_market_scope_bars`` 同口径：区间起点仍存续的指数
        （含此后才退市的指数），避免历史日期只统计活到今天的指数（幸存者偏差）。

        Args:
            start_date: 计算区间首日。

        Returns:
            指数代码列表。
        """
        rows = BenchmarkIndexRepository(self._db).find_for_period(start_date)
        return sorted(row.index_code for row in rows)

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
        日线数据额外加载区间起点仍存续的全部指数（与回测同口径）。

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
            bar_codes = sorted(set(bar_codes) | set(self.market_index_codes(min(dates))))

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

        axis = (
            list(panel_dates)
            if panel_dates
            else self._panel_calculation_dates(min(dates), end_date)
        )
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
        # 派生因子的依赖可能是面板类模板：先展开，否则面板不会被装配
        base, derived = _expand_derived(list(instances))
        instance_list = [*base, *derived]
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

    def _panel_calculation_dates(self, start_date: date, end_date: date) -> list[date]:
        """获取复合因子所需的连续交易日计算轴。

        计算轴覆盖 [start_date, end_date] 的全部交易日，并向前预留
        ``_PANEL_FACTOR_WINDOW_DAYS - 1`` 个交易日作为预热，保证区间首日起即可
        产出完整窗口的复合因子值（单日请求时等价于截止日及此前 332 个交易日）。
        交易日以已落库的指数日线为准，避免请求线程调用外部交易日历。

        Args:
            start_date: 需要产出因子值的首个交易日。
            end_date: 计算轴的截止交易日。

        Returns:
            升序交易日列表。
        """
        dates = IndexDailyBarRepository(self._db).find_all_trading_dates(
            start_date - timedelta(days=_PANEL_LOOKBACK_CALENDAR_DAYS), end_date
        )
        first_target = bisect.bisect_left(dates, start_date)
        return dates[max(0, first_target - (_PANEL_FACTOR_WINDOW_DAYS - 1)) :]

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
        派生因子（expr 模板）不直接计算：先把它们绑定的依赖并入本次计算，算完后
        按依赖顺序逐日逐指数求表达式（依赖值也会出现在结果中）。

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
        _assert_unique_instance_ids(instance_list)
        instance_list, derived = _expand_derived(instance_list)
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

        market_ids = {
            instance.instance_id
            for instance in instance_list
            if instance.template.value_shape == VALUE_SHAPE_MARKET
        }
        if batch_groups:
            for _key, computer, instance_ids in batch_groups:
                axis = (
                    panel_axis
                    if _needs_panels(instance_list, instance_ids) and panel_axis
                    else base_axis
                )
                # 市场级模板对任一指数返回同一序列：只算一次，再广播到全部指数
                is_market = bool(market_ids.intersection(instance_ids))
                compute_codes = list(index_codes)[:1] if is_market else list(index_codes)
                for code in compute_codes:
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
                    target_codes = list(index_codes) if is_market else [code]
                    for trade_date, value in results.items():
                        if trade_date not in target_set:
                            continue
                        day_values = matrix.values[trade_date]
                        for target_code in target_codes:
                            for instance_id in instance_ids:
                                day_values[(target_code, instance_id)] = value.numeric

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

        if derived:
            _evaluate_derived(matrix, derived, index_codes, target_dates)

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
        """计算市场级择时因子值（代理选择口径见 :meth:`compute_market_factor_selection`）。

        Args:
            instances: 择时引用的因子实例。
            trade_date: 交易日。
            proxy_codes: 择时代理指数代码列表（按优先级）。

        Returns:
            {instance_id: 因子值}；代理指数全无数据时为 None。
        """
        return dict(self.compute_market_factor_selection(instances, trade_date, proxy_codes).values)

    def compute_market_factor_selection(
        self,
        instances: Sequence[FactorInstance],
        trade_date: date,
        proxy_codes: Sequence[str],
    ) -> TimingProxySelection:
        """计算市场级择时因子并按代理一致性口径选值。

        优先选第一个让全部择时因子都有值的代理；没有代理全覆盖时退回逐因子取
        第一个有值代理。与回测路径共用 :func:`select_timing_proxy_values`。

        Args:
            instances: 择时引用的因子实例。
            trade_date: 交易日。
            proxy_codes: 择时代理指数代码列表（按优先级）。

        Returns:
            代理选择结果，``values`` 的键为 instance_id。
        """
        if not instances:
            return TimingProxySelection()
        matrix = self.compute_matrix(instances, list(proxy_codes), [trade_date])
        day = matrix.day(trade_date)
        return select_timing_proxy_values(
            proxy_codes,
            [instance.instance_id for instance in instances],
            lambda code, ref: day.get((code, ref)),
        )

    def compute_market_factor_series(
        self,
        instances: Sequence[FactorInstance],
        dates: Sequence[date],
        proxy_groups: Sequence[tuple[Sequence[str], Sequence[str]]],
    ) -> dict[date, TimingProxySelection]:
        """批量计算多个交易日的市场级择时因子，并按代理组逐日选值。

        用于实时路径回放择时状态层（以及按因子指定代理的单日计算）：一次
        ``compute_matrix`` 覆盖全部交易日与全部代理指数，再逐日调用
        :func:`select_grouped_timing_proxy_values`，与回测路径同一口径。

        Args:
            instances: 择时引用的因子实例。
            dates: 交易日（升序）。
            proxy_groups: ``(代理指数代码列表, 因子 instance_id 列表)`` 代理组。

        Returns:
            {交易日: 代理选择结果}；``values`` 的键为 instance_id。
        """
        if not instances or not dates:
            return {}
        codes = list(dict.fromkeys(code for proxies, _refs in proxy_groups for code in proxies))
        matrix = self.compute_matrix(instances, codes, list(dates))
        result: dict[date, TimingProxySelection] = {}
        for day in dates:
            values = matrix.day(day)
            result[day] = select_grouped_timing_proxy_values(
                proxy_groups, lambda code, ref, values=values: values.get((code, ref))
            )
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


def _expand_derived(
    instances: Sequence[FactorInstance],
) -> tuple[list[FactorInstance], list[FactorInstance]]:
    """拆出派生因子并补齐其依赖。

    Args:
        instances: 本次请求的实例。

    Returns:
        (需要直接计算的实例, 按依赖顺序排列的派生实例)；依赖与请求中已有的同名
        实例只算一次。
    """
    from quant_etf_api.factors.builtins.derived import ExpressionComputer  # noqa: PLC0415

    base: list[FactorInstance] = []
    derived: list[FactorInstance] = []
    seen: set[str] = set()

    def _visit(instance: FactorInstance) -> None:
        if instance.instance_id in seen:
            return
        seen.add(instance.instance_id)
        computer = instance.computer
        if isinstance(computer, ExpressionComputer):
            # 依赖先入列：保证求值时依赖已有值（派生引用派生同样成立）
            for dep in computer.dependencies.values():
                _visit(dep)
            derived.append(instance)
        else:
            base.append(instance)

    # 请求中直接给出的普通实例优先占位，派生因子的同名依赖复用它们
    for instance in instances:
        if not isinstance(instance.computer, ExpressionComputer):
            _visit(instance)
    for instance in instances:
        _visit(instance)
    return base, derived


def _evaluate_derived(
    matrix: FactorMatrix,
    derived: Sequence[FactorInstance],
    index_codes: Sequence[str],
    dates: Sequence[date],
) -> None:
    """逐日逐指数求派生因子（依赖缺失或数学错误时为 None）。

    Args:
        matrix: 已写入依赖值的矩阵（原地写入派生值）。
        derived: 按依赖顺序排列的派生实例。
        index_codes: 指数代码。
        dates: 交易日。
    """
    for instance in derived:
        computer = instance.computer
        refs = computer.refs  # type: ignore[attr-defined]
        if matrix.failed.intersection(refs):
            matrix.failed.add(instance.instance_id)
        for trade_date in dates:
            day_values = matrix.values.setdefault(trade_date, {})
            for code in index_codes:
                day_values[(code, instance.instance_id)] = computer.evaluate(  # type: ignore[attr-defined]
                    lambda ref, _code=code, _day=day_values: _day.get((_code, ref))
                )


def _assert_unique_instance_ids(instances: Sequence[FactorInstance]) -> None:
    """校验实例 ID 唯一，避免不同参数的同名实例互相覆盖结果。

    矩阵以 ``(交易日, 指数, 实例 ID)`` 为键，实例 ID 就是策略里的引用名；别名
    解析天然保证唯一，但直接用 ``resolve_params`` 取多个同模板不同参数的实例时
    都会得到模板 ID 作为实例 ID，后者会静默覆盖前者。这里在计算前快速失败。

    Args:
        instances: 本次计算的因子实例。

    Raises:
        ValueError: 同一实例 ID 对应了不同的「模板 + 参数」。
    """
    seen: dict[str, tuple[str, str]] = {}
    for instance in instances:
        key = instance.dedup_key
        existing = seen.get(instance.instance_id)
        if existing is not None and existing != key:
            raise ValueError(
                f"因子实例 ID {instance.instance_id} 对应了多个参数组合："
                f"{existing} 与 {key}；同一模板的不同参数必须用不同别名区分"
            )
        seen[instance.instance_id] = key


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

    仅供未实现批量协议的计算器兜底使用（内置模板均已实现批量协议，并在批量
    实现内自行保证只用截止目标日的数据）。估值与宏观指标按公布可见日过滤；
    日线保留（计算器自行按日期切片）。

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
