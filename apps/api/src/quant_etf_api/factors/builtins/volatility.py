"""波动率类因子：17 日、20 日年化波动率（基于指数数据）。"""

from __future__ import annotations

import math
from datetime import date

from quant_etf_api.factors.base import FactorContext, FactorSpec, FactorValue

# A 股全年约 252 个交易日，年化因子为 sqrt(252)
_ANNUALIZE_FACTOR = math.sqrt(252)


class Volatility20dComputer:
    """20 日年化波动率因子计算器。

    计算公式：std(近 20 个日收益率序列) × sqrt(252) × 100。
    使用样本标准差（除以 n-1，贝塞尔修正），与金融实践一致。
    需要至少 21 个连续收盘价才能算出 20 个日收益率。
    结果单位为 %（年化标准差 × 100）。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 20 日年化波动率的因子元数据。"""
        return FactorSpec(
            factor_id="volatility_20d",
            name="20日年化波动率",
            category="volatility",
            version="2.0.0",
            description=(
                "指数近 20 个交易日日收益率的年化标准差（%）。"
                "公式：std(20 个日收益率, ddof=1) × sqrt(252) × 100。需 21 个连续收盘价。"
            ),
            required_data=["index_bars"],
            lookback_days=40,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 20 日年化波动率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，需 21 个收盘价，不足时 numeric 为 None。
            payload 包含 sample_count（实际使用的日收益率数量）。
        """
        # 收集含 trade_date 在内的历史收盘价，按日期升序排列
        closes = sorted(
            [
                (dt, v.close_price)
                for (code, dt), v in ctx.index_bars.items()
                if code == index_code and dt <= trade_date and v.close_price is not None
            ],
            key=lambda x: x[0],
        )

        # 至少需要 21 个收盘价才能算出 20 个日收益率
        if len(closes) < 21:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"sample_count": max(0, len(closes) - 1), "required": 20},
            )

        # 取最近 21 个收盘价，计算 20 个日收益率
        recent_closes = [p for _, p in closes[-21:]]
        daily_returns = [
            (recent_closes[i] - recent_closes[i - 1]) / recent_closes[i - 1]
            for i in range(1, len(recent_closes))
            if recent_closes[i - 1] > 0
        ]

        if len(daily_returns) < 2:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"sample_count": len(daily_returns), "required": 2},
            )

        n = len(daily_returns)
        mean = sum(daily_returns) / n
        # 样本方差（贝塞尔修正，除以 n-1）
        variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
        std_dev = math.sqrt(variance)
        annualized_vol = round(std_dev * _ANNUALIZE_FACTOR * 100, 4)

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=annualized_vol,
            payload={"sample_count": n, "std_daily": round(std_dev, 6)},
        )


class Volatility17dComputer:
    """17 日年化波动率因子计算器。

    计算公式：std(近 17 个日收益率序列) × sqrt(252) × 100。
    使用样本标准差（除以 n-1，贝塞尔修正），与金融实践一致。
    需要至少 18 个连续收盘价才能算出 17 个日收益率。
    结果单位为 %（年化标准差 × 100）。
    """

    @property
    def spec(self) -> FactorSpec:
        """返回 17 日年化波动率的因子元数据。"""
        return FactorSpec(
            factor_id="volatility_17d",
            name="17日年化波动率",
            category="volatility",
            version="1.0.0",
            description=(
                "指数近 17 个交易日日收益率的年化标准差（%）。"
                "公式：std(17 个日收益率, ddof=1) × sqrt(252) × 100。需 18 个连续收盘价。"
            ),
            required_data=["index_bars"],
            lookback_days=35,
        )

    def compute(self, index_code: str, trade_date: date, ctx: FactorContext) -> FactorValue:
        """计算 17 日年化波动率。

        Args:
            index_code: 指数代码。
            trade_date: 目标交易日。
            ctx: FactorContext。

        Returns:
            FactorValue，需 18 个收盘价，不足时 numeric 为 None。
            payload 包含 sample_count（实际使用的日收益率数量）。
        """
        # 收集含 trade_date 在内的历史收盘价，按日期升序排列
        closes = sorted(
            [
                (dt, v.close_price)
                for (code, dt), v in ctx.index_bars.items()
                if code == index_code and dt <= trade_date and v.close_price is not None
            ],
            key=lambda x: x[0],
        )

        # 至少需要 18 个收盘价才能算出 17 个日收益率
        if len(closes) < 18:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"sample_count": max(0, len(closes) - 1), "required": 17},
            )

        # 取最近 18 个收盘价，计算 17 个日收益率
        recent_closes = [p for _, p in closes[-18:]]
        daily_returns = [
            (recent_closes[i] - recent_closes[i - 1]) / recent_closes[i - 1]
            for i in range(1, len(recent_closes))
            if recent_closes[i - 1] > 0
        ]

        if len(daily_returns) < 2:
            return FactorValue(
                factor_id=self.spec.factor_id,
                numeric=None,
                payload={"sample_count": len(daily_returns), "required": 2},
            )

        n = len(daily_returns)
        mean = sum(daily_returns) / n
        # 样本方差（贝塞尔修正，除以 n-1）
        variance = sum((r - mean) ** 2 for r in daily_returns) / (n - 1)
        std_dev = math.sqrt(variance)
        annualized_vol = round(std_dev * _ANNUALIZE_FACTOR * 100, 4)

        return FactorValue(
            factor_id=self.spec.factor_id,
            numeric=annualized_vol,
            payload={"sample_count": n, "std_daily": round(std_dev, 6)},
        )
