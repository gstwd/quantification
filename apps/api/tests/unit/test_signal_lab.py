"""量价 / 择时研报研究台：领域计算与只读端点边界测试。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from quant_etf_api.api.routers import signal_lab as router
from quant_etf_api.domain.research import signal_lab as lab
from quant_etf_api.services.signal_lab_service import (
    MAX_RANGE_DAYS,
    SignalLabError,
    SignalLabService,
)


def _bars(close: np.ndarray, volume: np.ndarray | None = None) -> pd.DataFrame:
    """构造单指数日线（OHL 由收盘派生）。"""
    idx = pd.bdate_range("2020-01-01", periods=len(close))
    vol = volume if volume is not None else np.full(len(close), 1e6)
    return lab.clean_bars(
        pd.DataFrame(
            {
                "open": close * 0.99,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": vol,
                "amount": vol * close,
            },
            index=idx,
        )
    )


# ---------------------------------------------------------------- 参数解析


def test_resolve_params_defaults_and_types() -> None:
    """缺省参数取研报默认值，整数参数保持 int。"""
    params = lab.resolve_params(lab.TIMING_SIGNALS["pv_resonance"], {"lag": 5})
    assert params["lag"] == 5 and isinstance(params["lag"], int)
    assert params["bma_window"] == 50
    assert params["bull_threshold"] == pytest.approx(1.125)


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"unknown": 1}, "不支持参数"),
        ({"d": 2.5}, "必须为整数"),
        ({"d": 1000}, "超出范围"),
        ({"d": "abc"}, "必须为数字"),
    ],
)
def test_resolve_params_rejects_invalid(raw: dict[str, object], message: str) -> None:
    """未声明、非整数、越界和非数字参数均被拒绝。"""
    with pytest.raises(ValueError, match=message):
        lab.resolve_params(lab.TIMING_SIGNALS["llt"], raw)


def test_default_warmups_within_backtest_window() -> None:
    """默认参数推导的预热交易日数均远小于回测加载窗口（730 自然日）。"""
    defs = [d for d, _ in lab.CROSS_SECTION_FACTORS.values()] + list(lab.TIMING_SIGNALS.values())
    for definition in defs:
        assert definition.warmup(lab.resolve_params(definition, None)) < 500


# ---------------------------------------------------------------- 基础算子


def test_wma_matches_manual_weights() -> None:
    """WMA 与手工加权一致，窗口不足处为 NaN。"""
    s = pd.Series([1.0, 2.0, 3.0, 4.0])
    out = lab.weighted_moving_average(s, 3)
    assert np.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx((1 * 1 + 2 * 2 + 3 * 3) / 6)
    assert out.iloc[3] == pytest.approx((2 * 1 + 3 * 2 + 4 * 3) / 6)


def test_llt_constant_price_is_flat() -> None:
    """常数价格下 LLT 恒等于价格（滤波器直流增益为 1）。"""
    s = pd.Series(np.full(200, 10.0))
    assert np.allclose(lab.low_latency_trendline(s, 30).to_numpy(), 10.0)


def test_clean_bars_invalidates_partial_ohl() -> None:
    """OHL 任一缺失或 high < low 时三者整体置空，非正量额置空。"""
    df = pd.DataFrame(
        {
            "open": [1.0, 0.0, 1.0],
            "high": [1.1, 1.1, 0.9],
            "low": [0.9, 0.9, 1.0],
            "close": [1.0, 1.0, 1.0],
            "volume": [10.0, 0.0, 10.0],
            "amount": [10.0, 10.0, 10.0],
        }
    )
    out = lab.clean_bars(df)
    assert out.loc[0, ["open", "high", "low"]].notna().all()
    assert out.loc[1, ["open", "high", "low"]].isna().all()
    assert out.loc[2, ["open", "high", "low"]].isna().all()
    assert np.isnan(out.loc[1, "volume"])


# ---------------------------------------------------------------- 信号


def test_llt_signal_follows_trend_direction() -> None:
    """单边上涨后单边下跌：预热后先持仓、后空仓。"""
    close = np.concatenate([np.linspace(100, 200, 150), np.linspace(200, 100, 150)])
    out = lab.signal_llt(_bars(close), {"d": 30})
    assert out["on"].iloc[:30].isna().all()
    assert out["on"].iloc[100] == 1.0
    assert out["on"].iloc[-1] == 0.0


def test_amplitude_scissors_requires_ohl() -> None:
    """OHL 缺失时剪刀差为空，而不是被当成 0。"""
    bars = _bars(np.linspace(100, 110, 100))
    bars[["open", "high", "low"]] = np.nan
    out = lab.signal_amplitude_scissors(bars, {"d": 20})
    assert out["value"].isna().all()
    assert out["on"].isna().all()


def test_pv_resonance_on_is_nan_during_warmup() -> None:
    """价量共振在预热期 on 为 NaN，之后为 0/1。"""
    rng = np.random.default_rng(0)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.01, 400))
    out = lab.signal_pv_resonance(
        _bars(close, rng.uniform(1e6, 2e6, 400)),
        lab.resolve_params(lab.TIMING_SIGNALS["pv_resonance"], None),
    )
    assert out["on"].iloc[:50].isna().all()
    assert set(out["on"].dropna().unique()) <= {0.0, 1.0}


def test_existing_composite_uses_injected_transforms() -> None:
    """现有综合分按 0.6 / 0.4 加权注入的映射函数。"""
    out = lab.signal_existing_composite(
        _bars(np.linspace(100, 120, 120)),
        {"return_period": 10, "drawdown_period": 10, "defensive": 40},
        lambda v: 100.0,
        lambda v: 0.0,
    )
    assert out["value"].dropna().eq(60.0).all()
    assert out["on"].dropna().eq(1.0).all()


def test_hysteresis_holds_state_between_thresholds() -> None:
    """阈值之间保持上一状态。"""
    s = pd.Series([0.0, 0.5, 0.1, -0.5, 0.2])
    assert lab.hysteresis_state(s, 0.3, -0.3).tolist()[1:] == [1.0, 1.0, 0.0, 0.0]


def test_gsisi_positive_when_high_beta_outperforms() -> None:
    """行业收益 = β × 市场收益且市场上涨时，GSISI 为正。"""
    days = pd.bdate_range("2020-01-01", periods=600)
    rng = np.random.default_rng(1)
    mkt_r = rng.normal(0.002, 0.01, len(days))
    proxy = pd.Series(100 * np.cumprod(1 + mkt_r), index=days)
    betas = np.linspace(0.5, 1.5, 12)
    panel = pd.DataFrame(
        {f"I{i}": 100 * np.cumprod(1 + b * mkt_r) for i, b in enumerate(betas)}, index=days
    )
    weekly = lab.gsisi_weekly(panel, proxy, 20)
    value = weekly["value"].dropna()
    week_ret = proxy.groupby(proxy.index.to_period("W-FRI")).last().pct_change()
    up_weeks = week_ret[week_ret > 0].index
    up_values = value[value.index.to_period("W-FRI").isin(up_weeks)]
    assert len(up_values) > 10
    assert (up_values > 0.9).all()


# ---------------------------------------------------------------- 检验统计


def test_cross_section_rank_corr_perfect_and_min_n() -> None:
    """完全同序时 IC = 1；样本不足 min_n 时为 NaN。"""
    a = pd.DataFrame([np.arange(12.0), np.r_[np.arange(5.0), [np.nan] * 7]])
    b = a * 2
    ic = lab.cross_section_rank_corr(a, b, min_n=10)
    assert ic.iloc[0] == pytest.approx(1.0)
    assert np.isnan(ic.iloc[1])


def test_position_returns_lag_two_days() -> None:
    """t+1 收盘成交：t 日收益只受 on_{t−2} 影响。"""
    close = pd.Series([100.0, 100.0, 110.0, 121.0])
    on = pd.Series([1.0, 0.0, 0.0, 0.0])
    strat, bench = lab.position_returns(on, close)
    assert strat.iloc[2] == pytest.approx(0.10)
    assert strat.iloc[3] == pytest.approx(0.0)
    assert bench.iloc[3] == pytest.approx(0.10)


def test_tercile_returns_order() -> None:
    """因子与未来收益同序时 G3 > G2 > G1。"""
    f = pd.DataFrame([np.arange(12.0)] * 3)
    y = f / 100
    g1, g2, g3 = lab.tercile_mean_returns(f, y)
    assert g1 is not None and g2 is not None and g3 is not None
    assert g1 < g2 < g3


# ---------------------------------------------------------------- 服务 / 端点边界


def test_service_rejects_inverted_and_oversized_range() -> None:
    """区间颠倒与超长区间在触达仓库前被拒绝。"""
    service = SignalLabService(MagicMock())
    with pytest.raises(SignalLabError, match="不能晚于"):
        service.timing("000300", "llt", date(2024, 1, 2), date(2024, 1, 1), {})
    with pytest.raises(SignalLabError, match="最多支持"):
        service.timing(
            "000300",
            "llt",
            date(2010, 1, 1),
            date(2010, 1, 1) + pd.Timedelta(days=MAX_RANGE_DAYS + 1),
            {},
        )


def test_service_rejects_unknown_signal() -> None:
    """未知信号返回明确错误。"""
    with pytest.raises(SignalLabError, match="未知择时信号"):
        SignalLabService(MagicMock()).timing(
            "000300", "nope", date(2024, 1, 1), date(2024, 6, 1), {}
        )


def test_router_invalid_params_json_returns_422() -> None:
    """params 非 JSON 对象时返回 422。"""
    with pytest.raises(HTTPException) as exc_info:
        router.get_timing(
            index_code="000300",
            signal="llt",
            start=date(2024, 1, 1),
            end=date(2024, 6, 1),
            params="[1, 2]",
            db=MagicMock(),
        )
    assert exc_info.value.status_code == 422


def test_router_maps_service_error_to_422() -> None:
    """服务层参数错误映射为 422，携带中文原因。"""
    with pytest.raises(HTTPException) as exc_info:
        router.get_cross_section(
            factor="unknown",
            start=date(2024, 1, 1),
            end=date(2024, 6, 1),
            horizon=5,
            index_codes=None,
            params=None,
            db=MagicMock(),
        )
    assert exc_info.value.status_code == 422
    assert "未知截面因子" in str(exc_info.value.detail)
