"""因子评估模块与估值因子单元测试。

测试覆盖：
- PE/PB 百分位因子计算器（纯计算逻辑，mock FactorContext）
- Rank IC 计算（需要 mock DB session）
- 因子相关性矩阵（需要 mock DB session）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.valuation import (
    PBPercentileComputer,
    PEPercentileComputer,
)
from quant_etf_api.factors.evaluation import (
    STATUS_LOW_N,
    STATUS_NO_FORWARD,
    STATUS_OK,
    build_forward_returns,
    build_ic_observation,
    calc_rank_ic_from_values,
    pairwise_rank_correlation,
    summarize_ic,
)


# ─── 测试辅助：轻量 mock 数据行 ─────────────────────────────────────────────────


@dataclass
class MockValuation:
    """模拟 IndexValuationModel 行，仅保留计算所需字段。"""

    pe: float | None = None
    pe_percentile: float | None = None
    pb: float | None = None
    pb_percentile: float | None = None


# ─── PEPercentileComputer ───────────────────────────────────────────────────────


class TestPEPercentileComputer:
    _computer = PEPercentileComputer()

    def test_spec_factor_id(self) -> None:
        """验证因子 ID。"""
        assert self._computer.spec.factor_id == "pe_percentile"

    def test_spec_category(self) -> None:
        """验证因子类别为 valuation。"""
        assert self._computer.spec.category == "valuation"

    def test_normal_compute(self) -> None:
        """正常场景：有估值数据时返回百分位值。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            index_valuation={
                ("000300", trade_date): MockValuation(pe=12.5, pe_percentile=35.2),
            },
        )
        result = self._computer.compute("000300", trade_date, ctx)
        assert result.numeric == 35.2
        assert result.payload.get("index_code") == "000300"
        assert result.payload.get("pe") == 12.5

    def test_no_valuation_data(self) -> None:
        """无估值数据时返回 None。"""
        ctx = FactorContext()
        result = self._computer.compute("000300", date(2024, 6, 1), ctx)
        assert result.numeric is None
        assert "无估值数据" in result.payload.get("reason", "")

    def test_pe_percentile_none(self) -> None:
        """pe_percentile 为 None 时返回 None。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            index_valuation={
                ("000300", trade_date): MockValuation(pe=12.5, pe_percentile=None),
            },
        )
        result = self._computer.compute("000300", trade_date, ctx)
        assert result.numeric is None


# ─── PBPercentileComputer ───────────────────────────────────────────────────────


class TestPBPercentileComputer:
    _computer = PBPercentileComputer()

    def test_spec_factor_id(self) -> None:
        """验证因子 ID。"""
        assert self._computer.spec.factor_id == "pb_percentile"

    def test_normal_compute(self) -> None:
        """正常场景：有估值数据时返回百分位值。"""
        trade_date = date(2024, 6, 1)
        ctx = FactorContext(
            index_valuation={
                ("000300", trade_date): MockValuation(pb=1.35, pb_percentile=22.8),
            },
        )
        result = self._computer.compute("000300", trade_date, ctx)
        assert result.numeric == 22.8
        assert result.payload.get("pb") == 1.35

    def test_no_data_returns_none(self) -> None:
        """无数据时返回 None。"""
        ctx = FactorContext()
        result = self._computer.compute("000300", date(2024, 6, 1), ctx)
        assert result.numeric is None


# ─── Rank IC 纯函数测试（不依赖 DB） ────────────────────────────────────────────


class TestCalcRankIcFromValues:
    """测试 Rank IC 核心计算与最小样本门禁。"""

    def test_perfect_positive(self) -> None:
        """完全正相关时 IC 应为 1.0。"""
        ic = calc_rank_ic_from_values(
            {"a": 1.0, "b": 2.0, "c": 3.0}, {"a": 1.0, "b": 2.0, "c": 3.0}, min_n=3
        )
        assert ic == pytest.approx(1.0, abs=1e-6)

    def test_perfect_negative(self) -> None:
        """完全负相关时 IC 应为 -1.0。"""
        ic = calc_rank_ic_from_values(
            {"a": 1.0, "b": 2.0, "c": 3.0}, {"a": 3.0, "b": 2.0, "c": 1.0}, min_n=3
        )
        assert ic == pytest.approx(-1.0, abs=1e-6)

    def test_monotone_transform_invariance(self) -> None:
        """单调变换不改变秩相关（收益率量纲不影响 IC）。"""
        base = calc_rank_ic_from_values(
            {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0},
            {"a": 0.01, "b": 0.02, "c": 0.03, "d": 0.04},
            min_n=4,
        )
        scaled = calc_rank_ic_from_values(
            {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0},
            {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0},
            min_n=4,
        )
        assert base == scaled

    def test_below_min_n_returns_none(self) -> None:
        """配对样本少于 min_n 时不产出 IC（n=3 时 Spearman 只能取 ±1/±0.5）。"""
        values = {"a": 1.0, "b": 2.0, "c": 3.0}
        returns = {"a": 1.0, "b": 2.0, "c": 3.0}
        assert calc_rank_ic_from_values(values, returns, min_n=3) is not None
        assert calc_rank_ic_from_values(values, returns, min_n=4) is None

    def test_unpaired_indexes_are_ignored(self) -> None:
        """缺少前瞻收益的指数不参与配对。"""
        ic = calc_rank_ic_from_values(
            {"a": 1.0, "b": 2.0, "c": 3.0}, {"a": 1.0, "b": 2.0}, min_n=2
        )
        assert ic == pytest.approx(1.0, abs=1e-6)

    def test_constant_input_returns_none(self) -> None:
        """横截面因子值恒定时相关未定义，应返回 None 而不是 0。"""
        assert calc_rank_ic_from_values(
            {"a": 5.0, "b": 5.0, "c": 5.0}, {"a": 1.0, "b": 2.0, "c": 3.0}, min_n=3
        ) is None


class TestBuildForwardReturns:
    """测试前瞻收益的交易日历对齐（同一横截面内前瞻窗口必须等长）。"""

    _D1 = date(2024, 1, 1)
    _D2 = date(2024, 1, 2)
    _D3 = date(2024, 1, 3)
    _D4 = date(2024, 1, 4)

    def _lookup(self) -> dict[tuple[str, date], float]:
        """构造 A 全量、B 缺 D2 的行情。"""
        return {
            ("A", self._D1): 100.0,
            ("A", self._D2): 110.0,
            ("A", self._D3): 121.0,
            ("A", self._D4): 133.1,
            ("B", self._D1): 50.0,
            ("B", self._D3): 55.0,
            ("B", self._D4): 60.5,
        }

    def test_next_trading_day(self) -> None:
        """forward_days=1 时前瞻日是日历上的下一个交易日。"""
        dates = [self._D1, self._D2, self._D3, self._D4]
        result = build_forward_returns(self._lookup(), dates, 1)
        assert result[self._D1]["A"] == pytest.approx(10.0, abs=1e-9)

    def test_missing_forward_bar_excludes_index(self) -> None:
        """前瞻日当天缺行情的指数被剔除，不能顺延到更晚的 bar。"""
        dates = [self._D1, self._D2, self._D3, self._D4]
        result = build_forward_returns(self._lookup(), dates, 1)
        # B 在 D2 没有行情：若顺延到 D3 会得到 10% 的"两日"收益，这里必须没有 B
        assert "B" not in result[self._D1]
        assert set(result[self._D1]) == {"A"}

    def test_calendar_tail_has_no_entry(self) -> None:
        """日历末尾没有足够前瞻日的交易日不出现（区别于"出现但为空"）。"""
        dates = [self._D1, self._D2, self._D3, self._D4]
        result = build_forward_returns(self._lookup(), dates, 1)
        assert self._D4 not in result
        assert set(result) == {self._D1, self._D2, self._D3}

    def test_multi_day_forward(self) -> None:
        """forward_days=2 时用日历上第 2 个交易日，缺 bar 的指数仍被剔除。"""
        dates = [self._D1, self._D2, self._D3, self._D4]
        result = build_forward_returns(self._lookup(), dates, 2)
        assert set(result) == {self._D1, self._D2}
        assert result[self._D1]["A"] == pytest.approx(21.0, abs=1e-9)
        assert result[self._D1]["B"] == pytest.approx(10.0, abs=1e-9)
        assert "B" not in result[self._D2]

    def test_window_present_but_empty(self) -> None:
        """前瞻窗口存在但没有指数合格时保留空字典，便于区分"窗口不存在"。"""
        lookup = {("A", self._D1): 100.0, ("A", self._D3): 121.0}
        dates = [self._D1, self._D2, self._D3]
        result = build_forward_returns(lookup, dates, 1)
        # D1 / D2 都有前瞻窗口，只是没有指数同时具备当日与前瞻日行情
        assert result[self._D1] == {}
        assert result[self._D2] == {}
        # D3 没有前瞻窗口，因此根本不出现在结果里
        assert self._D3 not in result

    def test_non_positive_price_skipped(self) -> None:
        """价格非正时不作为基准价。"""
        lookup = {("A", self._D1): 0.0, ("A", self._D2): 10.0}
        result = build_forward_returns(lookup, [self._D1, self._D2], 1)
        assert result[self._D1] == {}


class TestBuildIcObservation:
    """测试单日 IC 观测的状态分类。"""

    _DAY = date(2024, 1, 1)

    def test_ok(self) -> None:
        """配对样本达标时产出有效 IC 与横截面数量。"""
        obs = build_ic_observation(
            self._DAY,
            {"a": 1.0, "b": 2.0, "c": 3.0},
            {"a": 1.0, "b": 2.0, "c": 3.0},
            min_n=3,
        )
        assert obs["status"] == STATUS_OK
        assert obs["cross_section_n"] == 3
        assert obs["ic"] == pytest.approx(1.0, abs=1e-6)

    def test_low_n(self) -> None:
        """配对样本不足时记为 low_n 且不带 IC。"""
        obs = build_ic_observation(
            self._DAY, {"a": 1.0, "b": 2.0}, {"a": 1.0, "b": 2.0}, min_n=20
        )
        assert obs["status"] == STATUS_LOW_N
        assert obs["cross_section_n"] == 2
        assert obs["ic"] is None

    def test_single_pair_never_ok(self) -> None:
        """只有 1 个配对样本时不可能算出相关系数。"""
        obs = build_ic_observation(
            self._DAY, {"a": 1.0}, {"a": 1.0}, min_n=1
        )
        assert obs["status"] == STATUS_LOW_N


class TestSummarizeIc:
    """测试 IC 汇总统计的门禁、t 统计量与重叠窗口折算。"""

    @staticmethod
    def _ok(ic: float, n: int, day: int = 1) -> dict[str, object]:
        """构造一个有效观测。"""
        return {
            "trade_date": date(2024, 1, day),
            "ic": ic,
            "cross_section_n": n,
            "status": STATUS_OK,
        }

    def test_counts_and_statistics(self) -> None:
        """只有 ok 观测进入统计量，low_n / no_forward 分别计数。"""
        observations = [
            self._ok(0.2, 25),
            self._ok(-0.1, 30, day=2),
            {
                "trade_date": date(2024, 1, 3),
                "ic": None,
                "cross_section_n": 8,
                "status": STATUS_LOW_N,
            },
            {
                "trade_date": date(2024, 1, 4),
                "ic": None,
                "cross_section_n": 0,
                "status": STATUS_NO_FORWARD,
            },
        ]
        summary = summarize_ic(observations, forward_days=1, min_n=20)
        assert summary["count"] == 2
        assert summary["ic_mean"] == pytest.approx(0.05)
        assert summary["ic_std"] == pytest.approx(0.2121)
        assert summary["ic_ir"] == pytest.approx(0.2357)
        assert summary["t_stat"] == pytest.approx(0.3333)
        assert summary["ic_positive_ratio"] == pytest.approx(0.5)
        assert summary["excluded_low_n_days"] == 1
        assert summary["dropped_no_forward_days"] == 1
        assert summary["cross_section_n_avg"] == pytest.approx(27.5)
        assert summary["cross_section_n_min"] == 25
        assert summary["cross_section_n_max"] == 30
        assert summary["cross_section_n_required"] == 20
        assert summary["overlap"] is False
        assert summary["effective_n"] == 2
        assert summary["insufficient_reason"] is None

    def test_overlap_discounts_effective_n(self) -> None:
        """forward_days > 1 时 effective_n 按重叠倍数折算。"""
        observations = [self._ok(0.05, 25, day=index + 1) for index in range(11)]
        summary = summarize_ic(observations, forward_days=5, min_n=20)
        assert summary["count"] == 11
        assert summary["effective_n"] == 2
        assert summary["overlap"] is True

    def test_empty_gives_reason(self) -> None:
        """没有任何候选观测时给出"无因子值"的原因。"""
        summary = summarize_ic([], forward_days=1, min_n=20)
        assert summary["count"] == 0
        assert summary["ic_mean"] is None
        assert summary["ic_ir"] is None
        assert summary["t_stat"] is None
        assert summary["effective_n"] == 0
        assert summary["insufficient_reason"] == "区间内没有该因子的因子值，无法计算 Rank IC"

    def test_all_low_n_reason_reports_observed_size(self) -> None:
        """全部横截面不足时原因里要带上观测到的最大指数数量。"""
        observations = [
            {
                "trade_date": date(2024, 1, day),
                "ic": None,
                "cross_section_n": 8,
                "status": STATUS_LOW_N,
            }
            for day in (1, 2, 3)
        ]
        summary = summarize_ic(observations, forward_days=1, min_n=20)
        assert summary["count"] == 0
        assert summary["excluded_low_n_days"] == 3
        assert "不足 20 个指数" in str(summary["insufficient_reason"])
        assert "最多 8 个" in str(summary["insufficient_reason"])


class TestPairwiseRankCorrelation:
    """测试因子相关性矩阵的成对交集口径。"""

    def test_pairwise_intersection(self) -> None:
        """每一对因子用该对自身的交集，不要求所有因子都有值的全局交集。"""
        values = {
            "f1": {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0},
            "f2": {"A": 1.0, "B": 2.0, "C": 3.0},
            "f3": {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0},
        }
        ids, matrix, pair_counts, undetermined = pairwise_rank_correlation(values, min_n=3)
        assert ids == ["f1", "f2", "f3"]
        assert all(matrix[i][i] == 1.0 for i in range(3))
        # f1 × f3：交集 4 个指数，完全负相关
        assert pair_counts[0][2] == 4
        assert matrix[0][2] == pytest.approx(-1.0, abs=1e-6)
        # f1 × f2：交集 3 个指数
        assert pair_counts[0][1] == 3
        assert matrix[0][1] == pytest.approx(1.0, abs=1e-6)
        assert undetermined == 0

    def test_below_min_n_is_none(self) -> None:
        """成对样本不足的格子为 None 并计入未定对数。"""
        values = {
            "f1": {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0},
            "f2": {"A": 1.0, "B": 2.0, "C": 3.0},
        }
        ids, matrix, pair_counts, undetermined = pairwise_rank_correlation(values, min_n=4)
        assert matrix[0][1] is None
        assert matrix[1][0] is None
        assert pair_counts[0][1] == 3
        assert undetermined == 1

    def test_constant_input_is_none_not_zero(self) -> None:
        """常数输入导致的相关未定义必须记为 None，不能伪造成 0（=不相关）。"""
        values = {
            "f1": {"A": 1.0, "B": 2.0, "C": 3.0},
            "f2": {"A": 5.0, "B": 5.0, "C": 5.0},
        }
        _ids, matrix, _counts, undetermined = pairwise_rank_correlation(values, min_n=3)
        assert matrix[0][1] is None
        assert undetermined == 1


# ─── FactorRegistry 估值因子注册 ────────────────────────────────────────────────


class TestValuationRegistry:
    def test_default_registry_has_all_factors(self) -> None:
        """默认注册表应包含足量内置因子（含估值与技术指标因子）。"""
        from quant_etf_api.factors.registry import build_default_factor_registry

        registry = build_default_factor_registry()
        # 只校验下界，避免每次新增因子都要改这个数字
        assert len(registry.all()) >= 40

    def test_valuation_factors_registered(self) -> None:
        """估值因子应已注册。"""
        from quant_etf_api.factors.registry import build_default_factor_registry

        registry = build_default_factor_registry()
        pe = registry.get("pe_percentile")
        pb = registry.get("pb_percentile")
        assert pe is not None
        assert pb is not None
        assert pe.spec.category == "valuation"
        assert pb.spec.category == "valuation"
