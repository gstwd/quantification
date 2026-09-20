"""因子评估模块与估值因子单元测试。

测试覆盖：
- PE/PB 百分位因子计算器（纯计算逻辑，mock FactorContext）
- Rank IC 计算（需要 mock DB session）
- 因子相关性矩阵（需要 mock DB session）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

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
    analyze_backtest_score_ic,
    calc_rank_ic_from_values,
    pairwise_rank_correlation,
    summarize_ic,
)
import quant_etf_api.factors.evaluation as evaluation_module


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


class TestAnalyzeBacktestScoreIc:
    """组合分数 IC 只使用真正参与评分的资产，前瞻收益取全横截面日线。"""

    _D1 = date(2024, 1, 2)
    _D2 = date(2024, 1, 3)
    _DAY = date(2024, 1, 1)

    def _install_repositories(
        self,
        monkeypatch: pytest.MonkeyPatch,
        count: int,
        constant: bool = False,
        unscored: int = 0,
        include_bars: bool = True,
        non_finite: int = 0,
        selection_rebalanced: bool = True,
        recorded: list[dict[str, object]] | None = None,
    ) -> None:
        """装配假仓库：``unscored`` 个 scored=False 的占位 0 分资产。"""
        d1, d2 = self._D1, self._D2
        codes = [f"I{index:02d}" for index in range(count)]
        score_rows = [
            SimpleNamespace(
                trade_date=d1,
                index_code=code,
                signal_score=1.0 if constant else float(index + 1),
                scored=True,
                selection_rebalanced=selection_rebalanced,
            )
            for index, code in enumerate(codes)
        ]
        # 未评分资产：得分为占位 0.0，但前瞻收益刻意做得最高——
        # 一旦被算进横截面，秩相关会被系统性拉低
        filler_codes = [f"U{index:02d}" for index in range(unscored)]
        score_rows.extend(
            SimpleNamespace(
                trade_date=d1,
                index_code=code,
                signal_score=0.0,
                scored=False,
                selection_rebalanced=selection_rebalanced,
            )
            for code in filler_codes
        )
        score_rows.extend(
            SimpleNamespace(
                trade_date=d1,
                index_code=f"N{index:02d}",
                signal_score=float("nan"),
                scored=True,
                selection_rebalanced=selection_rebalanced,
            )
            for index in range(non_finite)
        )

        all_codes = codes + filler_codes
        bars: dict[tuple[str, date], SimpleNamespace] = {}
        if include_bars:
            bars.update({(code, d1): SimpleNamespace(close_price=100.0) for code in all_codes})
            bars.update(
                {
                    (code, d2): SimpleNamespace(close_price=100.0 + index + 1)
                    for index, code in enumerate(codes)
                }
            )
            # 未评分资产次日大涨：若被纳入横截面会把 IC 压成负值
            bars.update(
                {
                    (code, d2): SimpleNamespace(close_price=300.0 + index)
                    for index, code in enumerate(filler_codes)
                }
            )

        captured = recorded if recorded is not None else []

        class FakeBacktestRepository:
            def __init__(self, _db: object) -> None:
                pass

            def find_index_results(
                self, _backtest_id: str, **kwargs: object
            ) -> list[SimpleNamespace]:
                captured.append(dict(kwargs))
                return score_rows

        class FakeBarRepository:
            def __init__(self, _db: object) -> None:
                pass

            def find_all_trading_dates(self, _start: date, _end: date) -> list[date]:
                return [d1, d2] if include_bars else []

            def find_all_date_range(
                self, _start: date, _end: date, _codes: list[str]
            ) -> dict[tuple[str, date], SimpleNamespace]:
                return bars

        monkeypatch.setattr(evaluation_module, "BacktestRepository", FakeBacktestRepository)
        monkeypatch.setattr(evaluation_module, "IndexDailyBarRepository", FakeBarRepository)

    def test_uses_complete_score_cross_section(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install_repositories(monkeypatch, count=20)
        result = analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D1)
        assert result["summary"]["count"] == 1
        assert result["summary"]["cross_section_n_avg"] == 20
        assert result["series"][0]["ic"] == pytest.approx(1.0)

    def test_unscored_rows_do_not_enter_cross_section(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """占位 0 分（scored=False）必须排除，否则固定底部名次会扭曲 IC 与 t 值。"""
        self._install_repositories(monkeypatch, count=20, unscored=20)
        result = analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D1)
        summary = result["summary"]
        assert summary["count"] == 1
        assert summary["cross_section_n_avg"] == 20
        assert result["series"][0]["ic"] == pytest.approx(1.0)
        # 覆盖度诊断如实反映 20/40 的评分覆盖，而不是把 40 当证据基数
        assert summary["scored_n_avg"] == 20.0
        assert summary["universe_n_avg"] == 40.0
        assert summary["coverage_ratio"] == pytest.approx(0.5)
        assert summary["unscored_rows"] == 20

    def test_non_finite_score_is_not_evidence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """得分非有限值的行按未评分处理，不进入横截面。"""
        self._install_repositories(monkeypatch, count=6, non_finite=3)
        result = analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D1)
        assert result["summary"]["cross_section_n_avg"] == 6
        assert result["summary"]["unscored_rows"] == 3

    def test_default_min_n_allows_narrow_candidate_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """策略级横截面门槛是 5：窄池策略不该因为指数池小而永久没有 IC。"""
        self._install_repositories(monkeypatch, count=5)
        narrow = analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D1)
        assert narrow["summary"]["count"] == 1
        assert narrow["summary"]["cross_section_n_required"] == 5

        self._install_repositories(monkeypatch, count=4)
        too_narrow = analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D1)
        assert too_narrow["summary"]["count"] == 0
        assert too_narrow["summary"]["excluded_low_n_days"] == 1

    def test_single_factor_threshold_still_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """显式传入单因子门槛 20 时，19 个评分资产仍不算有效证据。"""
        self._install_repositories(monkeypatch, count=19)
        low_n = analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D1, min_n=20)
        assert low_n["summary"]["count"] == 0
        assert low_n["summary"]["excluded_low_n_days"] == 1

        self._install_repositories(monkeypatch, count=20, constant=True)
        constant = analyze_backtest_score_ic(
            object(), "monitor-1", self._D1, self._D1, min_n=20
        )
        assert constant["summary"]["count"] == 0
        assert constant["summary"]["excluded_low_n_days"] == 1

    def test_missing_forward_bars_are_counted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """没有任何日线时全部观测记为缺少前瞻行情，而不是静默无观测。"""
        self._install_repositories(monkeypatch, count=20, include_bars=False)
        result = analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D1)
        assert result["series"] == []
        assert result["summary"]["count"] == 0
        assert result["summary"]["dropped_no_forward_days"] == 1
        assert "缺少前瞻行情" in (result["summary"]["insufficient_reason"] or "")

    def test_date_range_is_pushed_down_to_repository(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """监控区间下推到 SQL，避免把整段回测取回内存后再筛。"""
        recorded: list[dict[str, object]] = []
        self._install_repositories(monkeypatch, count=20, recorded=recorded)
        analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D2)
        assert recorded == [{"start_date": self._D1, "end_date": self._D2}]

    def test_non_selection_dates_are_excluded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """每天计算的潜在分数不能替代实际执行选股调仓日的信号。"""
        self._install_repositories(monkeypatch, count=20, selection_rebalanced=False)
        result = analyze_backtest_score_ic(object(), "monitor-1", self._D1, self._D1)
        assert result["series"] == []
        assert result["summary"]["count"] == 0

    def test_mixed_dates_keep_only_selection_days(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """同一区间内选股日与非选股日混合时，只保留选股日的观测。

        选股日构造正 IC、非选股日构造负 IC：一旦把后者算进来，均值会被抹平，
        测试就能区分"按标记过滤"与"照单全收"。
        """
        selection_day = date(2024, 1, 2)
        idle_day = date(2024, 1, 3)
        forward_day = date(2024, 1, 4)
        codes = [f"I{index:02d}" for index in range(20)]
        rows = [
            SimpleNamespace(
                trade_date=day,
                index_code=code,
                signal_score=float(index + 1),
                scored=True,
                selection_rebalanced=is_selection,
            )
            for day, is_selection in ((selection_day, True), (idle_day, False))
            for index, code in enumerate(codes)
        ]
        bars: dict[tuple[str, date], SimpleNamespace] = {}
        for index, code in enumerate(codes):
            # 选股日：价格横截面恒定，前瞻日按得分递增 → 正 IC
            bars[(code, selection_day)] = SimpleNamespace(close_price=100.0)
            # 非选股日：起点按得分递增、终点递减 → 负 IC（若被算入会拉低均值）
            bars[(code, idle_day)] = SimpleNamespace(close_price=100.0 + index)
            bars[(code, forward_day)] = SimpleNamespace(close_price=200.0 - index)

        class FakeBacktestRepository:
            def __init__(self, _db: object) -> None:
                pass

            def find_index_results(self, _backtest_id: str, **_kwargs: object):
                return rows

        class FakeBarRepository:
            def __init__(self, _db: object) -> None:
                pass

            def find_all_trading_dates(self, _start: date, _end: date) -> list[date]:
                return [selection_day, idle_day, forward_day]

            def find_all_date_range(self, _start: date, _end: date, _codes: list[str]):
                return bars

        monkeypatch.setattr(evaluation_module, "BacktestRepository", FakeBacktestRepository)
        monkeypatch.setattr(evaluation_module, "IndexDailyBarRepository", FakeBarRepository)

        result = analyze_backtest_score_ic(
            object(), "monitor-1", selection_day, forward_day
        )
        assert result["summary"]["count"] == 1
        assert result["series"][0]["trade_date"] == str(selection_day)
        assert result["series"][0]["ic"] == pytest.approx(1.0)

    def test_effective_n_equals_count_for_rebalance_days(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """组合序列的观测就是调仓日：前瞻期为 5 天时 effective_n 也不得再打折。

        构造 3 个相隔 5 个交易日的选股调仓日（中间夹非选股日），前瞻期 5 天。
        观测之间已经首尾相接，按重叠窗口再折算会把 3 个观测折成 0 个。
        """
        codes = [f"I{index:02d}" for index in range(20)]
        trading_dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(21)]
        selection_days = {trading_dates[0], trading_dates[5], trading_dates[10]}
        rows = [
            SimpleNamespace(
                trade_date=day,
                index_code=code,
                signal_score=float(index + 1),
                scored=True,
                selection_rebalanced=day in selection_days,
            )
            for day in trading_dates
            for index, code in enumerate(codes)
        ]
        bars: dict[tuple[str, date], SimpleNamespace] = {}
        for day_index, day in enumerate(trading_dates):
            for index, code in enumerate(codes):
                # 价格 = 100 + 日序号 × 资产序号：任意 5 日窗口的收益都随得分递增，
                # 于是每个调仓日的 IC 都是 +1；非选股日本可算出 IC 但必须被排除
                bars[(code, day)] = SimpleNamespace(
                    close_price=100.0 + day_index * index,
                )

        class FakeBacktestRepository:
            def __init__(self, _db: object) -> None:
                pass

            def find_index_results(self, _backtest_id: str, **_kwargs: object):
                return rows

        class FakeBarRepository:
            def __init__(self, _db: object) -> None:
                pass

            def find_all_trading_dates(self, _start: date, _end: date) -> list[date]:
                return trading_dates

            def find_all_date_range(self, _start: date, _end: date, _codes: list[str]):
                return bars

        monkeypatch.setattr(evaluation_module, "BacktestRepository", FakeBacktestRepository)
        monkeypatch.setattr(evaluation_module, "IndexDailyBarRepository", FakeBarRepository)

        result = analyze_backtest_score_ic(
            object(), "monitor-1", trading_dates[0], trading_dates[-1], forward_days=5
        )
        summary = result["summary"]
        assert summary["count"] == 3
        assert summary["effective_n"] == 3
        assert summary["overlap"] is False
        assert [item["ic"] for item in result["series"]] == pytest.approx([1.0, 1.0, 1.0])
        # 对照：同一批观测按重叠窗口折算会被压成 0，这正是需要避免的口径
        assert summarize_ic(result["series"], forward_days=5)["effective_n"] == 0

    def test_overlapping_actual_rebalance_windows_keep_conservative_discount(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """实际调仓日间隔不足前瞻期时，不能假定组合 IC 观测相互独立。"""
        codes = [f"I{index:02d}" for index in range(20)]
        trading_dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(21)]
        # 第 0 与第 4 个交易日之间不足 5 日，两个 5 日前瞻收益窗口重叠。
        selection_days = {trading_dates[0], trading_dates[4], trading_dates[10]}
        rows = [
            SimpleNamespace(
                trade_date=day,
                index_code=code,
                signal_score=float(index + 1),
                scored=True,
                selection_rebalanced=day in selection_days,
            )
            for day in trading_dates
            for index, code in enumerate(codes)
        ]
        bars = {
            (code, day): SimpleNamespace(close_price=100.0 + day_index * index)
            for day_index, day in enumerate(trading_dates)
            for index, code in enumerate(codes)
        }

        class FakeBacktestRepository:
            def __init__(self, _db: object) -> None:
                pass

            def find_index_results(self, _backtest_id: str, **_kwargs: object):
                return rows

        class FakeBarRepository:
            def __init__(self, _db: object) -> None:
                pass

            def find_all_trading_dates(self, _start: date, _end: date) -> list[date]:
                return trading_dates

            def find_all_date_range(self, _start: date, _end: date, _codes: list[str]):
                return bars

        monkeypatch.setattr(evaluation_module, "BacktestRepository", FakeBacktestRepository)
        monkeypatch.setattr(evaluation_module, "IndexDailyBarRepository", FakeBarRepository)

        result = analyze_backtest_score_ic(
            object(), "monitor-1", trading_dates[0], trading_dates[-1], forward_days=5
        )
        summary = result["summary"]
        assert summary["count"] == 3
        assert summary["selection_windows_non_overlapping"] is False
        assert summary["overlap"] is True
        assert summary["effective_n"] == 0

    def test_inverted_range_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """区间反向或前瞻期非法时直接返回空结果，不做无意义查询。"""
        recorded: list[dict[str, object]] = []
        self._install_repositories(monkeypatch, count=20, recorded=recorded)
        result = analyze_backtest_score_ic(object(), "monitor-1", self._D2, self._D1)
        assert result["series"] == []
        assert recorded == []

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
        """构造一个有效观测（``day`` 为相对 2024-01-01 的天偏移，可超过 31）。"""
        return {
            "trade_date": date(2024, 1, 1) + timedelta(days=day - 1),
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

    def test_already_non_overlapping_skips_discount(self) -> None:
        """观测本身已按调仓周期间隔时不得二次折算，否则 t 值被系统性低估。"""
        observations = [self._ok(0.05, 25, day=index + 1) for index in range(34)]
        summary = summarize_ic(
            observations, forward_days=5, min_n=20, already_non_overlapping=True
        )
        assert summary["count"] == 34
        assert summary["effective_n"] == 34
        assert summary["overlap"] is False
        # t = ICIR × √34，而不是 × √(34//5)
        assert summary["t_stat"] == pytest.approx(
            summary["ic_ir"] * (34**0.5), abs=1e-4
        )

    def test_already_non_overlapping_keeps_horizon_reporting(self) -> None:
        """折算是取消的，但前瞻期本身仍要如实回显给调用方。"""
        observations = [self._ok(0.05, 25, day=index + 1) for index in range(11)]
        disjoint = summarize_ic(
            observations, forward_days=5, min_n=20, already_non_overlapping=True
        )
        overlapping = summarize_ic(observations, forward_days=5, min_n=20)
        assert disjoint["count"] == overlapping["count"] == 11
        assert disjoint["effective_n"] == 11
        assert overlapping["effective_n"] == 2

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


# ─── FactorTemplateRegistry 估值模板登记 ────────────────────────────────────────


class TestValuationRegistry:
    def test_default_registry_has_all_templates(self) -> None:
        """默认注册表应包含足量内置模板（含估值与技术指标模板）。"""
        from quant_etf_api.factors.catalog import build_default_registry

        registry = build_default_registry()
        # 只校验下界，避免每次新增模板都要改这个数字
        assert len(registry.all()) >= 40

    def test_valuation_templates_registered(self) -> None:
        """估值模板应已登记。"""
        from quant_etf_api.factors.catalog import build_default_registry

        registry = build_default_registry()
        pe = registry.get("pe_percentile")
        pb = registry.get("pb_percentile")
        assert pe is not None
        assert pb is not None
        assert pe.category == "valuation"
        assert pb.category == "valuation"
