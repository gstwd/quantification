"""滚动窗口估值分位模板（valuation_percentile）的回归测试。

覆盖：
- 窗口不短于全部历史时与入库扩张分位（``_calc_percentile``）逐日一致；
- 窗口截断、min_periods、当日缺失与时点安全（未来数据不影响历史取值）；
- field=pe / pb / mean；
- 模板注册：choice 参数校验、目录载荷、回望窗口与策略配置校验。
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from quant_etf_api.engine.config import FactorAliasConfig, ScoreConfig, StrategyConfig
from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.valuation import (
    ValuationPercentileComputer,
    rolling_percentile_series,
)
from quant_etf_api.factors.catalog import build_default_registry
from quant_etf_api.factors.templates import FactorParameterError
from quant_etf_api.infra.clients.akshare_index import _calc_percentile
from quant_etf_api.services.strategy_config_service import StrategyConfigService

CODE = "000852"
START = date(2014, 10, 17)


def _dates(n: int) -> list[date]:
    """生成 n 个连续工作日。"""
    out: list[date] = []
    day = START
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _context(
    pe: list[float | None], pb: list[float | None] | None = None, dates: list[date] | None = None
) -> FactorContext:
    """按 PE / PB 序列构造只含估值数据的上下文（同时写入入库扩张分位）。"""
    days = dates or _dates(len(pe))
    pb_values = pb if pb is not None else [None] * len(pe)
    pe_pct = _calc_percentile([(d, v) for d, v in zip(days, pe) if v is not None])
    pb_pct = _calc_percentile([(d, v) for d, v in zip(days, pb_values) if v is not None])
    return FactorContext(
        index_valuation={
            (CODE, d): SimpleNamespace(
                pe=pe_v, pb=pb_v, pe_percentile=pe_pct.get(d), pb_percentile=pb_pct.get(d)
            )
            for d, pe_v, pb_v in zip(days, pe, pb_values)
        }
    )


def _computer(**params: Any) -> ValuationPercentileComputer:
    """按模板规范化参数创建计算器。"""
    template = build_default_registry().get("valuation_percentile")
    assert template is not None
    return template.build_computer(template.resolve_params(params))  # type: ignore[return-value]


def _random_series(n: int, seed: int = 7) -> list[float]:
    """带并列值的随机估值序列（保留 1 位小数制造并列）。"""
    rng = random.Random(seed)
    value = 30.0
    out: list[float] = []
    for _ in range(n):
        value = max(5.0, value + rng.gauss(0, 1.5))
        out.append(round(value, 1))
    return out


class TestRollingMatchesExpanding:
    """窗口足够长时与入库扩张分位一致。"""

    def test_series_equals_calc_percentile(self) -> None:
        """纯函数：窗口 ≥ 样本数且 min_periods=1 时逐日等于扩张分位。"""
        days = _dates(400)
        values = _random_series(400)
        expanding = _calc_percentile(list(zip(days, values)))
        rolling = rolling_percentile_series(days, values, window=400, min_periods=1)
        assert {d: pct for d, (pct, _n) in rolling.items()} == expanding

    @pytest.mark.parametrize("field", ["pe", "pb"])
    def test_computer_equals_stored_percentile(self, field: str) -> None:
        """计算器：window 覆盖全部历史时等于 index_valuation 的入库分位。"""
        series = _random_series(600, seed=11)
        ctx = _context(series, [v / 10 for v in series])
        days = _dates(600)
        values = _computer(field=field, window=3000, min_periods=1).compute_batch(CODE, days, ctx)
        stored = f"{field}_percentile"
        for d in days:
            assert values[d].numeric == getattr(ctx.index_valuation[(CODE, d)], stored)

    def test_diverges_once_window_shorter_than_history(self) -> None:
        """窗口短于历史后不再等于扩张分位（早期高估值样本被移出窗口）。"""
        # 先高后低再回升：扩张分位被早期泡沫压住，滚动分位能回到高位
        series = [100.0 - i * 0.5 for i in range(120)] + [40.0 + i * 0.5 for i in range(60)]
        ctx = _context(series)
        last = _dates(len(series))[-1]
        value = _computer(window=60, min_periods=60).compute_batch(CODE, [last], ctx)[last]
        assert value.numeric == 100.0
        assert ctx.index_valuation[(CODE, last)].pe_percentile < 70


class TestWindowSemantics:
    """窗口、样本数与缺失语义。"""

    def test_window_truncation(self) -> None:
        """窗口取截至当日最近 window 个有效观测（含当日）。"""
        days = _dates(5)
        result = rolling_percentile_series(days, [5.0, 1.0, 3.0, 4.0, 2.0], window=3, min_periods=3)
        # 第 3 日窗口 [5,1,3]：3 排第 2 → 1/2；第 4 日 [1,3,4] → 100；第 5 日 [3,4,2] → 0
        assert result == {days[2]: (50.0, 3), days[3]: (100.0, 3), days[4]: (0.0, 3)}

    def test_ties_count_toward_rank(self) -> None:
        """并列值计入自身（与扩张分位同口径）。"""
        days = _dates(3)
        result = rolling_percentile_series(days, [2.0, 2.0, 2.0], window=3, min_periods=1)
        assert [result[d][0] for d in days] == [50.0, 100.0, 100.0]

    def test_min_periods_and_missing_reasons(self) -> None:
        """样本不足与当日无估值分别给出缺失原因。"""
        pe: list[float | None] = [10.0 + i for i in range(5)] + [None]
        ctx = _context(pe)
        days = _dates(6)
        values = _computer(window=60, min_periods=5).compute_batch(CODE, days, ctx)
        assert values[days[3]].numeric is None
        assert "样本不足 5" in values[days[3]].payload["reason"]
        assert values[days[4]].numeric == 100.0
        assert values[days[4]].payload["sample_counts"] == {"pe": 5}
        assert values[days[5]].numeric is None
        assert "无当日有效 PE" in values[days[5]].payload["reason"]

    def test_min_periods_capped_by_window(self) -> None:
        """min_periods 大于窗口时按窗口长度截断，窗口填满即出值。"""
        ctx = _context([float(i) for i in range(80)])
        last = _dates(80)[-1]
        value = _computer(window=60, min_periods=500).compute_batch(CODE, [last], ctx)[last]
        assert value.numeric == 100.0

    def test_point_in_time_safety(self) -> None:
        """追加未来估值行不改变历史日期的取值（只用截至当日的数据）。"""
        base = _random_series(300, seed=3)
        future = base + [1.0] * 50 + [500.0] * 50
        days = _dates(300)
        short = _computer(window=120, min_periods=60).compute_batch(CODE, days, _context(base))
        long = _computer(window=120, min_periods=60).compute_batch(CODE, days, _context(future))
        assert {d: v.numeric for d, v in short.items()} == {d: v.numeric for d, v in long.items()}

    def test_mean_field_averages_pe_and_pb(self) -> None:
        """field=mean 取 PE 与 PB 滚动分位的算术平均；任一缺失即缺失。"""
        pe = [1.0, 2.0, 3.0, 4.0]
        pb: list[float | None] = [4.0, 3.0, 2.0, None]
        ctx = _context(pe, pb)
        days = _dates(4)
        values = _computer(field="mean", window=60, min_periods=3).compute_batch(CODE, days, ctx)
        # 第 3 日：PE 分位 100、PB 分位 0 → 50
        assert values[days[2]].numeric == 50.0
        assert values[days[2]].payload["percentiles"] == {"pe": 100.0, "pb": 0.0}
        assert values[days[3]].numeric is None
        assert "PB" in values[days[3]].payload["reason"]

    def test_compute_single_day_matches_batch(self) -> None:
        """单日接口委托批量实现。"""
        ctx = _context(_random_series(100))
        day = _dates(100)[-1]
        computer = _computer(window=60, min_periods=30)
        assert (
            computer.compute(CODE, day, ctx).numeric
            == computer.compute_batch(CODE, [day], ctx)[day].numeric
        )


class TestTemplateRegistration:
    """模板注册、choice 参数与目录载荷。"""

    def test_template_metadata(self) -> None:
        """默认参数、回望窗口、适用位置与 0-100 量纲。"""
        template = build_default_registry().get("valuation_percentile")
        assert template is not None
        assert dict(template.default_params) == {"field": "pe", "window": 1250, "min_periods": 250}
        assert template.native_0_100 is True
        assert template.required_data == ("index_valuation",)
        assert "timing" in template.usage and "filter" in template.usage
        assert template.lookback_days({"field": "pb", "window": 3000, "min_periods": 1}) >= 4800
        assert template.label({"field": "mean", "window": 500, "min_periods": 250}) == (
            "PE/PB均值滚动分位（500日）"
        )

    @pytest.mark.parametrize(
        "params",
        [{"field": "ps"}, {"field": 1}, {"window": 59}, {"window": 3001}, {"min_periods": 0}],
    )
    def test_invalid_params_rejected(self, params: dict[str, Any]) -> None:
        """choice 取值不在列表、窗口越界时拒绝。"""
        template = build_default_registry().get("valuation_percentile")
        assert template is not None
        with pytest.raises(FactorParameterError):
            template.resolve_params(params)

    def test_payload_choices_only_for_choice_params(self) -> None:
        """choice 参数载荷带 choices；存量数值参数载荷不变（不出现 choices 键）。"""
        registry = build_default_registry()
        template = registry.get("valuation_percentile")
        assert template is not None
        payload = {k: v.to_payload() for k, v in template.parameter_schema.items()}
        assert payload["field"]["type"] == "choice"
        assert payload["field"]["choices"] == ["pe", "pb", "mean"]
        assert "choices" not in payload["window"]
        for other in registry.all():
            if other.template_id == "valuation_percentile":
                continue
            for parameter in other.parameter_schema.values():
                # 其他模板的 choice 参数同样带 choices；数值参数载荷不出现 choices 键
                if parameter.type != "choice":
                    assert "choices" not in parameter.to_payload()

    def test_distinct_params_are_distinct_instances(self) -> None:
        """同模板不同 field / window 是不同实例（不会被同参复用合并）。"""
        registry = build_default_registry()
        aliases = {
            "pe5y": FactorAliasConfig(template_id="valuation_percentile", params={}),
            "pb5y": FactorAliasConfig(template_id="valuation_percentile", params={"field": "pb"}),
            "pe10y": FactorAliasConfig(template_id="valuation_percentile", params={"window": 2500}),
        }
        keys = {registry.resolve(ref, aliases).dedup_key for ref in aliases}
        assert len(keys) == 3


class _FakeRow:
    """极简 factor_definition 行替身，仅暴露 factor_id。"""

    def __init__(self, factor_id: str) -> None:
        self.factor_id = factor_id


def _validate_alias(params: dict[str, Any]) -> list[str]:
    """校验一个引用 valuation_percentile 别名的策略配置，返回错误。"""
    db = mock.MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [
        _FakeRow(fid) for fid in build_default_registry().ids()
    ]
    config = StrategyConfig(
        strategy_id="v",
        display_name="v",
        factor_aliases={
            "val5y": FactorAliasConfig(template_id="valuation_percentile", params=params)
        },
        score=ScoreConfig(factors={"val5y": -1.0}),
    )
    return StrategyConfigService(db=db).validate_config(config.model_dump()).errors


class TestStrategyValidation:
    """策略配置引用滚动估值分位。"""

    def test_valid_alias(self) -> None:
        """合法参数无错误。"""
        assert _validate_alias({"field": "pb", "window": 1250, "min_periods": 250}) == []

    def test_invalid_choice_rejected(self) -> None:
        """field 取值非法时配置校验报错。"""
        errors = _validate_alias({"field": "ps"})
        assert any("field" in e for e in errors), errors
