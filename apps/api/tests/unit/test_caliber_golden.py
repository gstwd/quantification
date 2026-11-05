"""口径金样本回归测试：把回测主链路的数值口径固定在提交的基准文件上。

在固定的合成行情上，用真实的因子现算、上下文构建、策略引擎、执行器与汇总指标跑三条
代表性策略，把逐日结果与汇总指标同 ``tests/golden/caliber_backtest.json`` 逐项比对：

1. ``rotation_weekly_close``：横截面 rank 评分 + 过滤 + TopN 等权、周度选股腿，
   T+1 收盘执行；
2. ``timing_weighted_open``：单资产加权择时（变换 + 状态层滞回 / 确认）+ regime 仓位表，
   T+1 开盘执行；
3. ``risk_parity_overlay_close``：排名缓冲带 + 风险平价 + 波动率目标 / 移动止损叠加层
   + 偏离调仓，T+1 收盘执行。

数据库、行情预加载与全市场行情补充被替换为内存桩；交易日历用合成交易日集合桩替。
这里冻结的是**计算口径**（drift_v1 记账、执行模型、基准决策日归因、因子现算、引擎
管线、路径依赖叠加层、汇总指标），不覆盖数据接入与落库。

任何让本测试失败的改动都改变了历史回测的可比性。确认口径变更是有意为之、且已按
AGENTS.md「冻结与变更准入」登记决策后，才可以重写基准::

    QUANT_UPDATE_GOLDEN=1 pytest tests/unit/test_caliber_golden.py

并把基准文件的差异与改动一起提交。
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np
import pytest

from quant_etf_api.engine.config import StrategyConfig
from quant_etf_api.engine.context_builder import ContextBuilder
from quant_etf_api.engine.factor_provider import FactorProvider
from quant_etf_api.engine.orchestrator import StrategyEngine
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.infra.db.models.core import BacktestRunModel
from quant_etf_api.services.backtest_service import BacktestService

GOLDEN_PATH = Path(__file__).resolve().parents[1] / "golden" / "caliber_backtest.json"
UPDATE_ENV = "QUANT_UPDATE_GOLDEN"

# 合成行情：4 只指数，60 个预热交易日 + 120 个回测交易日（仅周一至周五）
_CODES = ("000300", "000905", "000852", "399006")
_WARMUP_DAYS = 60
_TEST_DAYS = 120
_SEED = 20261105
# 浮点比较容差：逐日结果本身已按 4 位小数落行，容差只吸收跨平台的末位噪声
_ABS_TOL = 1e-6


class _SetCalendar:
    """以给定交易日集合为准的交易日历桩。"""

    def __init__(self, days: set[date]) -> None:
        """记录交易日集合。

        Args:
            days: 交易日集合。
        """
        self._days = days

    def is_trading_day(self, day: date) -> bool:
        """判断是否为交易日。

        Args:
            day: 待判断日期。

        Returns:
            True 表示交易日。
        """
        return day in self._days


def _trading_days() -> list[date]:
    """生成预热期 + 回测期的连续工作日序列。"""
    days: list[date] = []
    current = date(2024, 1, 2)
    while len(days) < _WARMUP_DAYS + _TEST_DAYS:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _synthetic_bars(days: list[date]) -> dict[tuple[str, date], Any]:
    """生成确定性的合成日线行情。

    每只指数的日收益 = 漂移 + 正弦周期项 + 固定种子噪声；开盘价带隔夜跳空，最高 / 最低价
    包住开收盘。``np.random.RandomState`` 的序列由 NumPy 兼容性策略保证跨版本不变。

    Args:
        days: 全部交易日（含预热期）。

    Returns:
        ``(index_code, trade_date) → 行情对象`` 映射。
    """
    rng = np.random.RandomState(_SEED)
    profiles = {
        "000300": (0.0003, 0.6, 37.0, 0.0, 0.9),
        "000905": (0.0001, 0.9, 23.0, 1.3, 1.2),
        "000852": (-0.0002, 1.2, 29.0, 2.1, 1.5),
        "399006": (0.0004, 1.0, 41.0, 0.7, 1.8),
    }
    bars: dict[tuple[str, date], Any] = {}
    for code in _CODES:
        drift, amp, period, phase, noise = profiles[code]
        close = 1000.0
        for t, day in enumerate(days):
            daily_pct = (
                drift * 100
                + amp * math.sin(2 * math.pi * t / period + phase)
                + noise * rng.standard_normal()
            )
            gap_pct = 0.3 * rng.standard_normal()
            prev_close = close
            open_price = prev_close * (1 + gap_pct / 100)
            close = prev_close * (1 + daily_pct / 100)
            high = max(open_price, close) * (1 + abs(rng.standard_normal()) * 0.004)
            low = min(open_price, close) * (1 - abs(rng.standard_normal()) * 0.004)
            volume = 1e8 * (1 + 0.2 * math.sin(t / 7.0)) * (1 + 0.1 * rng.standard_normal())
            bars[(code, day)] = SimpleNamespace(
                index_code=code,
                trade_date=day,
                open_price=round(open_price, 4),
                high_price=round(high, 4),
                low_price=round(low, 4),
                close_price=round(close, 4),
                prev_close_price=round(prev_close, 4),
                change_pct=round(daily_pct, 4),
                volume=round(volume, 2),
                turnover=round(volume * close, 2),
            )
    # 回测期挖掉两段行情缺口：399006 连续 3 日（当日可执行候选池剔除、因子缺失剔除与
    # 数据缺口告警），000300 连续 2 日（它既是持仓资产也是基准，覆盖收益前向填充——
    # 缺口期间按前值平盘、缺口后补回涨跌）
    for day in days[_WARMUP_DAYS + 50 : _WARMUP_DAYS + 53]:
        del bars[("399006", day)]
    for day in days[_WARMUP_DAYS + 80 : _WARMUP_DAYS + 82]:
        del bars[("000300", day)]
    return bars


def cases() -> dict[str, tuple[StrategyConfig, str]]:
    """三条代表性策略配置及其执行模型（独立对账测试同样复用）。"""
    aliases = {
        "mom20": {"template_id": "return", "params": {"period": 20}},
        "vol20": {"template_id": "volatility", "params": {"period": 20}},
        "rsi14": {"template_id": "rsi", "params": {"period": 14}},
        "dev30": {"template_id": "ma_deviation", "params": {"period": 30}},
    }
    rotation = StrategyConfig.model_validate(
        {
            "strategy_id": "golden_rotation",
            "display_name": "golden_rotation",
            "index_codes": list(_CODES),
            "factor_aliases": aliases,
            "score": {
                "factors": {"mom20": 1.0, "vol20": -0.5},
                "scoring_mode": "rank",
                "missing_factor_strategy": "exclude",
            },
            "filters": {
                "logic": "AND",
                "rules": [{"factor": "rsi14", "op": "lt", "value": 85.0}],
            },
            "rank": {"top_n": 2},
            "portfolio": {"method": "equal_weight", "default_exposure": 1.0},
            "risk": {"max_asset_weight": 0.5},
            "rebalance": {"selection": {"frequency": "weekly", "day_of_week": 4}},
        }
    )
    timing = StrategyConfig.model_validate(
        {
            "strategy_id": "golden_timing",
            "display_name": "golden_timing",
            "index_codes": ["000852"],
            "factor_aliases": aliases,
            "timing": {
                "factors": {"dev30": 1.0, "rsi14": 0.5},
                "transforms": {"dev30": "linear(-8,8)"},
                "proxy_index_codes": ["000852"],
                "state": {"hysteresis": 5.0, "confirm_days": 2},
            },
            "score": {"factors": {"mom20": 1.0}},
            "rank": {"top_n": 1},
            "portfolio": {
                "method": "equal_weight",
                "default_exposure": 1.0,
                "timing_exposure": {"offensive": 1.0, "neutral": 0.5, "defensive": 0.0},
            },
            "risk": {"max_asset_weight": 1.0},
        }
    )
    overlay = StrategyConfig.model_validate(
        {
            "strategy_id": "golden_overlay",
            "display_name": "golden_overlay",
            "index_codes": list(_CODES),
            "factor_aliases": aliases,
            "score": {"factors": {"mom20": 1.0}},
            "rank": {"top_n": 2, "buffer_n": 1},
            "portfolio": {"method": "risk_parity", "default_exposure": 1.0, "cov_window": 40},
            "risk": {
                "max_asset_weight": 0.8,
                "vol_target": {"target": 0.15, "window": 40},
                "trailing_stop": {"threshold": 0.08, "cooldown_days": 10},
            },
            "rebalance": {"band": {"max_drift": 0.05}},
        }
    )
    return {
        "rotation_weekly_close": (rotation, "t_plus_1_close"),
        "timing_weighted_open": (timing, "t_plus_1_open"),
        "risk_parity_overlay_close": (overlay, "t_plus_1_close"),
    }


def _run_case(config: StrategyConfig, execution_model: str) -> dict[str, Any]:
    """在内存里用真实计算链路跑一条回测，返回可序列化的口径快照。

    Args:
        config: 策略配置。
        execution_model: 执行模型。

    Returns:
        包含口径元数据、汇总指标与逐日结果的字典。
    """
    outcome, _bars = run_outcome(config, execution_model)
    return _snapshot(outcome)


def run_outcome(
    config: StrategyConfig, execution_model: str
) -> tuple[dict[str, Any], dict[tuple[str, date], Any]]:
    """在内存里用真实计算链路跑一条回测，返回主循环原始结果与所用的合成行情。

    独立对账测试（``test_backtest_reconcile.py``）复用这里的数据与策略。

    Args:
        config: 策略配置。
        execution_model: 执行模型。

    Returns:
        (``_run_backtest_loop`` 的返回值, 含预热期的原始行情)。
    """
    days = _trading_days()
    test_days = days[_WARMUP_DAYS:]
    bars = _synthetic_bars(days)
    universe = [
        {"index_code": code, "name_cn": code, "category": "broad_index", "is_active": True}
        for code in _CODES
    ]
    svc = BacktestService(db=mock.MagicMock())
    svc._ensure_market_scope_bars = mock.MagicMock()
    svc._get_lookback_days = mock.MagicMock(return_value=90)
    svc._prepare_backtest_data = mock.MagicMock(
        return_value=(universe, list(_CODES), list(test_days), bars, {}, {})
    )
    svc._factor_provider = FactorProvider(db=mock.MagicMock(), registry=build_default_registry())
    svc._context_builder = ContextBuilder(mock.MagicMock(), factor_provider=svc._factor_provider)
    svc._engine = StrategyEngine()
    svc._backtest_repo = mock.MagicMock()
    row = BacktestRunModel(
        backtest_id=f"golden-{config.strategy_id}",
        strategy_id=config.strategy_id,
        start_date=test_days[0],
        end_date=test_days[-1],
        universe_filter={"mode": "subset", "index_codes": list(_CODES)},
        params={
            "_execution_model": execution_model,
            "_data_quality_mode": "warn",
            "_enable_benchmark": True,
            "_benchmark_index_code": "000300",
        },
        status="running",
    )
    with mock.patch(
        "quant_etf_api.services.backtest_service.resolve_trading_calendar",
        return_value=(_SetCalendar(set(days)), "database"),
    ):
        outcome = svc._run_backtest_loop(row.backtest_id, row, config, persist=False)
    return outcome, bars


def _snapshot(outcome: dict[str, Any]) -> dict[str, Any]:
    """把主循环结果规整为稳定、可比对的 JSON 结构。

    Args:
        outcome: ``_run_backtest_loop`` 的返回值。

    Returns:
        口径元数据、汇总指标与逐日结果。
    """
    params = outcome["params"]
    caliber_keys = (
        "_accounting_model",
        "_benchmark_alignment",
        "_calendar_source",
        "_risk_overlay_model",
        "_warmup_trading_days",
        "_execution_model",
    )
    daily = [
        {
            "date": r.trade_date.isoformat(),
            "ret": r.portfolio_return,
            "cum": r.cumulative_return,
            "dd": r.drawdown,
            "bench": r.benchmark_return,
            "exposure": r.total_exposure,
            "turnover": r.turnover,
            "regime": r.timing_regime,
            "held": dict(sorted((r.executed_positions or {}).items())),
        }
        for r in outcome["daily_rows"]
    ]
    return {
        "caliber": {key: params.get(key) for key in caliber_keys},
        "metrics": _normalize(outcome["metrics"]),
        "warnings": sorted(w["code"] for w in outcome["warnings"]),
        "daily": _normalize(daily),
    }


def _normalize(value: Any) -> Any:
    """递归规整：浮点保留 8 位小数，NaN / Inf 转成字符串，字典键排序。

    Args:
        value: 任意 JSON 兼容值。

    Returns:
        规整后的值。
    """
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        return round(value, 8)
    if isinstance(value, np.floating):
        return _normalize(float(value))
    if isinstance(value, np.integer):
        return int(value)
    return str(value)


def _assert_matches(actual: Any, expected: Any, path: str) -> None:
    """逐项比对，失败时报告首个差异的路径。

    Args:
        actual: 本次计算结果。
        expected: 基准值。
        path: 当前比对路径（用于报错定位）。
    """
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: 期望字典，实际 {actual!r}"
        assert sorted(actual) == sorted(expected), (
            f"{path}: 键集合不同，新增 {sorted(set(actual) - set(expected))}，"
            f"缺少 {sorted(set(expected) - set(actual))}"
        )
        for key in expected:
            _assert_matches(actual[key], expected[key], f"{path}.{key}")
        return
    if isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: 期望列表，实际 {actual!r}"
        assert len(actual) == len(expected), f"{path}: 长度 {len(actual)} ≠ {len(expected)}"
        for i, (a, e) in enumerate(zip(actual, expected)):
            _assert_matches(a, e, f"{path}[{i}]")
        return
    if isinstance(expected, float) and not isinstance(actual, bool):
        assert isinstance(actual, (int, float)), f"{path}: 期望数值，实际 {actual!r}"
        assert actual == pytest.approx(expected, abs=_ABS_TOL), (
            f"{path}: {actual!r} ≠ 基准 {expected!r}"
        )
        return
    assert actual == expected, f"{path}: {actual!r} ≠ 基准 {expected!r}"


def _load_golden() -> dict[str, Any]:
    """读取基准文件，不存在时返回空字典。"""
    if not GOLDEN_PATH.exists():
        return {}
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    """模块级基准：更新模式下先全部重算并写回文件。"""
    if os.environ.get(UPDATE_ENV) == "1":
        data = {name: _run_case(cfg, model) for name, (cfg, model) in cases().items()}
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return data
    return _load_golden()


class TestCaliberGolden:
    """回测主链路的口径金样本。"""

    @pytest.mark.parametrize("case", sorted(cases()))
    def test_matches_golden(self, case: str, golden: dict[str, Any]) -> None:
        """当前实现的逐日结果与汇总指标必须与基准一致。"""
        assert case in golden, (
            f"基准文件缺少用例 {case}：确认口径后用 {UPDATE_ENV}=1 生成，并登记决策记录"
        )
        config, model = cases()[case]
        _assert_matches(_run_case(config, model), golden[case], case)

    def test_cases_are_not_degenerate(self, golden: dict[str, Any]) -> None:
        """基准本身要能拦住口径漂移：每条用例都有换仓、非零收益，择时用例出现多种 regime。"""
        for name, data in golden.items():
            daily = data["daily"]
            assert sum(1 for d in daily if d["turnover"]) >= 3, f"{name}: 换仓次数过少"
            assert sum(1 for d in daily if d["ret"]) >= len(daily) // 2, f"{name}: 收益多为 0"
        regimes = {d["regime"] for d in golden["timing_weighted_open"]["daily"]}
        assert len(regimes) >= 2, "择时用例只出现一种 regime，覆盖不到状态层"
