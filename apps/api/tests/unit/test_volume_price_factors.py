"""QuantsPlaybook 第一批晋升因子：计算器、模板登记、择时变换与 usage 校验测试。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd
import pytest

from quant_etf_api.domain.research import signal_lab
from quant_etf_api.engine.transforms import list_transform_names, resonance_score
from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.volume_price import (
    AmountVolatilityComputer,
    PriceVolumeCorrComputer,
    PvResonanceComputer,
)
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.services.strategy_config_service import StrategyConfigService

CODE = "000300"


@dataclass
class _Bar:
    """模拟指数日线行。"""

    close_price: float | None = None
    volume: float | None = None
    turnover: float | None = None


def _series(n: int, seed: int = 0) -> tuple[list[date], np.ndarray, np.ndarray]:
    """生成 n 个交易日的随机收盘价与成交量。"""
    rng = np.random.default_rng(seed)
    days = [d.date() for d in pd.bdate_range("2023-01-02", periods=n)]
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.012, n))
    volume = rng.uniform(1e6, 3e6, n)
    return days, close, volume


def _ctx(days: list[date], close: np.ndarray, volume: np.ndarray) -> FactorContext:
    """按序列构造 FactorContext（成交额 = 成交量 × 收盘）。"""
    bars = {
        (CODE, d): _Bar(close_price=float(c), volume=float(v), turnover=float(v * c))
        for d, c, v in zip(days, close, volume)
    }
    return FactorContext(index_bars=bars)


def _frame(days: list[date], close: np.ndarray, volume: np.ndarray) -> pd.DataFrame:
    """构造领域函数口径的数据帧。"""
    return pd.DataFrame(
        {"close": close, "volume": volume, "amount": volume * close},
        index=pd.DatetimeIndex(days),
    )


# ---------------------------------------------------------------- 计算器


def test_amount_volatility_matches_domain() -> None:
    """成交额波动与研究台领域函数逐日一致，预热期为空且带原因。"""
    days, close, volume = _series(80)
    result = AmountVolatilityComputer(period=20).compute_batch(
        CODE, days, _ctx(days, close, volume)
    )
    expected = signal_lab.factor_amount_volatility(_frame(days, close, volume), {"d": 20})
    assert result[days[10]].numeric is None
    assert "不足" in result[days[10]].payload["reason"]
    for d in days[19:]:
        assert result[d].numeric == pytest.approx(expected.loc[pd.Timestamp(d)], abs=1e-6)
        assert result[d].numeric < 0


def test_price_volume_corr_matches_domain_and_sign() -> None:
    """量价相关与领域函数一致；放量上涨（正相关）时取值为负。"""
    days, close, volume = _series(60)
    result = PriceVolumeCorrComputer(period=20).compute_batch(CODE, days, _ctx(days, close, volume))
    expected = signal_lab.factor_price_volume_corr(_frame(days, close, volume), {"d": 20})
    for d in days[19:]:
        assert result[d].numeric == pytest.approx(expected.loc[pd.Timestamp(d)], abs=1e-6)
    trend = np.linspace(100, 130, 40)
    up_days = days[:40]
    out = PriceVolumeCorrComputer(period=20).compute(
        CODE, up_days[-1], _ctx(up_days, trend, trend * 1e4)
    )
    assert out.numeric == pytest.approx(-1.0, abs=1e-6)


def test_zero_volume_treated_as_missing() -> None:
    """成交量为 0 的指数（如 000941）因子为空，而不是被当作有效 0。"""
    days, close, _ = _series(60)
    ctx = _ctx(days, close, np.zeros(60))
    value = PriceVolumeCorrComputer(period=20).compute(CODE, days[-1], ctx)
    assert value.numeric is None
    assert "缺失" in value.payload["reason"]
    pv = PvResonanceComputer().compute(CODE, days[-1], ctx)
    assert pv.numeric is None


def test_missing_trade_date_reports_reason() -> None:
    """请求日不在序列中时返回空值与原因。"""
    days, close, volume = _series(40)
    value = AmountVolatilityComputer(period=20).compute(
        CODE, days[-1] + timedelta(days=30), _ctx(days, close, volume)
    )
    assert value.numeric is None
    assert value.payload["reason"] == "当日无有效日线数据"


def test_pv_resonance_ratio_matches_domain() -> None:
    """价量共振比 = 指标 / 阈值，与研究台一致；payload 给出市场划分与分量。"""
    days, close, volume = _series(320, seed=3)
    params = signal_lab.resolve_params(signal_lab.TIMING_SIGNALS["pv_resonance"], None)
    result = PvResonanceComputer().compute_batch(CODE, days, _ctx(days, close, volume))
    out = signal_lab.signal_pv_resonance(_frame(days, close, volume), params)
    valid = [d for d in days if result[d].numeric is not None]
    assert len(valid) > 150
    for d in valid[-50:]:
        row = out.loc[pd.Timestamp(d)]
        assert result[d].numeric == pytest.approx(row["value"] / row["threshold"], abs=1e-4)
        payload = result[d].payload
        expected_regime = "bull" if row["threshold"] == params["bull_threshold"] else "bear"
        assert payload["regime"] == expected_regime
        assert (result[d].numeric > 1) == bool(row["on"] == 1.0)


def test_pv_resonance_rejects_out_of_range_params() -> None:
    """计算器构造时拒绝越界参数。"""
    with pytest.raises(ValueError):
        PvResonanceComputer({"bma_window": 1})


# ---------------------------------------------------------------- 模板登记


def test_templates_registered_with_expected_usage() -> None:
    """三个模板已登记：截面因子开放 score/filter/rank，价量共振只开放 timing/filter。"""
    registry = build_default_registry()
    for template_id in ("amount_volatility", "price_volume_corr"):
        template = registry.get(template_id)
        assert template is not None
        assert set(template.usage) == {"score", "filter", "rank"}
        assert template.category == "volume"
        assert template.value_shape == "asset"
    pv = registry.get("pv_resonance")
    assert pv is not None
    assert set(pv.usage) == {"timing", "filter"}
    assert pv.resolve_params({})["bull_threshold"] == pytest.approx(1.125)


def test_template_params_match_research_lab() -> None:
    """模板参数默认值与范围和研究台声明一致，保证两处口径不漂移。"""
    registry = build_default_registry()
    pairs = {
        "amount_volatility": (signal_lab.CROSS_SECTION_FACTORS["amount_volatility"][0], "d"),
        "price_volume_corr": (signal_lab.CROSS_SECTION_FACTORS["price_volume_corr"][0], "d"),
    }
    for template_id, (definition, lab_name) in pairs.items():
        spec = registry.get(template_id).parameter_schema["period"]
        lab_spec = next(p for p in definition.params if p.name == lab_name)
        assert (spec.default, spec.minimum, spec.maximum) == (
            lab_spec.default,
            lab_spec.minimum,
            lab_spec.maximum,
        )
    pv_schema = registry.get("pv_resonance").parameter_schema
    for lab_spec in signal_lab.TIMING_SIGNALS["pv_resonance"].params:
        spec = pv_schema[lab_spec.name]
        assert (spec.default, spec.minimum, spec.maximum) == (
            lab_spec.default,
            lab_spec.minimum,
            lab_spec.maximum,
        )


def test_template_instance_computes_through_registry() -> None:
    """通过注册表解析出的实例可直接计算。"""
    days, close, volume = _series(60)
    instance = build_default_registry().resolve("amount_volatility", {})
    value = instance.computer.compute(CODE, days[-1], _ctx(days, close, volume))
    assert value.numeric is not None


# ---------------------------------------------------------------- 变换与配置校验


def test_resonance_score_step() -> None:
    """共振比严格大于 1 记 100 分，否则 0 分。"""
    assert "resonance_score" in list_transform_names()
    assert resonance_score(1.0) == 0.0
    assert resonance_score(1.0001) == 100.0
    assert resonance_score(0.5) == 0.0


def _service() -> StrategyConfigService:
    """构造不依赖数据库元数据的配置服务。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        SimpleNamespace(factor_id="pv_resonance", usage=["timing", "filter"]),
        SimpleNamespace(factor_id="amount_volatility", usage=["score", "filter", "rank"]),
    ]
    return StrategyConfigService(db=db)


def test_pv_resonance_rejected_in_score() -> None:
    """价量共振不允许进入截面评分。"""
    result = _service().validate_config(
        {"score": {"factors": {"pv_resonance": 1.0}}, "portfolio": {"method": "equal_weight"}}
    )
    assert not result.valid


def test_pv_resonance_accepted_in_timing_and_filter() -> None:
    """价量共振可进 Timing（配 resonance_score）与 Filter；成交额波动可进评分。"""
    result = _service().validate_config(
        {
            "timing": {
                "factors": {"pv_resonance": 1.0},
                "transforms": {"pv_resonance": "resonance_score"},
            },
            "score": {"factors": {"amount_volatility": 1.0}},
            "filter": {"rules": [{"factor": "pv_resonance", "op": "gt", "value": 1.0}]},
            "portfolio": {"method": "equal_weight"},
        }
    )
    assert result.valid, result.errors
