"""成分股聚合因子测试：加权口径、覆盖率门槛、主力净流入金额加权与估值汇总。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from unittest import mock

import numpy as np
import pytest

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.constituent import (
    ConstituentMainflowComputer,
    ConstituentTurnoverComputer,
    ConstituentValuationComputer,
)
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.services.index_factor_panel_service import IndexFactorPanelService

_DAYS = [date(2025, 3, 3) + timedelta(days=i) for i in range(4)]


def _ctx(fields: dict[str, list[list[float]]], weights: tuple[Any, Any] = (0.6, 0.4)) -> FactorContext:
    """两只成分股（A、B）的面板上下文；fields 每项为 [天][股] 的值。"""
    members = [
        {"stock_code": "A", "weight": weights[0]},
        {"stock_code": "B", "weight": weights[1]},
    ]
    return FactorContext(
        panels={
            "index_membership": {"IDX": {day: members for day in _DAYS}},
            "stock_fields": {
                "dates": _DAYS,
                "date_index": {d: i for i, d in enumerate(_DAYS)},
                "stock_index": {"A": 0, "B": 1},
                "fields": {
                    name: np.array(rows, dtype=np.float32) for name, rows in fields.items()
                },
            },
        }
    )


def _values(computer: Any, ctx: FactorContext) -> list[float | None]:
    out = computer.compute_batch("IDX", _DAYS, ctx)
    return [out[d].numeric for d in _DAYS]


class TestTurnover:
    """加权换手率。"""

    def test_weighted_and_rolling(self) -> None:
        """按指数权重加权，窗口 2 的滚动均值。"""
        ctx = _ctx({"turnover_rate_f": [[1, 2], [2, 4], [3, 6], [4, 8]]})
        values = _values(ConstituentTurnoverComputer("turnover_rate_f", 2, 0.8), ctx)
        daily = [0.6 * a + 0.4 * b for a, b in [(1, 2), (2, 4), (3, 6), (4, 8)]]
        assert values[0] is None
        assert values[1] == pytest.approx((daily[0] + daily[1]) / 2)
        assert values[3] == pytest.approx((daily[2] + daily[3]) / 2)

    def test_equal_weight_when_weights_missing(self) -> None:
        """成分不全带权重时等权。"""
        ctx = _ctx({"turnover_rate_f": [[1, 3]] * 4}, weights=(None, 0.4))
        values = _values(ConstituentTurnoverComputer("turnover_rate_f", 1, 0.8), ctx)
        assert values[0] == pytest.approx(2.0)

    def test_coverage_threshold(self) -> None:
        """有效样本权重占比低于门槛时缺失。"""
        nan = float("nan")
        ctx = _ctx({"turnover_rate_f": [[nan, 3]] * 4})
        assert _values(ConstituentTurnoverComputer("turnover_rate_f", 1, 0.8), ctx)[0] is None
        assert _values(ConstituentTurnoverComputer("turnover_rate_f", 1, 0.3), ctx)[0] == 3.0


class TestMainflow:
    """主力净流入占比（窗口内金额加总后相除）。"""

    def test_amount_weighted_ratio(self) -> None:
        """(大单 + 特大单) 净买入 / 全部买入 × 100。"""
        fields = {
            "buy_sm_amount": [[10, 10]] * 4,
            "buy_md_amount": [[10, 10]] * 4,
            "buy_lg_amount": [[20, 0]] * 4,
            "buy_elg_amount": [[10, 0]] * 4,
            "sell_lg_amount": [[5, 10]] * 4,
            "sell_elg_amount": [[5, 0]] * 4,
        }
        values = _values(ConstituentMainflowComputer(2, 0.8), _ctx(fields))
        # 单日：净 = (30 − 10) + (0 − 10) = 10，买入 = 50 + 20 = 70
        assert values[1] == pytest.approx(10 / 70 * 100)


class TestValuation:
    """估值汇总。"""

    def test_median_excludes_losses(self) -> None:
        """中位数剔除非正 PE。"""
        ctx = _ctx({"pe_ttm": [[-5, 20]] * 4}, weights=(0.1, 0.9))
        assert _values(ConstituentValuationComputer("pe_ttm", "median", 0.8), ctx)[0] == 20.0

    def test_harmonic_weighted(self) -> None:
        """调和平均 = Σw / Σ(w/PE)。"""
        ctx = _ctx({"pe_ttm": [[10, 40]] * 4})
        value = _values(ConstituentValuationComputer("pe_ttm", "harmonic", 0.8), ctx)[0]
        assert value == pytest.approx(1.0 / (0.6 / 10 + 0.4 / 40))


class TestRegistration:
    """模板注册与面板装配。"""

    @pytest.mark.parametrize(
        ("template_id", "table"),
        [
            ("constituent_turnover", "stock_daily_basic"),
            ("constituent_mainflow", "stock_moneyflow"),
            ("constituent_valuation", "stock_daily_basic"),
        ],
    )
    def test_required_data(self, template_id: str, table: str) -> None:
        """模板声明成分面板与个股表依赖。"""
        template = build_default_registry().get(template_id)
        assert template is not None
        assert {"index_membership", table} <= set(template.required_data)

    def test_panel_loads_field_arrays(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """面板装配把流式行写成 交易日 × 个股 数组，未知日期 / 个股被忽略。"""
        rows = [
            (_DAYS[0], "A", 1.0, 2.0, 15.0, 1.5, 0.0),
            (_DAYS[1], "B", 3.0, None, 25.0, 2.0, 1.0),
            (date(2020, 1, 1), "A", 9.0, 9.0, 9.0, 9.0, 9.0),
        ]
        fake = mock.MagicMock()
        fake.iter_field_rows.return_value = iter(rows)
        monkeypatch.setattr(
            "quant_etf_api.services.index_factor_panel_service.StockDailyBasicRepository",
            mock.MagicMock(return_value=fake),
        )
        service = IndexFactorPanelService(mock.MagicMock())
        membership = {"IDX": {_DAYS[0]: [{"stock_code": "A"}, {"stock_code": "B"}]}}
        panel = service._build_stock_fields(membership, _DAYS, ["stock_daily_basic"])
        turnover = panel["fields"]["turnover_rate"]
        assert turnover.shape == (4, 2)
        assert turnover[0, 0] == 1.0 and turnover[1, 1] == 3.0
        assert np.isnan(panel["fields"]["turnover_rate_f"][1, 1])
        assert np.isnan(turnover[2:]).all()
