"""阶段 2 防过拟合：研究线 N、试验预算、验收第 8–10 项单元测试。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import quant_etf_api.cli as cli
from quant_etf_api.domain.research import ledger as rules
from quant_etf_api.domain.research.acceptance import (
    difference_episodes,
    yearly_sharpe_comparison,
)
from quant_etf_api.infra.db.models.core import StrategyConfigModel
from quant_etf_api.schemas.strategy import StrategyConfigCreate, StrategyConfigUpdate
from quant_etf_api.services.optimization_service import OptimizationService
from quant_etf_api.services.research_ledger_service import ResearchLedgerService
from quant_etf_api.services.strategy_config_service import StrategyConfigService


def _business_days(start: date, n: int) -> list[date]:
    """生成 n 个工作日（用作交易日替身）。"""
    days: list[date] = []
    current = start
    while len(days) < n:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _series(days: list[date], pattern: list[float]) -> dict[date, float]:
    """按循环模式生成 {交易日: 日收益（%）}。"""
    return {day: pattern[i % len(pattern)] for i, day in enumerate(days)}


class TestYearlyConsistency:
    """验收第 9 项：分年度一致性。"""

    def test_needs_three_full_years(self) -> None:
        """完整年份不足 3 个时不通过。"""
        days = _business_days(date(2020, 1, 1), 400)
        base = _series(days, [0.1, -0.05])
        result = yearly_sharpe_comparison(base, base)
        assert result["pass"] is False
        assert "不足" in result["reason"]

    def test_single_year_gain_fails(self) -> None:
        """改善只来自一年：剔除最好年份后不再领先 → 不通过。"""
        days = _business_days(date(2018, 1, 1), 260 * 4)
        base = _series(days, [0.12, -0.08, 0.05])
        cand = dict(base)
        for day in days:
            if day.year == 2019:
                cand[day] = base[day] + 0.2
            elif day.year in (2020, 2021):
                cand[day] = base[day] - 0.03 * (1 if day.toordinal() % 2 else -1)
        result = yearly_sharpe_comparison(base, cand)
        assert result["best_year"] == 2019
        assert result["pass"] is False

    def test_broad_gain_passes(self) -> None:
        """每年都有改善 → 通过。"""
        days = _business_days(date(2018, 1, 1), 260 * 4)
        base = _series(days, [0.12, -0.08, 0.05])
        cand = {day: value + 0.02 for day, value in base.items()}
        result = yearly_sharpe_comparison(base, cand)
        assert result["pass"] is True
        assert result["wins"] == result["total_years"] == 4


class TestDifferenceEpisodes:
    """验收第 10 项：差异事件数。"""

    def test_merges_close_days_and_ignores_noise(self) -> None:
        """间隔 ≤2 个交易日的差异日合并为一段；低于 1bp 的差异视为噪声。"""
        days = _business_days(date(2021, 1, 4), 30)
        base = {day: 0.1 for day in days}
        cand = dict(base)
        for i in (2, 3, 6):  # 3 与 6 相隔 2 个交易日 → 同一段
            cand[days[i]] = 0.5
        cand[days[20]] = 0.5  # 独立第二段
        cand[days[25]] = 0.105  # 0.005% < 1bp，不计
        stats = difference_episodes(base, cand)
        assert stats == {"common_days": 30, "diff_days": 4, "episodes": 2}


class TestLedgerBudgetRules:
    """试验预算纯规则。"""

    def test_raw_sum_and_budget(self) -> None:
        """原始和不含 estimated；已用 + 本次 > 预算才算超出。"""
        breakdown = {"robustness": 10, "optimization": 2, "research_batch": 5, "manual": 3}
        breakdown["estimated"] = 3
        assert rules.raw_trial_sum(breakdown) == 20
        assert rules.exceeds_budget(95, 5, 100) is False
        assert rules.exceeds_budget(95, 6, 100) is True


def _ledger_service(used: int) -> ResearchLedgerService:
    """构造 Mock 的台账服务，研究线已用 ``used`` 次试验。"""
    svc = ResearchLedgerService(db=MagicMock())
    svc._repo = MagicMock()
    svc._repo.sum_robustness_trials.return_value = used
    svc._repo.count_evaluated_optimizations.return_value = 0
    svc._repo.sum_ledger_trials.return_value = {}
    svc._repo.find_hypothesis.return_value = None
    svc._config_svc = MagicMock()
    svc._config_svc.lineage_of.return_value = "line"
    svc._config_svc.lineage_members.return_value = ["line", "line__opt_a"]
    return svc


class TestLedgerBudgetService:
    """研究线 N 与试验预算。"""

    def test_breakdown_sums_lineage_members(self) -> None:
        """缺省按研究线成员统计；scope=strategy 只统计自身。"""
        svc = _ledger_service(7)
        svc.trial_breakdown("line__opt_a")
        assert svc._repo.sum_robustness_trials.call_args.args[0] == ["line", "line__opt_a"]
        svc.trial_breakdown("line__opt_a", scope="strategy")
        assert svc._repo.sum_robustness_trials.call_args.args[0] == ["line__opt_a"]

    def test_within_budget_passes_silently(self) -> None:
        """预算内不留痕。"""
        svc = _ledger_service(10)
        status = svc.ensure_budget("line", 5)
        assert status["used"] == 10
        assert status["remaining"] == 90
        svc._repo.add.assert_not_called()

    def test_over_budget_requires_reason(self) -> None:
        """超预算不给理由时拒绝。"""
        svc = _ledger_service(98)
        with pytest.raises(ValueError, match="--over-budget"):
            svc.ensure_budget("line", 5)

    def test_over_budget_with_reason_records_override(self) -> None:
        """给理由放行，并在台账写一条不计次数的留痕行。"""
        svc = _ledger_service(100)
        status = svc.ensure_budget("line", 1, "最后一轮确认邻域")
        trial = svc._repo.add.call_args.args[0]
        assert trial.kind == "manual"
        assert trial.trial_count == 0
        assert "最后一轮确认邻域" in trial.note
        assert status["override_trial_id"] == trial.trial_id


class _FakeConfigRepo:
    """内存版策略仓库（研究线相关方法）。"""

    def __init__(self, rows: list[StrategyConfigModel]) -> None:
        """记录初始行。"""
        self.rows = {row.strategy_id: row for row in rows}

    def find_by_id(self, strategy_id: str) -> StrategyConfigModel | None:
        """按主键返回。"""
        return self.rows.get(strategy_id)

    def find_lineage_members(self, lineage_id: str) -> list[StrategyConfigModel]:
        """研究线成员。"""
        return [r for r in self.rows.values() if (r.lineage_id or r.strategy_id) == lineage_id]

    def upsert(self, model: StrategyConfigModel) -> None:
        """写入（补齐数据库会填的默认值）。"""
        model.is_starred = bool(model.is_starred)
        model.is_variant = bool(model.is_variant)
        self.rows[model.strategy_id] = model


def _row(strategy_id: str, lineage_id: str | None = None, **extra: object) -> StrategyConfigModel:
    """构造策略行。"""
    return StrategyConfigModel(
        strategy_id=strategy_id,
        display_name=strategy_id,
        version="1.0.0",
        frequency="daily",
        config_json={},
        status="active",
        is_starred=False,
        is_variant=False,
        lineage_id=lineage_id,
        **extra,
    )


def _config_service(rows: list[StrategyConfigModel]) -> StrategyConfigService:
    """注入内存仓库、跳过配置校验的策略配置服务。"""
    svc = StrategyConfigService(MagicMock())
    svc._repo = _FakeConfigRepo(rows)
    svc.validate_config = MagicMock(return_value=SimpleNamespace(valid=True, errors=[]))
    svc._ledger_repo = MagicMock()
    svc._ledger_repo.sum_robustness_trials.return_value = 0
    svc._ledger_repo.count_evaluated_optimizations.return_value = 0
    svc._ledger_repo.sum_ledger_trials.return_value = {}
    return svc


class TestStrategyLineage:
    """研究线归属与验证期继承。"""

    def test_create_joins_root_lineage_and_inherits_consumption(self) -> None:
        """归入任一成员即归入其研究线根；线上已消费验证期则新成员继承留痕。"""
        consumed = _row("cand", lineage_id="root")
        consumed.validation_consumed_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
        svc = _config_service([_row("root", "root"), consumed])
        svc.create_config(
            StrategyConfigCreate(
                strategy_id="manual", display_name="m", config_json={}, lineage_id="cand"
            )
        )
        created = svc._repo.rows["manual"]
        assert created.lineage_id == "root"
        assert created.validation_consumed_at == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert "root" in created.validation_consumed_note

    def test_create_without_lineage_is_own_root(self) -> None:
        """不指定研究线时自成一条。"""
        svc = _config_service([])
        svc.create_config(
            StrategyConfigCreate(strategy_id="solo", display_name="s", config_json={})
        )
        assert svc._repo.rows["solo"].lineage_id == "solo"

    def test_root_with_members_cannot_move(self) -> None:
        """仍有成员的研究线根不能改归。"""
        svc = _config_service([_row("root", "root"), _row("cand", "root"), _row("other", "other")])
        with pytest.raises(ValueError, match="研究线根"):
            svc.update_config("root", StrategyConfigUpdate(lineage_id="other"))

    def test_member_can_move(self) -> None:
        """自身没有试验的普通成员可以改归。"""
        svc = _config_service([_row("root", "root"), _row("cand", "root"), _row("other", "other")])
        svc.update_config("cand", StrategyConfigUpdate(lineage_id="other"))
        assert svc._repo.rows["cand"].lineage_id == "other"

    def test_member_with_trials_cannot_leave(self) -> None:
        """成员自身已有试验时不能移出研究线（否则会把试验从原研究线 N 中拿走）。"""
        svc = _config_service([_row("root", "root"), _row("cand", "root"), _row("other", "other")])
        svc._ledger_repo.sum_ledger_trials.return_value = {("research_batch", False): 4}
        with pytest.raises(ValueError, match="4 次试验"):
            svc.update_config("cand", StrategyConfigUpdate(lineage_id="other"))
        assert svc._repo.rows["cand"].lineage_id == "root"

    def test_standalone_with_trials_can_join(self) -> None:
        """独立策略即使已有试验也可以归入其他研究线（N 只会增加）。"""
        svc = _config_service([_row("solo", "solo"), _row("other", "other")])
        svc._ledger_repo.sum_robustness_trials.return_value = 7
        svc.update_config("solo", StrategyConfigUpdate(lineage_id="other"))
        assert svc._repo.rows["solo"].lineage_id == "other"


class TestAcceptanceStatisticalItems:
    """验收第 8、10 项的服务编排。"""

    def _series(self) -> tuple[dict[date, float], dict[date, float]]:
        """生成两年逐日收益：候选在基线上叠加有波动的小幅超额。"""
        days = _business_days(date(2020, 1, 1), 500)
        base = _series(days, [1.0, -1.0, 0.9, -0.95])
        # 超额序列：均值约 0.03%、日波动约 0.26%，夏普处于"N 小显著、N 大不显著"的区间
        excess = _series(days, [0.3, -0.25, 0.28, -0.22])
        cand = {day: value + excess[day] for day, value in base.items()}
        return base, cand

    def _ledger(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        """替换优化服务内的台账服务。"""
        ledger = MagicMock()
        ledger.lineage_scope.return_value = {"lineage_id": "line", "members": ["line"]}
        monkeypatch.setattr(
            "quant_etf_api.services.optimization_service.ResearchLedgerService",
            lambda db: ledger,
        )
        return ledger

    def test_deflated_sharpe_tests_excess_not_absolute(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """候选与基线完全相同（超额为 0）时不通过，即使候选绝对夏普很高。"""
        ledger = self._ledger(monkeypatch)
        ledger.trial_breakdown.return_value = {"robustness": 1}
        days = _business_days(date(2020, 1, 1), 500)
        strong = _series(days, [0.5, 0.3, 0.4, -0.1])
        item = OptimizationService(db=MagicMock())._check_deflated_sharpe(
            SimpleNamespace(strategy_id="line"), (strong, dict(strong))
        )
        assert item["pass"] is False
        assert item["evidence"]["basis"] == "excess_vs_baseline"
        assert item["evidence"]["deflated_sharpe"] == 0.0
        assert item["evidence"]["candidate_deflated_sharpe"] > 0.95

    def test_deflated_sharpe_fails_when_n_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """N 统计失败时第 8 项判为不通过，而不是按 N=1 放行。"""
        ledger = self._ledger(monkeypatch)
        ledger.trial_breakdown.side_effect = RuntimeError("db down")
        item = OptimizationService(db=MagicMock())._check_deflated_sharpe(
            SimpleNamespace(strategy_id="line"), self._series()
        )
        assert item["pass"] is False
        assert "db down" in item["evidence"]["error"]

    def test_deflated_sharpe_uses_lineage_n(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """N 越大越难通过；证据里给出 N 与研究线。"""
        ledger = self._ledger(monkeypatch)
        svc = OptimizationService(db=MagicMock())
        session = SimpleNamespace(strategy_id="line")
        series = self._series()

        ledger.trial_breakdown.return_value = {"robustness": 1}
        few = svc._check_deflated_sharpe(session, series)
        ledger.trial_breakdown.return_value = {"robustness": 5000}
        many = svc._check_deflated_sharpe(session, series)

        assert few["evidence"]["n_trials"] == 1
        assert many["evidence"]["n_trials"] == 5000
        assert few["evidence"]["deflated_sharpe"] > many["evidence"]["deflated_sharpe"]
        assert few["pass"] is True
        assert many["pass"] is False
        assert many["evidence"]["lineage_id"] == "line"

    def test_missing_full_backtests_fail(self) -> None:
        """缺少全区间回测时第 8–10 项都不通过。"""
        svc = OptimizationService(db=MagicMock())
        session = SimpleNamespace(strategy_id="line")
        assert svc._check_deflated_sharpe(session, None)["pass"] is False
        assert svc._check_yearly_consistency(None)["pass"] is False
        assert svc._check_difference_events(None)["pass"] is False

    def test_difference_events_threshold(self) -> None:
        """逐日都有差异 → 一个长区段，区段数不足 → 不通过。"""
        svc = OptimizationService(db=MagicMock())
        item = svc._check_difference_events(self._series())
        assert item["evidence"]["episodes"] == 1
        assert item["evidence"]["diff_days"] == 500
        assert item["pass"] is False


class TestStage2Cli:
    """CLI 参数注册。"""

    def test_flags_registered(self) -> None:
        """--over-budget / --lineage / --no-strict-reason 均已注册。"""
        parser = cli._build_parser()
        for argv in (
            ["research", "batch", "--strategy", "s1", "--variants", "v.json"],
            ["robustness", "ablate", "--strategy", "s1"],
            ["optimization", "start", "--strategy", "s1", "--candidate-file", "c.json"],
        ):
            assert parser.parse_args([*argv, "--over-budget", "r"]).over_budget == "r"
        created = parser.parse_args(
            ["strategy", "create", "--id", "x", "--name", "n", "--file", "f", "--lineage", "root"]
        )
        assert created.lineage == "root"
        finished = parser.parse_args(
            [
                "optimization",
                "finish",
                "o1",
                "--verdict",
                "accept",
                "--no-strict",
                "--no-strict-reason",
                "r",
            ]
        )
        assert finished.strict is False
        assert finished.no_strict_reason == "r"


class TestResearchDefaultExecutionModel:
    """研究与优化命令缺省按 T+1 收盘口径。"""

    @pytest.mark.parametrize(
        "argv",
        [
            ["backtest", "run", "--strategy", "s1"],
            ["research", "batch", "--strategy", "s1", "--variants", "v.json"],
            ["robustness", "scan", "--strategy", "s1"],
            ["robustness", "ablate", "--strategy", "s1"],
            ["robustness", "pool", "--strategy", "s1"],
            ["optimization", "start", "--strategy", "s1", "--candidate-file", "c.json"],
        ],
    )
    def test_execution_model_defaults_to_close(self, argv: list[str]) -> None:
        """不传 --execution-model 时取 t_plus_1_close。"""
        assert cli._build_parser().parse_args(argv).execution_model == "t_plus_1_close"

    def test_bucket_basis_defaults_to_close(self) -> None:
        """research buckets 的收益口径与研究缺省执行模型一致。"""
        args = cli._build_parser().parse_args(
            ["research", "buckets", "--strategy", "s1", "--ref", "x", "--quantiles", "5"]
        )
        assert args.basis == "t_plus_1_close"

    def test_missing_attribute_falls_back_to_close(self) -> None:
        """手工构造的参数缺少 execution_model 时同样取收盘口径。"""
        assert cli._execution_model(SimpleNamespace()) == "t_plus_1_close"
