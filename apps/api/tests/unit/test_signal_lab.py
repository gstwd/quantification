"""量价 / 择时研报研究台：领域计算与只读端点边界测试。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from quant_etf_api.api.routers import signal_lab as router
from quant_etf_api.domain.research import signal_lab as lab
from quant_etf_api.engine.config import ScoreConfig, StrategyConfig
from quant_etf_api.engine.transforms import drawdown_score, momentum_score
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.infra.db.repositories.strategy_config import StrategyConfigRepository
from quant_etf_api.services import signal_lab_service as svc_module
from quant_etf_api.services.signal_lab_service import (
    MAX_RANGE_DAYS,
    SignalLabError,
    SignalLabNotFound,
    SignalLabService,
)
from quant_etf_api.services.strategy_config_service import StrategyConfigService


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


def test_existing_composite_block_matches_main_strategy_shape() -> None:
    """默认示例是主策略 r60v90 同构的 timing 块：0.6 / 0.4、65 / 防守阈值、所选代理。"""
    block = lab.existing_composite_timing_block(
        {"return_period": 90, "drawdown_period": 60, "defensive": 40}, "000905"
    )
    config = StrategyConfig(
        strategy_id="t", display_name="t", score=ScoreConfig(factors={}), **block
    )
    assert config.timing is not None
    assert config.timing.factors == {"return_90d": 0.6, "drawdown_60d": 0.4}
    assert config.timing.transforms == {
        "return_90d": "momentum_score",
        "drawdown_60d": "drawdown_score",
    }
    assert (config.timing.thresholds.offensive, config.timing.thresholds.defensive) == (65, 40)
    assert config.timing.proxy_index_codes == ["000905"]
    assert config.factor_aliases["return_90d"].params == {"period": 90}
    assert config.factor_aliases["drawdown_60d"].template_id == "drawdown"


def test_engine_timing_frame_maps_regime_and_degraded_days() -> None:
    """引擎择时帧：降级日为无信号，非防守为持仓，分量列取各因子变换后得分。"""
    idx = pd.bdate_range("2024-01-01", periods=4)
    outputs = [
        (70.0, "offensive", {"a": {"transformed": 80.0}, "b": {"transformed": 55.0}}),
        (50.0, "neutral", {"a": {"transformed": 50.0}, "b": {"transformed": 50.0}}),
        (0.0, "neutral", {"degraded": True, "missing_factors": ["a", "b"]}),
        (30.0, "defensive", {"a": {"transformed": 20.0}, "b": {"raw": None, "status": "missing"}}),
    ]
    calls = iter(outputs)
    out = lab.signal_engine_timing(idx, lambda _ts: next(calls), ["a", "b"])

    assert out["value"].tolist()[:2] == [70.0, 50.0]
    assert np.isnan(out["value"].iloc[2])
    assert out["regime"].tolist() == ["offensive", "neutral", None, "defensive"]
    assert out["on"].tolist()[:2] == [1.0, 1.0]
    assert np.isnan(out["on"].iloc[2])
    assert out["on"].iloc[3] == 0.0
    assert out["degraded"].tolist() == [False, False, True, False]
    assert out["score:a"].tolist()[:2] == [80.0, 50.0]
    assert np.isnan(out["score:b"].iloc[3])


def test_engine_timing_frame_empty_index() -> None:
    """空日历返回带完整列的空帧，而不是缺列。"""
    out = lab.signal_engine_timing(pd.DatetimeIndex([]), lambda _ts: (0.0, "", {}), ["a"])
    assert out.empty
    assert {"value", "regime", "on", "degraded", "score:a"} <= set(out.columns)


def _bar_rows(code: str, close: np.ndarray, start: str = "2023-01-02") -> dict:
    """构造仓库返回格式的日线：{(代码, 日期): 行}。"""
    days = pd.bdate_range(start, periods=len(close))
    return {
        (code, day.date()): SimpleNamespace(
            index_code=code,
            trade_date=day.date(),
            open_price=float(c),
            high_price=float(c) * 1.01,
            low_price=float(c) * 0.99,
            close_price=float(c),
            volume=1e6,
            turnover=1e8,
        )
        for day, c in zip(days, close, strict=True)
    }


def test_service_engine_frame_matches_manual_composite(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务层引擎择时 = 0.6 × momentum_score(N 日收益%) + 0.4 × drawdown_score(M 日回撤%)。

    走真实的 FactorProvider → ContextBuilder → DefaultScoreCalculator 链路（仓库打桩），
    与按公式手算的结果逐日一致，确认研究台与回测同一实现。
    """
    rng = np.random.default_rng(7)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.012, 260))
    rows = _bar_rows("000300", close)
    monkeypatch.setattr(
        IndexDailyBarRepository,
        "find_all_date_range",
        lambda self, start, end, index_codes=None: {
            key: row for key, row in rows.items() if start <= key[1] <= end
        },
    )
    monkeypatch.setattr(IndexValuationRepository, "find_range", lambda self, *a, **k: {})
    monkeypatch.setattr(MacroIndicatorRepository, "find_all_as_map", lambda self: {})
    service = SignalLabService(MagicMock())
    params = {"return_period": 20, "drawdown_period": 15, "defensive": 40}
    config = service._default_timing_config(params, "000300")
    days = pd.DatetimeIndex(sorted(pd.Timestamp(day) for _, day in rows))
    start = days[60].date()
    frame = service._engine_timing_frame(config, days, start)

    series = pd.Series(close, index=days)
    ret = (series / series.shift(20) - 1) * 100
    dd = (series / series.rolling(15).max() - 1) * 100
    expected = 0.6 * ret.map(momentum_score) + 0.4 * dd.map(drawdown_score)
    window = days[days >= pd.Timestamp(start)]
    got = frame["value"].loc[window]
    assert got.notna().all()
    np.testing.assert_allclose(got.to_numpy(), expected.loc[window].round(1).to_numpy(), atol=0.11)
    regimes = frame["regime"].loc[window]
    assert set(regimes) <= {"offensive", "neutral", "defensive"}
    assert ((got <= 40) == (regimes == "defensive")).all()
    # 起点缓冲日之前不计算
    assert np.isnan(frame["value"].loc[days[0]])


def test_service_strategy_timing_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """策略口径：策略不存在 404、未配置择时 / 参数覆盖 / 非综合分信号 422。"""
    service = SignalLabService(MagicMock())
    monkeypatch.setattr(StrategyConfigRepository, "find_by_id", lambda self, sid: None)
    with pytest.raises(SignalLabNotFound, match="不存在"):
        service._strategy_timing_config("missing")

    monkeypatch.setattr(StrategyConfigRepository, "find_by_id", lambda self, sid: object())
    monkeypatch.setattr(
        StrategyConfigService,
        "get_parsed_config",
        lambda self, sid: StrategyConfig(
            strategy_id=sid, display_name="无择时", score=ScoreConfig(factors={"a": 1.0})
        ),
    )
    with pytest.raises(SignalLabError, match="未配置择时"):
        service._strategy_timing_config("no_timing")

    with pytest.raises(SignalLabError, match="只适用于择时综合分"):
        service.timing("000300", "llt", date(2024, 1, 1), date(2024, 6, 1), {}, strategy_id="s")
    with pytest.raises(SignalLabError, match="不接受参数覆盖"):
        service.timing(
            "000300",
            "existing_composite",
            date(2024, 1, 1),
            date(2024, 6, 1),
            {"defensive": 30},
            strategy_id="s",
        )


def test_service_strategy_timing_uses_strategy_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """策略口径只抽取 timing 与别名，定义按策略阈值、权重与代理生成。"""
    full = StrategyConfig(
        strategy_id="mainline",
        display_name="主策略",
        version="3.2.0",
        score=ScoreConfig(factors={"return_17d": 1.0}),
        factor_aliases={
            "return_60d": {"template_id": "return", "params": {"period": 90}},
            "max_drawdown_60d": {"template_id": "drawdown", "params": {"period": 60}},
        },
        timing={
            "factors": {"return_60d": 0.6, "max_drawdown_60d": 0.4},
            "transforms": {"return_60d": "momentum_score", "max_drawdown_60d": "drawdown_score"},
            "thresholds": {"offensive": 65, "defensive": 40},
            "proxy_index_codes": ["000300"],
        },
    )
    monkeypatch.setattr(StrategyConfigRepository, "find_by_id", lambda self, sid: object())
    monkeypatch.setattr(StrategyConfigService, "get_parsed_config", lambda self, sid: full)
    config = SignalLabService(MagicMock())._strategy_timing_config("mainline")

    assert config.score.factors == {}
    assert config.timing == full.timing
    definition = svc_module._strategy_definition(config)
    assert definition.label == "策略择时：主策略"
    assert "0.6 × momentum_score(return_60d)" in definition.formula
    assert "return_60d = return(period=90)" in definition.formula
    assert [line.value for line in definition.reference_lines] == [40.0, 65.0]
    assert definition.params == []


def test_strategy_definition_rules_mode_does_not_crash() -> None:
    """规则模式的策略择时定义可生成（阈值参考线取配置默认值，回归 UnboundLocalError）。"""
    config = StrategyConfig(
        strategy_id="rules_timing",
        display_name="规则择时",
        version="1.0.0",
        score=ScoreConfig(factors={}),
        factor_aliases={"pv_res": {"template_id": "pv_resonance", "params": {}}},
        timing={
            "combine": "rules",
            "proxy_index_codes": ["000852"],
            "rules": {
                "offensive": {
                    "logic": "AND",
                    "rules": [{"factor": "pv_res", "op": "gt", "value": 1}],
                },
                "defensive": {
                    "logic": "AND",
                    "rules": [{"factor": "pv_res", "op": "lte", "value": 1}],
                },
            },
        },
    )
    definition = svc_module._strategy_definition(config)
    assert definition.formula.startswith("规则模式：")
    assert len(definition.reference_lines) == 2


def test_catalog_lists_only_active_non_variant_timing_strategies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """目录只列出启用、非变体、配置了 timing 块的策略。"""
    rows = [
        SimpleNamespace(
            strategy_id="a", display_name="A", version="1", is_variant=False,
            config_json={"timing": {"factors": {"x": 1}}},
        ),
        SimpleNamespace(
            strategy_id="b", display_name="B", version="1", is_variant=True,
            config_json={"timing": {"factors": {"x": 1}}},
        ),
        SimpleNamespace(
            strategy_id="c", display_name="C", version="1", is_variant=False, config_json={}
        ),
    ]
    monkeypatch.setattr(StrategyConfigRepository, "find_all_active", lambda self: rows)
    monkeypatch.setattr(BenchmarkIndexRepository, "find_active", lambda self: [])
    catalog = SignalLabService(MagicMock()).catalog()
    assert [item.strategy_id for item in catalog.timing_strategies] == ["a"]
    composite = next(d for d in catalog.timing_signals if d.key == "existing_composite")
    assert [line.value for line in composite.reference_lines] == [40.0, 65.0]


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
