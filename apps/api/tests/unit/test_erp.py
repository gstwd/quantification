"""ERP / ERP 百分位因子单元测试。

覆盖 B1 修复：
- LPR 取值为"截止 trade_date 的最近一期"（period <= trade_date 中 period 最大者），
  而非数值最大的一期
- trade_date 之后才公布的 LPR 不会被使用（前视偏差防护）
- 无有效 LPR 记录时返回 None
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.erp import ERPComputer, ERPPercentileComputer
from quant_etf_api.factors.macro_period import (
    latest_value_as_of,
    macro_indicators_as_of,
    parse_macro_period,
)


@dataclass
class MockValuationRow:
    """模拟 IndexValuationModel 行，仅保留计算所需字段。"""

    pe: float


def _build_erp_ctx(
    lpr_data: dict[str, float],
    trade_date: date,
    pe: float = 10.0,
) -> FactorContext:
    """构造包含指定 LPR 与当日 PE 的 FactorContext。"""
    return FactorContext(
        index_valuation={("000300", trade_date): MockValuationRow(pe=pe)},
        macro_indicators={"lpr1y": lpr_data},
    )


class TestParseMacroPeriod:
    """parse_macro_period 格式解析测试。"""

    def test_lpr_date_format(self) -> None:
        """LPR 报价日格式 'YYYY-MM-DD' 应正确解析。"""
        assert parse_macro_period("2024-07-22") == date(2024, 7, 22)

    def test_datetime_format(self) -> None:
        """带时间部分的 'YYYY-MM-DD HH:MM:SS' 应正确解析。"""
        assert parse_macro_period("2024-07-22 00:00:00") == date(2024, 7, 22)

    def test_monthly_format(self) -> None:
        """月度格式 'YYYY-MM' 应解析为当月首日。"""
        assert parse_macro_period("2024-07") == date(2024, 7, 1)

    def test_invalid_format_returns_none(self) -> None:
        """无法识别的格式应返回 None。"""
        assert parse_macro_period("2024/07/22") is None


class TestLatestValueAsOf:
    """latest_value_as_of 时点化取值测试。"""

    def test_picks_latest_period_not_max_value(self) -> None:
        """应取 period 日期最大（最新）的一期，而非数值最大的一期。"""
        lpr_data = {"2019-08-20": 4.35, "2024-07-22": 3.45}
        assert latest_value_as_of(lpr_data, date(2024, 8, 1)) == ("2024-07-22", 3.45)

    def test_excludes_future_lpr(self) -> None:
        """trade_date 之后才公布的 LPR 不应被使用。"""
        lpr_data = {"2019-08-20": 4.35, "2024-07-22": 3.45, "2024-08-20": 3.35}
        assert latest_value_as_of(lpr_data, date(2024, 8, 1)) == ("2024-07-22", 3.45)

    def test_no_valid_record_returns_none(self) -> None:
        """所有记录都在 trade_date 之后时返回 None。"""
        assert latest_value_as_of({"2024-08-20": 3.35}, date(2024, 8, 1)) is None


class TestMacroIndicatorsAsOf:
    """macro_indicators_as_of 逐日时点化视图测试（回测路径）。"""

    def test_filters_future_periods(self) -> None:
        """仅保留 period <= trade_date 的记录，其余指标代码保持结构。"""
        all_macro = {
            "lpr1y": {"2019-08-20": 4.35, "2024-07-22": 3.45, "2024-08-20": 3.35},
            "pmi": {"2024-07": 49.5},
        }
        result = macro_indicators_as_of(all_macro, date(2024, 8, 1))
        assert result == {
            "lpr1y": {"2019-08-20": 4.35, "2024-07-22": 3.45},
            "pmi": {"2024-07": 49.5},
        }

    def test_none_input_returns_empty_dict(self) -> None:
        """入参为 None 时应返回空字典。"""
        assert macro_indicators_as_of(None, date(2024, 8, 1)) == {}


class TestERPComputer:
    """ERPComputer 时点化 LPR 取值测试。"""

    _computer = ERPComputer()

    def test_uses_latest_lpr_as_of_trade_date(self) -> None:
        """ERP 应使用截止 trade_date 的最近一期 LPR，而非数值最大值。"""
        trade_date = date(2024, 8, 1)
        lpr_data = {"2019-08-20": 4.35, "2024-07-22": 3.45, "2024-08-20": 3.35}
        ctx = _build_erp_ctx(lpr_data, trade_date, pe=10.0)
        result = self._computer.compute("000300", trade_date, ctx)
        # 1/10 × 100 - 3.45 = 6.55
        assert result.numeric == 6.55
        assert result.payload["lpr_1y"] == 3.45
        assert result.payload["lpr_period"] == "2024-07-22"

    def test_excludes_future_lpr(self) -> None:
        """截止日无任何已公布 LPR 时应返回 None。"""
        trade_date = date(2024, 8, 1)
        ctx = _build_erp_ctx({"2024-08-20": 3.35}, trade_date, pe=10.0)
        result = self._computer.compute("000300", trade_date, ctx)
        assert result.numeric is None


class TestERPPercentileComputer:
    """ERPPercentileComputer 时点化 LPR 取值测试。"""

    _computer = ERPPercentileComputer()

    def test_uses_latest_lpr_as_of_trade_date(self) -> None:
        """历史 ERP 分布应使用截止 trade_date 的最近一期 LPR。"""
        trade_date = date(2024, 8, 1)
        lpr_data = {"2019-08-20": 4.35, "2024-07-22": 3.45, "2024-08-20": 3.35}

        # 11 个历史估值点 + 当日 = 12 个 ERP 样本（>= 10 阈值）
        historical_pes = [20.0, 8.0, 25.0, 5.0, 12.0, 15.0, 6.0, 30.0, 9.0, 11.0, 14.0]
        valuation: dict[tuple[str, date], MockValuationRow] = {}
        for i, pe in enumerate(historical_pes):
            valuation[("000300", trade_date - timedelta(days=30 * (i + 1)))] = MockValuationRow(
                pe=pe
            )
        valuation[("000300", trade_date)] = MockValuationRow(pe=10.0)
        ctx = FactorContext(index_valuation=valuation, macro_indicators={"lpr1y": lpr_data})

        result = self._computer.compute("000300", trade_date, ctx)

        assert result.numeric is not None
        assert result.payload["lpr_1y"] == 3.45
        assert result.payload["lpr_period"] == "2024-07-22"
        # 历史 ERP 均按 LPR=3.45 计算，验证百分位口径
        history = [1.0 / pe * 100.0 - 3.45 for pe in historical_pes] + [6.55]
        current_erp = 6.55
        expected = round(
            sum(1 for v in history if v <= current_erp) / len(history) * 100,
            2,
        )
        assert result.numeric == expected

    def test_no_valid_lpr_returns_none(self) -> None:
        """截止日无任何已公布 LPR 时应返回 None。"""
        trade_date = date(2024, 8, 1)
        valuation = {
            ("000300", trade_date - timedelta(days=30 * i)): MockValuationRow(pe=10.0)
            for i in range(12)
        }
        valuation[("000300", trade_date)] = MockValuationRow(pe=10.0)
        ctx = FactorContext(
            index_valuation=valuation,
            macro_indicators={"lpr1y": {"2024-08-20": 3.35}},
        )
        result = self._computer.compute("000300", trade_date, ctx)
        assert result.numeric is None
