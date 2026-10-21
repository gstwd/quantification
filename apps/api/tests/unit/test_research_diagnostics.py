"""研究诊断：分桶收益纯函数、研究期守卫与 research 诊断 CLI 的单元测试。"""

from __future__ import annotations

import json
import math
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

import quant_etf_api.cli as cli
from quant_etf_api.domain.research import diagnostics as diag
from quant_etf_api.domain.research.periods import PeriodBoundaries
from quant_etf_api.services import research_diagnostics_service as svc_module
from quant_etf_api.services.research_diagnostics_service import ResearchDiagnosticsService

BOUNDS = PeriodBoundaries(
    research_start=date(2016, 1, 1),
    research_end=date(2025, 12, 31),
    validation_start=date(2026, 1, 1),
)


def _service(registry: Any | None = None) -> ResearchDiagnosticsService:
    """构造不连库的诊断服务（固定研究期边界）。"""
    return ResearchDiagnosticsService(
        MagicMock(), boundaries=BOUNDS, registry=registry or MagicMock()
    )


# ---------------------------------------------------------------------- 纯函数


class TestForwardReturns:
    """前瞻收益口径。"""

    @pytest.fixture()
    def prices(self) -> tuple[pd.Series, pd.Series]:
        """开盘价 10,11,12,13,14；收盘价 20,22,24,26,28。"""
        idx = pd.date_range("2020-01-01", periods=5, freq="D")
        return (
            pd.Series([10.0, 11, 12, 13, 14], index=idx),
            pd.Series([20.0, 22, 24, 26, 28], index=idx),
        )

    @pytest.mark.parametrize(
        ("basis", "expected_first"),
        [
            ("t_plus_1_open", (13 / 11 - 1) * 100),
            ("t_plus_1_close", (26 / 22 - 1) * 100),
            ("close_to_close", (24 / 20 - 1) * 100),
        ],
    )
    def test_basis(
        self, prices: tuple[pd.Series, pd.Series], basis: str, expected_first: float
    ) -> None:
        """三种口径的入场 / 出场价位移正确，末端前瞻不足为 NaN。"""
        open_, close = prices
        ret = diag.forward_returns(open_, close, 2, basis)
        assert ret.iloc[0] == pytest.approx(expected_first)
        tail_nan = 3 if basis != "close_to_close" else 2
        assert ret.iloc[-tail_nan:].isna().all()

    def test_non_positive_price_is_nan(self, prices: tuple[pd.Series, pd.Series]) -> None:
        """缺价（≤0）不参与收益计算。"""
        open_, close = prices
        open_ = open_.copy()
        open_.iloc[1] = 0.0
        ret = diag.forward_returns(open_, close, 1, "t_plus_1_open")
        assert math.isnan(ret.iloc[0])

    @pytest.mark.parametrize(("horizon", "basis"), [(0, "t_plus_1_open"), (1, "vwap")])
    def test_invalid_args(
        self, prices: tuple[pd.Series, pd.Series], horizon: int, basis: str
    ) -> None:
        """持有期 < 1 或未知口径直接报错。"""
        with pytest.raises(ValueError):
            diag.forward_returns(*prices, horizon, basis)


class TestBucketEdges:
    """分桶边界。"""

    def test_cut_points_add_infinite_bounds(self) -> None:
        """给定切点去重排序并补 ±inf。"""
        edges = diag.bucket_edges(pd.Series([1.0]), cut_points=[1.0, 0.5, 1.0])
        assert edges == [-math.inf, 0.5, 1.0, math.inf]
        assert diag.bucket_labels(edges) == ["(-inf, 0.5]", "(0.5, 1]", "(1, inf)"]

    def test_quantiles_merge_duplicates(self) -> None:
        """分位切点重合时合并，桶数少于请求值。"""
        values = pd.Series([1.0] * 90 + [2.0] * 10)
        edges = diag.bucket_edges(values, quantiles=4)
        assert edges == [-math.inf, 1.0, math.inf]

    @pytest.mark.parametrize(
        "kwargs",
        [{}, {"cut_points": [1.0], "quantiles": 3}, {"quantiles": 1}, {"cut_points": []}],
    )
    def test_invalid(self, kwargs: dict[str, Any]) -> None:
        """两种方式必须二选一，桶数 ≥ 2，切点非空。"""
        with pytest.raises(ValueError):
            diag.bucket_edges(pd.Series(np.arange(10.0)), **kwargs)


class TestBucketTable:
    """分桶统计表。"""

    def test_stats_and_overlap_adjusted_t(self) -> None:
        """桶均值 / 胜率 / 超额 / t 值（按 n // N 折算）与人工计算一致。"""
        idx = pd.date_range("2020-01-01", periods=8, freq="D")
        factor = pd.Series([-1.0, -1, -1, -1, 1, 1, 1, 1], index=idx)
        returns = pd.Series([-1.0, -2, 1, -2, 1, 2, 3, 2], index=idx)
        edges = diag.bucket_edges(factor, cut_points=[0.0])
        table = diag.bucket_table(diag.assign_buckets(factor, edges), returns, edges, horizon=2)

        low, high = table["buckets"]
        assert table["n"] == 8
        assert table["mean_pct"] == pytest.approx(0.5)
        assert low["n"] == 4 and low["mean_pct"] == pytest.approx(-1.0)
        assert low["hit_rate"] == pytest.approx(0.25)
        assert low["excess_pct"] == pytest.approx(-1.5)
        assert high["mean_pct"] == pytest.approx(2.0) and high["hit_rate"] == 1.0
        std = float(pd.Series([1.0, 2, 3, 2]).std())
        assert high["effective_n"] == 2
        assert high["t_stat"] == pytest.approx(round(2.0 / std * math.sqrt(2), 3))
        assert high["t_stat_raw"] == pytest.approx(round(2.0 / std * 2, 3))
        assert high["low_sample"] is True
        assert table["spread_pct"] == pytest.approx(3.0)
        assert table["monotonicity"] is None  # 只有 2 个非空桶

    def test_empty_bucket(self) -> None:
        """无样本的桶统计量为 None，不影响价差计算。"""
        idx = pd.date_range("2020-01-01", periods=4, freq="D")
        factor = pd.Series([0.1, 0.2, 2.0, 3.0], index=idx)
        returns = pd.Series([1.0, 1.0, 2.0, 2.0], index=idx)
        edges = diag.bucket_edges(factor, cut_points=[0.5, 1.0])
        table = diag.bucket_table(diag.assign_buckets(factor, edges), returns, edges, horizon=1)
        assert table["buckets"][1]["n"] == 0
        assert table["buckets"][1]["mean_pct"] is None
        assert table["spread_pct"] == pytest.approx(1.0)


class TestBucketForwardReturns:
    """分桶主入口与分段一致性。"""

    @staticmethod
    def _monotone_sample(n: int = 400) -> tuple[pd.Series, dict[int, pd.Series]]:
        """构造收益随因子单调递增的样本。"""
        idx = pd.bdate_range("2018-01-01", periods=n)
        rng = np.random.default_rng(0)
        factor = pd.Series(rng.uniform(0, 1, n), index=idx)
        ret = factor * 2 + rng.normal(0, 0.1, n)
        return factor, {1: ret, 5: ret * 2}

    def test_split_halves_consistent(self) -> None:
        """单调样本：两段桶均值秩相关为 1、超额同号、价差同号。"""
        factor, returns = self._monotone_sample()
        result = diag.bucket_forward_returns(factor, returns, quantiles=4, split_halves_flag=True)
        assert result["bucketing"]["mode"] == "quantiles"
        assert len(result["bucketing"]["labels"]) == 4
        assert result["split_at"] == factor.index[200].date().isoformat()
        assert [t["horizon"] for t in result["tables"]] == [1, 5]
        table = result["tables"][0]
        assert table["full"]["monotonicity"] == pytest.approx(1.0)
        assert [s["label"] for s in table["segments"]] == ["first", "second"]
        consistency = table["consistency"]
        assert consistency["paired_buckets"] == 4
        assert consistency["rank_corr"] == pytest.approx(1.0)
        assert consistency["excess_sign_agreement"] == pytest.approx(1.0)
        assert consistency["spread_same_sign"] is True

    def test_explicit_split_date_and_no_split(self) -> None:
        """显式分段日生效；不分段时不输出 segments。"""
        factor, returns = self._monotone_sample()
        split = pd.Timestamp("2018-06-01")
        result = diag.bucket_forward_returns(factor, returns, cut_points=[0.5], split_at=split)
        assert result["split_at"] == "2018-06-01"
        assert result["tables"][0]["segments"][1]["start"] >= "2018-06-01"
        plain = diag.bucket_forward_returns(factor, returns, cut_points=[0.5])
        assert plain["split_at"] is None
        assert "segments" not in plain["tables"][0]
        assert plain["distribution"]["n"] == len(factor)


def test_yearly_ic_groups_by_year() -> None:
    """IC 序列按年聚合均值、正值占比与样本数。"""
    series = [
        {"trade_date": "2020-01-02", "ic": 0.2},
        {"trade_date": "2020-06-01", "ic": -0.1},
        {"trade_date": "2021-01-04", "ic": 0.3},
    ]
    assert diag.yearly_ic(series) == [
        {"year": 2020, "ic_mean": 0.05, "positive_ratio": 0.5, "count": 2},
        {"year": 2021, "ic_mean": 0.3, "positive_ratio": 1.0, "count": 1},
    ]


# ---------------------------------------------------------------------- 服务层守卫


class TestResearchWindow:
    """研究期边界：与 purpose=research 回测同一规则。"""

    def test_defaults_to_full_research_period(self) -> None:
        """缺省区间为研究期全段。"""
        assert _service().research_window(None, None) == (
            BOUNDS.research_start,
            BOUNDS.research_end,
        )

    @pytest.mark.parametrize(
        ("start", "end", "keyword"),
        [
            (date(2020, 1, 1), date(2026, 1, 5), "研究期末端"),
            (date(2015, 6, 1), date(2020, 1, 1), "研究期起点"),
            (date(2021, 1, 1), date(2020, 1, 1), "不能晚于"),
        ],
    )
    def test_rejects_out_of_bounds(self, start: date, end: date, keyword: str) -> None:
        """越过研究期末端 / 早于研究期起点 / 区间倒置都直接拒绝。"""
        with pytest.raises(ValueError, match=keyword):
            _service().research_window(start, end)

    def test_signal_lab_rejected_before_compute(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """越界请求在调用研究台计算之前就被拒绝。"""
        called = MagicMock()
        monkeypatch.setattr(svc_module, "SignalLabService", called)
        with pytest.raises(ValueError, match="研究期末端"):
            _service().signal_lab_timing("000300", "llt", None, date(2026, 2, 1), {})
        called.assert_not_called()


class TestFactorIcClipping:
    """因子 IC 观测截止日收缩，前瞻窗口不越过研究期末端。"""

    def test_observation_end_clipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """观测截止日 = 研究期末端往前第 forward_days 个交易日。"""
        calendar = list(pd.bdate_range("2025-12-01", "2025-12-31").date)
        repo = MagicMock()
        repo.find_all_trading_dates.return_value = calendar
        monkeypatch.setattr(svc_module, "IndexDailyBarRepository", lambda db: repo)
        captured: dict[str, Any] = {}

        def _fake_analyze(db, instance, start, end, forward_days, min_n, registry):  # noqa: ANN001, ANN202
            captured.update(start=start, end=end, forward_days=forward_days, min_n=min_n)
            return {
                "series": [{"trade_date": "2025-12-01", "ic": 0.1, "cross_section_n": 25}],
                "summary": {},
            }

        monkeypatch.setattr(svc_module, "analyze_ic", _fake_analyze)
        instance = SimpleNamespace(instance_id="f", template_id="f", label="f", params={})
        out = _service().factor_ic(instance, date(2025, 1, 1), None, forward_days=5)

        repo.find_all_trading_dates.assert_called_once_with(date(2025, 1, 1), BOUNDS.research_end)
        assert captured["end"] == calendar[-6]
        assert out["observation_end"] == calendar[-6].isoformat()
        assert out["yearly"][0]["year"] == 2025

    def test_forward_days_bounds(self) -> None:
        """前瞻天数超出 1~20 时拒绝。"""
        with pytest.raises(ValueError, match="前瞻天数"):
            _service().factor_ic(MagicMock(), None, None, forward_days=21)


class TestBucketReturnsService:
    """分桶诊断取数：行情不越过研究期末端。"""

    def test_price_fetch_capped_at_research_end(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """前瞻行情只加载到研究期末端，返回结构含元数据。"""
        days = pd.bdate_range("2025-10-01", "2025-12-31")
        rows = [
            SimpleNamespace(trade_date=d.date(), factor_value_numeric=float(i % 5))
            for i, d in enumerate(days)
        ]
        compute = MagicMock()
        compute.compute_series.return_value = rows
        monkeypatch.setattr(svc_module, "FactorComputeService", lambda db, registry: compute)
        fetched: dict[str, Any] = {}

        def _prices(self, code, start, end):  # noqa: ANN001, ANN202
            fetched.update(code=code, start=start, end=end)
            return pd.DataFrame({"open": np.linspace(10, 11, len(days)), "close": 10.5}, index=days)

        monkeypatch.setattr(ResearchDiagnosticsService, "_price_frame", _prices)
        instance = SimpleNamespace(instance_id="f", template_id="f", label="f", params={})
        out = _service().bucket_returns(
            instance, "000852", date(2025, 10, 1), date(2025, 12, 31), horizons=[5, 1], quantiles=3
        )
        assert fetched == {"code": "000852", "start": date(2025, 10, 1), "end": BOUNDS.research_end}
        assert out["horizons"] == [1, 5]
        assert out["return_index"] == "000852"
        # 末端 1+5 个交易日前瞻不足，样本被剔除
        assert out["tables"][1]["full"]["n"] == len(days) - 6

    @pytest.mark.parametrize(
        ("kwargs", "keyword"),
        [
            ({"horizons": [0]}, "持有期"),
            ({"horizons": [5], "basis": "vwap"}, "收益口径"),
            ({"horizons": [5], "split_at": date(2015, 1, 1)}, "分段日"),
        ],
    )
    def test_invalid_args(self, kwargs: dict[str, Any], keyword: str) -> None:
        """持有期 / 口径 / 分段日非法时拒绝。"""
        with pytest.raises(ValueError, match=keyword):
            _service().bucket_returns(MagicMock(), "000852", None, None, quantiles=3, **kwargs)


class TestResolveFactor:
    """诊断对象解析的参数组合。"""

    @pytest.mark.parametrize(
        ("args", "keyword"),
        [
            ((None, None, None, None), "必须给出"),
            ((None, None, "s1", None), "同时给出"),
            (("ma_deviation", None, "s1", "pv"), "二选一"),
        ],
    )
    def test_invalid_combinations(self, args: tuple[Any, ...], keyword: str) -> None:
        """模板与策略引用必须二选一，策略方式需 --strategy 与 --ref 同时给出。"""
        with pytest.raises(ValueError, match=keyword):
            _service().resolve_factor(*args)

    def test_strategy_timing_ref_suggests_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--ref 为策略择时因子时推荐其代理指数作为因子计算指数。"""
        timing = MagicMock()
        timing.factor_refs.return_value = ["pv_res"]
        timing.proxies_for.return_value = ["000852"]
        config = SimpleNamespace(timing=timing, factor_aliases={"pv_res": object()})
        repo = MagicMock()
        repo.find_by_id.return_value = object()
        monkeypatch.setattr(svc_module, "StrategyConfigRepository", lambda db: repo)
        cfg_svc = MagicMock()
        cfg_svc.get_parsed_config.return_value = config
        monkeypatch.setattr(svc_module, "StrategyConfigService", lambda db: cfg_svc)
        registry = MagicMock()
        instance, proxy = _service(registry).resolve_factor(None, None, "s1", "pv_res")
        registry.resolve.assert_called_once_with("pv_res", config.factor_aliases)
        assert instance is registry.resolve.return_value
        assert proxy == "000852"


# ---------------------------------------------------------------------- CLI


class TestResearchDiagnosticCli:
    """research signal-lab / factor-ic / buckets 的参数解析与边界拒绝。"""

    def test_parse_buckets(self) -> None:
        """buckets 解析切点、持有期、口径与分段参数。"""
        args = cli._build_parser().parse_args(
            [
                "research",
                "buckets",
                "--strategy",
                "s1",
                "--ref",
                "pv_res",
                "--edges",
                "0,1",
                "--horizons",
                "1,5",
                "--basis",
                "t_plus_1_close",
                "--split-date",
                "2021-01-01",
                "--summary",
            ]
        )
        assert (args.command, args.subcommand) == ("research", "buckets")
        assert args.edges == "0,1" and args.quantiles is None
        assert args.split_date == date(2021, 1, 1)
        assert args.summary is True and args.no_json is False

    @pytest.mark.parametrize(
        "argv",
        [
            ["research", "buckets", "--factor", "x"],  # 缺分桶方式
            ["research", "buckets", "--factor", "x", "--edges", "0", "--quantiles", "3"],
            [
                "research",
                "buckets",
                "--factor",
                "x",
                "--edges",
                "0",
                "--split-halves",
                "--split-date",
                "2021-01-01",
            ],
            ["research", "buckets", "--factor", "x", "--quantiles", "3", "--basis", "vwap"],
            ["research", "signal-lab", "timing", "--signal", "llt"],  # 缺 --index
        ],
    )
    def test_parse_rejects_invalid(self, argv: list[str]) -> None:
        """互斥 / 必填 / 枚举约束在解析阶段拒绝。"""
        with pytest.raises(SystemExit):
            cli._build_parser().parse_args(argv)

    def test_parse_signal_lab_and_factor_ic(self) -> None:
        """signal-lab 嵌套子命令与 factor-ic 默认值。"""
        parser = cli._build_parser()
        args = parser.parse_args(
            ["research", "signal-lab", "cross-section", "--factor", "second_order_momentum"]
        )
        assert (args.subcommand, args.lab_command, args.horizon) == (
            "signal-lab",
            "cross-section",
            5,
        )
        args = parser.parse_args(["research", "factor-ic", "--factor", "ma_deviation"])
        assert args.forward_days == 1 and args.min_n is None and args.start is None

    @pytest.mark.parametrize(
        "argv",
        [
            [
                "research",
                "buckets",
                "--factor",
                "ma_deviation",
                "--index",
                "000852",
                "--quantiles",
                "5",
                "--end",
                "2026-03-01",
            ],
            ["research", "factor-ic", "--factor", "ma_deviation", "--end", "2026-01-02"],
            [
                "research",
                "signal-lab",
                "timing",
                "--index",
                "000852",
                "--signal",
                "llt",
                "--end",
                "2026-01-02",
            ],
        ],
    )
    def test_rejects_validation_period(
        self, argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """区间越过研究期末端时以错误 JSON 退出（退出码 1），不触发任何计算。"""
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())
        monkeypatch.setattr(
            cli, "ResearchDiagnosticsService", lambda db: _service(registry=MagicMock())
        )
        args = cli._build_parser().parse_args(argv)
        with pytest.raises(SystemExit) as exc:
            cli._run_research(args)
        assert exc.value.code == 1
        error = json.loads(capsys.readouterr().err)["error"]
        assert "研究期末端" in error

    def test_buckets_requires_index_without_timing_proxy(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """模板方式且未给 --index 时报错退出。"""
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())
        monkeypatch.setattr(cli, "ResearchDiagnosticsService", lambda db: _service())
        args = cli._build_parser().parse_args(
            ["research", "buckets", "--factor", "ma_deviation", "--quantiles", "5"]
        )
        with pytest.raises(SystemExit):
            cli._run_research(args)
        assert "--index" in json.loads(capsys.readouterr().err)["error"]

    def test_factor_ic_summary_drops_series(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """factor-ic --summary 去掉逐日序列，--params 以 JSON 解析后传给解析器。"""
        fake = MagicMock()
        fake.resolve_factor.return_value = (MagicMock(), None)
        fake.factor_ic.return_value = {"summary": {"ic_mean": 0.02}, "yearly": [], "series": [1]}
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())
        monkeypatch.setattr(cli, "ResearchDiagnosticsService", lambda db: fake)
        args = cli._build_parser().parse_args(
            [
                "research",
                "factor-ic",
                "--factor",
                "ma_deviation",
                "--params",
                '{"period": 60}',
                "--forward-days",
                "5",
                "--summary",
            ]
        )
        cli._run_research(args)
        out = json.loads(capsys.readouterr().out)
        assert "series" not in out and out["summary"]["ic_mean"] == 0.02
        fake.resolve_factor.assert_called_once_with(
            "ma_deviation", {"period": 60}, None, None, extra_aliases={}
        )
        assert fake.factor_ic.call_args.kwargs == {"forward_days": 5}

    def test_signal_lab_timing_summary_drops_series(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """signal-lab timing --summary 去掉逐日序列，定义只保留关键字段。"""
        fake = MagicMock()
        fake.signal_lab_timing.return_value = {
            "signal": {"key": "llt", "label": "LLT", "principle": "长文本", "params": []},
            "dates": ["2020-01-02"],
            "close": [1.0],
            "value": [0.1],
            "on": [1.0],
            "components": [{"key": "llt", "values": [1.0]}],
            "metrics": {"ic_fwd20": 0.1},
        }
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())
        monkeypatch.setattr(cli, "ResearchDiagnosticsService", lambda db: fake)
        args = cli._build_parser().parse_args(
            [
                "research",
                "signal-lab",
                "timing",
                "--index",
                "000300",
                "--signal",
                "llt",
                "--summary",
            ]
        )
        cli._run_research(args)
        out = json.loads(capsys.readouterr().out)
        assert "dates" not in out and "close" not in out
        assert out["components"] == ["llt"]
        assert "principle" not in out["signal"]
        assert out["metrics"]["ic_fwd20"] == 0.1

    def test_bad_params_json(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--params 不是 JSON 对象时报错退出。"""
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())
        monkeypatch.setattr(cli, "ResearchDiagnosticsService", lambda db: MagicMock())
        args = cli._build_parser().parse_args(
            [
                "research",
                "signal-lab",
                "timing",
                "--index",
                "000300",
                "--signal",
                "llt",
                "--params",
                "[1]",
            ]
        )
        with pytest.raises(SystemExit):
            cli._run_research(args)
        assert "JSON 对象" in json.loads(capsys.readouterr().err)["error"]
