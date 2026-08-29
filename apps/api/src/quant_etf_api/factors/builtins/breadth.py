"""市场宽度因子：全市场活跃指数中收盘价站上 MA20 的比例（基于指数数据）。

市场宽度衡量"当前有多少指数处于上升趋势"，属于市场级因子：
同一交易日所有指数返回相同值，适合用于择时（代理指数读取）或过滤阈值，
不建议用于横截面评分。

由于该因子需要全市场行情，FactorSpec.market_scope=True，
回测服务会额外加载全市场活跃指数的日线数据作为因子上下文，
保证回测与实时预计算（全量活跃指数）口径一致。
"""

from __future__ import annotations

import bisect
from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue


class BreadthMA20Computer:
    """MA20 市场宽度因子计算器。

    对每个交易日统计全市场（上下文内全部指数）中收盘价高于自身 MA20
    的指数占比（0-100）。数据不足 20 条收盘价的指数不参与统计。
    实现 BatchFactorComputer 协议，回测预计算一次覆盖所有交易日。
    """

    _MA_PERIOD = 20

    @property
    def spec(self) -> FactorSpec:
        """返回 MA20 市场宽度的因子元数据。"""
        return FactorSpec(
            factor_id="breadth_ma20_pct",
            name="MA20市场宽度",
            category="technical",
            version="1.0.0",
            description=(
                "全市场活跃指数中收盘价站上自身 20 日均线的占比（0-100），"
                "衡量上升趋势扩散程度。市场级因子，适合择时或过滤。"
            ),
            required_data=["index_bars"],
            lookback_days=40,
            market_scope=True,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 MA20 市场宽度。

        Args:
            index_code: 指数代码（市场级因子，不参与计算）。
            trade_date: 目标交易日。
            ctx: FactorContext，需包含全市场行情数据。

        Returns:
            FactorValue，全市场无足够数据时 numeric 为 None。
        """
        # 按指数分组收集截止 trade_date 的收盘价
        closes_by_code: dict[str, list[float]] = {}
        for (code, dt), v in ctx.index_bars.items():
            if dt <= trade_date and v.close_price is not None:
                closes_by_code.setdefault(code, []).append(v.close_price)

        above_count = 0
        valid_count = 0
        for closes in closes_by_code.values():
            if len(closes) < self._MA_PERIOD:
                continue
            ma20 = sum(closes[-self._MA_PERIOD :]) / self._MA_PERIOD
            valid_count += 1
            if closes[-1] > ma20:
                above_count += 1

        if valid_count == 0:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"reason": "全市场无足够数据"},
            )
        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=round(above_count / valid_count * 100, 2),
            payload={"above_ma20": above_count, "valid_indexes": valid_count},
        )

    def compute_batch(
        self,
        index_code: str,
        dates: list[date],
        ctx: FactorContext,
    ) -> dict[date, FactorValue]:
        """批量计算所有交易日的 MA20 市场宽度。

        市场宽度为市场级因子，对传入的任一指数返回相同序列；
        一次遍历全量数据即可覆盖所有交易日，避免逐日重复构建收盘价分组。

        Args:
            index_code: 指数代码（市场级因子，不参与计算）。
            dates: 需要计算的交易日列表（升序）。
            ctx: FactorContext，包含全量回望数据。

        Returns:
            key=交易日, value=FactorValue 的字典。
        """
        # 构建每个指数的 (日期, 收盘价) 有序序列
        rows_by_code: dict[str, list[tuple[date, float]]] = {}
        for (code, dt), v in ctx.index_bars.items():
            if v.close_price is not None:
                rows_by_code.setdefault(code, []).append((dt, v.close_price))
        per_code: dict[str, tuple[list[date], list[float]]] = {}
        for code, rows in rows_by_code.items():
            rows.sort(key=lambda x: x[0])
            per_code[code] = ([d for d, _ in rows], [p for _, p in rows])

        result: dict[date, FactorValue] = {}
        for trade_date in dates:
            above_count = 0
            valid_count = 0
            for code_dates, code_prices in per_code.values():
                idx = bisect.bisect_right(code_dates, trade_date) - 1
                if idx < 0 or code_dates[idx] != trade_date or idx < self._MA_PERIOD - 1:
                    continue
                ma20 = sum(code_prices[idx - self._MA_PERIOD + 1 : idx + 1]) / self._MA_PERIOD
                valid_count += 1
                if code_prices[idx] > ma20:
                    above_count += 1
            if valid_count == 0:
                result[trade_date] = FactorValue(
                    factor_id=self.spec.factor_id,
                    numeric=None,
                    payload={"reason": "全市场无足够数据"},
                )
                continue
            result[trade_date] = FactorValue(
                factor_id=self.spec.factor_id,
                numeric=round(above_count / valid_count * 100, 2),
                payload={"above_ma20": above_count, "valid_indexes": valid_count},
            )
        return result
