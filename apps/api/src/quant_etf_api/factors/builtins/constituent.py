"""成分股聚合因子：把个股每日指标 / 资金流向汇总为指数级因子。

数据来自 ``FactorContext.panels``：``index_membership``（点时成分与权重）与
``stock_fields``（成员股日频字段的 交易日 × 个股 数组，由面板装配层按模板的
``required_data`` 加载）。计算器保持纯函数、不访问数据库。

加权口径与 ``index_diffusion_ratio`` 一致：当日成分全部带指数权重时按权重加权，否则等权。
有效样本（字段非缺失）的权重占比低于 ``min_coverage`` 时当日缺失——成分股数据不全时
宁可缺失，也不让少数股票代表整个指数。
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np

from quant_etf_api.factors.base import (
    FactorContext,
    FactorSpec,
    FactorValue,
    VALUE_SHAPE_ASSET,
    period_lookback_days,
)

# 面板装配层按表加载的个股字段（两张表的单位各自一致：换手率 %、资金流金额 万元）
STOCK_PANEL_FIELDS: dict[str, tuple[str, ...]] = {
    "stock_daily_basic": ("turnover_rate", "turnover_rate_f", "pe_ttm", "pb", "dv_ttm"),
    "stock_moneyflow": (
        "buy_sm_amount",
        "buy_md_amount",
        "buy_lg_amount",
        "buy_elg_amount",
        "sell_lg_amount",
        "sell_elg_amount",
    ),
}
# 窗口内有效日占比下限（滚动平均时允许个别日缺失）
_WINDOW_VALID_RATIO = 0.8


def _member_vectors(
    members: list[dict[str, Any]], stock_index: dict[str, int]
) -> tuple[np.ndarray, np.ndarray]:
    """成分 → (个股列下标, 权重)；全部带权重时用指数权重，否则等权。"""
    use_weight = bool(members) and all(
        member.get("weight") is not None and float(member["weight"]) >= 0 for member in members
    )
    columns: list[int] = []
    weights: list[float] = []
    for member in members:
        j = stock_index.get(member["stock_code"])
        if j is None:
            continue
        columns.append(j)
        weights.append(float(member["weight"]) if use_weight else 1.0)
    return np.array(columns, dtype=int), np.array(weights, dtype=float)


def _rolling_mean(values: list[float | None], window: int) -> list[float | None]:
    """滚动均值：窗口内有效值占比不低于 80% 时取有效值均值。"""
    out: list[float | None] = []
    need = max(1, math.ceil(window * _WINDOW_VALID_RATIO))
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        valid = [v for v in values[i + 1 - window : i + 1] if v is not None]
        out.append(sum(valid) / len(valid) if len(valid) >= need else None)
    return out


class _ConstituentComputer:
    """成分聚合因子基类：遍历交易日，按当日成分取字段并聚合。"""

    factor_id = ""
    name = ""
    tables: tuple[str, ...] = ()

    def __init__(self, window: int, min_coverage: float) -> None:
        """初始化。

        Args:
            window: 滚动窗口（交易日）。
            min_coverage: 有效样本权重占比下限（0-1）。
        """
        self._window = int(window)
        self._min_coverage = float(min_coverage)

    @property
    def spec(self) -> FactorSpec:
        """因子元数据（模板注册时以模板描述为准）。"""
        return FactorSpec(
            factor_id=self.factor_id,
            name=self.name,
            category="constituent",
            version="1.0.0",
            description=self.name,
            required_data=["index_membership", *self.tables],
            lookback_days=period_lookback_days(self._window),
            value_shape=VALUE_SHAPE_ASSET,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """单日计算（委托批量实现）。"""
        return self.compute_batch(index_code, [trade_date], ctx)[trade_date]

    def compute_batch(
        self, index_code: str, dates: list[date], ctx: FactorContext
    ) -> dict[date, FactorValue]:
        """沿面板交易日轴逐日聚合，再在请求日上取滚动结果。

        Args:
            index_code: 指数代码。
            dates: 请求的交易日。
            ctx: 含 ``index_membership`` 与 ``stock_fields`` 面板的上下文。

        Returns:
            交易日 → FactorValue；面板缺失时全部为缺失值。
        """
        panel = ctx.panels.get("stock_fields") or {}
        membership = (ctx.panels.get("index_membership") or {}).get(index_code) or {}
        axis: list[date] = list(panel.get("dates") or [])
        if not axis or not membership:
            return {d: FactorValue(factor_id=self.factor_id, numeric=None) for d in dates}
        stock_index: dict[str, int] = panel["stock_index"]
        fields: dict[str, np.ndarray] = panel["fields"]
        daily = [
            self._daily_value(i, membership.get(day) or [], stock_index, fields)
            for i, day in enumerate(axis)
        ]
        rolled = self._roll(daily)
        by_date = dict(zip(axis, rolled))
        return {
            d: FactorValue(factor_id=self.factor_id, numeric=self._finish(by_date.get(d)))
            for d in dates
        }

    def _daily_value(
        self,
        row: int,
        members: list[dict[str, Any]],
        stock_index: dict[str, int],
        fields: dict[str, np.ndarray],
    ) -> Any:
        """单日聚合值（子类实现）。"""
        raise NotImplementedError

    def _roll(self, daily: list[Any]) -> list[float | None]:
        """把逐日聚合值滚动成因子值（缺省为滚动均值）。"""
        return _rolling_mean(daily, self._window)

    @staticmethod
    def _finish(value: float | None) -> float | None:
        """输出前取 6 位小数。"""
        return None if value is None else round(float(value), 6)

    def _covered(self, weights: np.ndarray, valid: np.ndarray) -> bool:
        """有效样本权重占比是否达标。"""
        total = float(weights.sum())
        return total > 0 and float(weights[valid].sum()) / total >= self._min_coverage


class ConstituentTurnoverComputer(_ConstituentComputer):
    """成分股加权换手率（%）的 N 日均值。"""

    factor_id = "constituent_turnover"
    name = "成分股加权换手率"
    tables = ("stock_daily_basic",)

    def __init__(self, field: str, window: int, min_coverage: float) -> None:
        """初始化。

        Args:
            field: turnover_rate（总股本口径）或 turnover_rate_f（自由流通口径）。
            window: 滚动窗口。
            min_coverage: 覆盖率下限。
        """
        super().__init__(window, min_coverage)
        self._field = field

    def _daily_value(
        self,
        row: int,
        members: list[dict[str, Any]],
        stock_index: dict[str, int],
        fields: dict[str, np.ndarray],
    ) -> Any:
        """单日聚合值。"""
        columns, weights = _member_vectors(members, stock_index)
        if columns.size == 0:
            return None
        values = fields[self._field][row, columns].astype(float)
        valid = np.isfinite(values)
        if not self._covered(weights, valid):
            return None
        return float((values[valid] * weights[valid]).sum() / weights[valid].sum())


class ConstituentMainflowComputer(_ConstituentComputer):
    """成分股主力净流入占比（%）：N 日（大单 + 特大单）净买入额 / 全部买入额。

    分子分母都来自资金流向表（同为万元），按成分股金额直接加总（大市值股票天然占比更高），
    指数权重只用于覆盖率判定。
    """

    factor_id = "constituent_mainflow"
    name = "成分股主力净流入占比"
    tables = ("stock_moneyflow",)

    def _daily_value(
        self,
        row: int,
        members: list[dict[str, Any]],
        stock_index: dict[str, int],
        fields: dict[str, np.ndarray],
    ) -> Any:
        """单日聚合值。"""
        columns, weights = _member_vectors(members, stock_index)
        if columns.size == 0:
            return None
        net = (
            fields["buy_lg_amount"][row, columns].astype(float)
            + fields["buy_elg_amount"][row, columns]
            - fields["sell_lg_amount"][row, columns]
            - fields["sell_elg_amount"][row, columns]
        )
        total = (
            fields["buy_sm_amount"][row, columns].astype(float)
            + fields["buy_md_amount"][row, columns]
            + fields["buy_lg_amount"][row, columns]
            + fields["buy_elg_amount"][row, columns]
        )
        valid = np.isfinite(net) & np.isfinite(total)
        if not self._covered(weights, valid):
            return None
        return (float(net[valid].sum()), float(total[valid].sum()))

    def _roll(self, daily: list[Any]) -> list[float | None]:
        """窗口内净流入与买入额分别加总后相除（金额加权，不是逐日比例的均值）。"""
        out: list[float | None] = []
        need = max(1, math.ceil(self._window * _WINDOW_VALID_RATIO))
        for i in range(len(daily)):
            if i + 1 < self._window:
                out.append(None)
                continue
            valid = [v for v in daily[i + 1 - self._window : i + 1] if v is not None]
            total = sum(v[1] for v in valid)
            if len(valid) < need or total <= 0:
                out.append(None)
                continue
            out.append(sum(v[0] for v in valid) / total * 100)
        return out


class ConstituentValuationComputer(_ConstituentComputer):
    """成分股估值汇总：中位数或按指数权重的调和平均（PE/PB 剔除非正值）。"""

    factor_id = "constituent_valuation"
    name = "成分股估值汇总"
    tables = ("stock_daily_basic",)

    def __init__(self, field: str, stat: str, min_coverage: float) -> None:
        """初始化。

        Args:
            field: pe_ttm / pb / dv_ttm。
            stat: median（中位数）/ harmonic（权重调和平均，与指数整体 PE 同口径）。
            min_coverage: 覆盖率下限。
        """
        super().__init__(1, min_coverage)
        self._field = field
        self._stat = stat

    def _daily_value(
        self,
        row: int,
        members: list[dict[str, Any]],
        stock_index: dict[str, int],
        fields: dict[str, np.ndarray],
    ) -> Any:
        """单日聚合值。"""
        columns, weights = _member_vectors(members, stock_index)
        if columns.size == 0:
            return None
        values = fields[self._field][row, columns].astype(float)
        # 亏损（PE≤0）与净资产为负（PB≤0）不参与估值汇总；股息率 0 是有效值
        valid = np.isfinite(values) & ((values > 0) if self._field != "dv_ttm" else (values >= 0))
        if not self._covered(weights, valid):
            return None
        picked, picked_weights = values[valid], weights[valid]
        if self._stat == "median":
            return float(np.median(picked))
        positive = picked > 0
        if not positive.any():
            return None
        return float(
            picked_weights[positive].sum() / (picked_weights[positive] / picked[positive]).sum()
        )
