"""研究台账（假设 / 试验 / 试验次数 N）单元测试。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import quant_etf_api.cli as cli
from quant_etf_api.domain.research import ledger as rules
from quant_etf_api.infra.db.models.core import ResearchHypothesisModel, ResearchTrialModel
from quant_etf_api.services.research_ledger_service import ResearchLedgerService


def _make_service(
    hypothesis: ResearchHypothesisModel | None = None, strategy_exists: bool = True
) -> ResearchLedgerService:
    """构建 Mock 仓库的台账服务。"""
    svc = ResearchLedgerService(db=MagicMock())
    svc._repo = MagicMock()
    svc._repo.find_hypothesis.return_value = hypothesis
    svc._config_svc = MagicMock()
    svc._config_svc.get_config.return_value = SimpleNamespace() if strategy_exists else None
    return svc


def _hypothesis(status: str = "open", **overrides: object) -> ResearchHypothesisModel:
    """构建测试用假设行。"""
    fields: dict[str, object] = {
        "hypothesis_id": "h1",
        "strategy_id": "s1",
        "category": "timing",
        "statement": "pv 门控加 2 月动量退出可降低回撤",
        "status": status,
    }
    fields.update(overrides)
    return ResearchHypothesisModel(**fields)


class TestLedgerRules:
    """台账纯规则。"""

    def test_pre_registration_requires_criteria(self) -> None:
        """不写判定标准不能登记假设。"""
        with pytest.raises(rules.LedgerRuleError, match="判定标准"):
            rules.build_pre_registration("  ")
        assert rules.build_pre_registration("净夏普 ≥ 基线", ["t_plus_1_open"]) == {
            "criteria": "净夏普 ≥ 基线",
            "execution_models": ["t_plus_1_open"],
        }

    def test_unknown_category_rejected(self) -> None:
        """类别必须在白名单内。"""
        with pytest.raises(rules.LedgerRuleError, match="类别"):
            rules.validate_category("magic")

    @pytest.mark.parametrize("status", rules.CLOSED_STATUSES)
    def test_closed_hypothesis_rejects_trials(self, status: str) -> None:
        """已关闭的假设不再接收试验（看过结论再追加试验属于事后调参）。"""
        with pytest.raises(rules.LedgerRuleError, match="子假设"):
            rules.ensure_accepts_trials("h1", status)

    @pytest.mark.parametrize(
        ("status", "expected"),
        [("open", "testing"), ("testing", "testing")],
    )
    def test_status_after_trial(self, status: str, expected: str) -> None:
        """第一次挂试验后 open → testing。"""
        assert rules.status_after_trial(status) == expected

    def test_close_requires_reason_and_active_status(self) -> None:
        """关闭必须写原因，且不能重复关闭。"""
        with pytest.raises(rules.LedgerRuleError, match="原因"):
            rules.validate_close("testing", "rejected", "")
        with pytest.raises(rules.LedgerRuleError, match="重复关闭"):
            rules.validate_close("rejected", "accepted", "改主意了")
        with pytest.raises(rules.LedgerRuleError, match="未知的结论"):
            rules.validate_close("testing", "maybe", "x")
        assert rules.validate_close("testing", "rejected", " 4 折只赢 1 折 ") == "4 折只赢 1 折"

    @pytest.mark.parametrize("status", ["rejected", "accepted", "inconclusive", "testing"])
    def test_only_blocked_can_reopen(self, status: str) -> None:
        """只有受 schema 限制的假设可以重开。"""
        with pytest.raises(rules.LedgerRuleError, match="重新打开"):
            rules.validate_reopen(status)
        rules.validate_reopen("blocked_by_schema")

    def test_breakdown_and_total(self) -> None:
        """N = 稳健性 + 优化 + 研究批量 + 人工；estimated 只是其中估算部分的复核值。"""
        breakdown = rules.summarize_trial_breakdown(
            robustness=18,
            optimization=3,
            ledger={
                ("research_batch", False): 40,
                ("research_batch", True): 100,
                ("manual", True): 6,
            },
        )
        assert breakdown == {
            "robustness": 18,
            "optimization": 3,
            "research_batch": 140,
            "manual": 6,
            "estimated": 106,
        }
        assert rules.total_trials(breakdown) == 167
        assert rules.total_trials({}) == 1

    def test_research_batch_summary_excludes_controls(self) -> None:
        """基线与择时归因对照变体不计入试验次数。"""
        variants = [
            {"label": "baseline", "kind": "baseline"},
            {
                "label": "topn4",
                "kind": "patch",
                "aggregate": {"net_metrics": {"net_sharpe_ratio": 0.8}},
                "vs_baseline": {"delta_net_sharpe_ratio": 0.1},
            },
            {"label": "topn4__timing_off", "kind": "timing_off"},
            {"label": "full", "kind": "manual"},
        ]
        count, summary = rules.summarize_research_batch(variants)
        assert count == 2
        assert [row["label"] for row in summary["variants"]] == [
            "baseline",
            "topn4",
            "topn4__timing_off",
            "full",
        ]
        assert summary["variants"][1]["net_sharpe_ratio"] == 0.8
        assert summary["variants"][1]["delta_net_sharpe_ratio"] == 0.1


class TestLedgerService:
    """台账服务编排。"""

    def test_add_hypothesis_persists_pre_registration(self) -> None:
        """登记假设写入事先判定标准，标签去重。"""
        svc = _make_service()
        result = svc.add_hypothesis(
            statement="  1000 上 dev250 ≤ −15% 加满  ",
            category="timing",
            criteria="两种执行口径净夏普均 ≥ 基线",
            strategy_id="s1",
            index_codes=["000852", "000852"],
            tags=["left_side", " ", "dev250"],
            execution_models=["t_plus_1_open", "t_plus_1_close"],
        )
        model = svc._repo.add.call_args.args[0]
        assert isinstance(model, ResearchHypothesisModel)
        assert model.hypothesis_id.startswith("h") and len(model.hypothesis_id) == 13
        assert result["statement"] == "1000 上 dev250 ≤ −15% 加满"
        assert result["status"] == "open"
        assert result["index_codes"] == ["000852"]
        assert result["tags"] == ["left_side", "dev250"]
        assert result["pre_registration"]["execution_models"] == [
            "t_plus_1_open",
            "t_plus_1_close",
        ]
        svc._db.commit.assert_called_once()

    def test_add_hypothesis_rejects_missing_strategy(self) -> None:
        """关联策略不存在时拒绝登记。"""
        svc = _make_service(strategy_exists=False)
        with pytest.raises(ValueError, match="不存在"):
            svc.add_hypothesis(statement="x", category="timing", criteria="y", strategy_id="nope")
        svc._repo.add.assert_not_called()

    def test_record_trial_moves_open_to_testing(self) -> None:
        """挂上第一次试验后假设进入 testing。"""
        hypothesis = _hypothesis("open")
        svc = _make_service(hypothesis)
        result = svc.record_research_batch(
            strategy_id="s1",
            variants=[{"label": "b", "kind": "baseline"}, {"label": "v", "kind": "patch"}],
            windows=[
                {"label": "W1", "start": "2016-01-01", "end": "2017-12-31"},
                {"label": "W2", "start": "2018-01-01", "end": "2025-12-31"},
            ],
            execution_model="t_plus_1_close",
            config_hashes=["abc"],
            hypothesis_id="h1",
        )
        assert hypothesis.status == "testing"
        assert result["kind"] == "research_batch"
        assert result["trial_count"] == 1
        assert result["counted_in_n"] is True
        assert result["start_date"] == "2016-01-01"
        assert result["end_date"] == "2025-12-31"
        assert result["config_hashes"] == ["abc"]

    def test_record_trial_rejects_closed_hypothesis(self) -> None:
        """已关闭的假设不能再挂试验，且不写库。"""
        svc = _make_service(_hypothesis("rejected"))
        with pytest.raises(ValueError, match="已关闭"):
            svc.record_trial(
                kind="research_batch", strategy_id="s1", trial_count=3, hypothesis_id="h1"
            )
        svc._repo.add.assert_not_called()

    def test_link_session_not_counted_in_n(self) -> None:
        """优化会话 / 稳健性批次的关联行不重复计入 N。"""
        svc = _make_service(_hypothesis("testing"))
        result = svc.link_session(
            kind="robustness",
            ref_id="rb1",
            strategy_id="s1",
            hypothesis_id="h1",
            trial_count=18,
        )
        assert result["counted_in_n"] is False
        assert result["ref_id"] == "rb1"
        with pytest.raises(ValueError, match="关联"):
            svc.link_session(
                kind="manual", ref_id="x", strategy_id="s1", hypothesis_id="h1", trial_count=1
            )

    def test_manual_trial_requires_note_and_positive_count(self) -> None:
        """人工 / 补录试验必须写依据且次数为正。"""
        svc = _make_service()
        with pytest.raises(ValueError, match="依据"):
            svc.record_manual_trial(strategy_id="s1", trial_count=3, note=" ")
        with pytest.raises(ValueError, match="为正"):
            svc.record_manual_trial(strategy_id="s1", trial_count=0, note="x")
        result = svc.record_manual_trial(
            strategy_id="s1", trial_count=12, note="left-side 原型", is_estimated=True
        )
        model = svc._repo.add.call_args.args[0]
        assert isinstance(model, ResearchTrialModel)
        assert result["kind"] == "manual"
        assert result["is_estimated"] is True

    def test_close_and_reopen(self) -> None:
        """关闭写入结论；只有 blocked_by_schema 可重开且保留原结论。"""
        hypothesis = _hypothesis("testing")
        svc = _make_service(hypothesis)
        closed = svc.close_hypothesis("h1", "blocked_by_schema", "需要 5 档仓位 schema")
        assert closed["status"] == "blocked_by_schema"
        assert closed["closed_at"] is not None
        reopened = svc.reopen_hypothesis("h1", "已支持多档仓位")
        assert reopened["status"] == "open"
        assert reopened["closed_at"] is None
        assert "需要 5 档仓位 schema" in reopened["conclusion"]
        assert "已支持多档仓位" in reopened["conclusion"]

    def test_trial_breakdown_raises_on_error(self) -> None:
        """台账查询失败时异常上抛，不退回"至少一次试验"（否则 DSR 与预算静默放行）。"""
        svc = _make_service()
        svc._repo.sum_robustness_trials.side_effect = RuntimeError("db down")
        with pytest.raises(RuntimeError, match="db down"):
            svc.trial_breakdown("s1")
        with pytest.raises(RuntimeError, match="db down"):
            svc.ensure_budget("s1", planned=3)

    def test_ledger_overview(self) -> None:
        """总览按状态分组，并给出该策略的 N 拆分与未挂假设的试验数。"""
        svc = _make_service()
        svc._repo.list_hypotheses.return_value = [
            _hypothesis("testing", hypothesis_id="h1"),
            _hypothesis("rejected", hypothesis_id="h2", conclusion="反向"),
        ]
        svc._repo.trial_counts_by_hypothesis.return_value = {"h1": 2}
        svc._repo.sum_robustness_trials.return_value = 18
        svc._repo.count_evaluated_optimizations.return_value = 2
        svc._repo.sum_ledger_trials.return_value = {("research_batch", False): 30}
        svc._repo.list_trials.return_value = [
            SimpleNamespace(hypothesis_id=None),
            SimpleNamespace(hypothesis_id="h1"),
        ]
        result = svc.ledger(strategy_id="s1")
        assert result["status_counts"]["testing"] == 1
        assert result["status_counts"]["rejected"] == 1
        assert [item["hypothesis_id"] for item in result["active"]] == ["h1"]
        assert result["active"][0]["trial_rows"] == 2
        assert [item["hypothesis_id"] for item in result["recent_closed"]] == ["h2"]
        assert result["n_trials"] == 50
        assert result["unlinked_trials"] == 1

    def test_list_rejects_unknown_status(self) -> None:
        """状态过滤值非法时直接报错。"""
        with pytest.raises(ValueError, match="未知的假设状态"):
            _make_service().list_hypotheses(statuses=["done"])


class TestLedgerCli:
    """research hypothesis / trial / ledger 与 --hypothesis-id 接入。"""

    def test_parser_registers_ledger_commands(self) -> None:
        """解析器注册台账子命令，并为产生试验的命令提供 --hypothesis-id。"""
        parser = cli._build_parser()
        args = parser.parse_args(
            [
                "research",
                "hypothesis",
                "add",
                "--statement",
                "x",
                "--category",
                "timing",
                "--criteria",
                "y",
                "--index-codes",
                "000852,000905",
            ]
        )
        assert args.hypothesis_action == "add"
        assert cli._split_list(args.index_codes) == ["000852", "000905"]
        for argv in (
            ["research", "batch", "--strategy", "s1", "--variants", "v.json"],
            ["robustness", "scan", "--strategy", "s1"],
            ["optimization", "start", "--strategy", "s1", "--candidate-file", "c.json"],
        ):
            parsed = parser.parse_args([*argv, "--hypothesis-id", "h1"])
            assert parsed.hypothesis_id == "h1"
        closed = parser.parse_args(
            ["research", "hypothesis", "close", "h1", "--verdict", "rejected", "--reason", "r"]
        )
        assert closed.verdict == "rejected"
        with pytest.raises(SystemExit):
            parser.parse_args(["research", "hypothesis", "close", "h1", "--verdict", "testing"])

    def test_optimization_start_requires_hypothesis_text_or_id(self, monkeypatch) -> None:
        """--hypothesis 与 --hypothesis-id 都不给时拒绝开会话。"""
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())
        monkeypatch.setattr(cli, "OptimizationService", lambda db: MagicMock())
        monkeypatch.setattr(cli, "ResearchLedgerService", lambda db: MagicMock())
        args = cli._build_parser().parse_args(
            ["optimization", "start", "--strategy", "s1", "--candidate-file", "c.json"]
        )
        with pytest.raises(SystemExit):
            cli._run_optimization(args)

    def test_research_batch_records_ledger_trial(self, monkeypatch, capsys) -> None:
        """研究批量评估跑完后自动写台账，输出里带 ledger 块。"""
        fake_batch = MagicMock()
        fake_batch.baseline_config.return_value = {}
        variant = SimpleNamespace(label="v", config={"a": 1}, kind="patch")
        fake_batch_cls = MagicMock(return_value=fake_batch)
        fake_batch_cls.parse_variants.return_value = [variant]
        result = MagicMock()
        result.variants = [{"label": "v", "kind": "patch"}]
        result.windows = [{"start": "2016-01-01", "end": "2025-12-31"}]
        result.to_dict.return_value = {"variants": result.variants}
        fake_batch.run.return_value = result
        fake_ledger = MagicMock()
        fake_ledger.record_research_batch.return_value = {
            "trial_id": "t1",
            "trial_count": 1,
            "hypothesis_id": None,
        }
        monkeypatch.setattr(cli, "SessionLocal", lambda: MagicMock())
        monkeypatch.setattr(cli, "ResearchBatchService", fake_batch_cls)
        monkeypatch.setattr(cli, "ResearchLedgerService", lambda db: fake_ledger)
        monkeypatch.setattr(cli, "_read_json_any", lambda path: [])

        args = cli._build_parser().parse_args(
            ["research", "batch", "--strategy", "s1", "--variants", "v.json"]
        )
        cli._run_research(args)

        out = json.loads(capsys.readouterr().out)
        assert out["ledger"] == {"trial_id": "t1", "trial_count": 1, "hypothesis_id": None}
        kwargs = fake_ledger.record_research_batch.call_args.kwargs
        assert kwargs["strategy_id"] == "s1"
        assert len(kwargs["config_hashes"]) == 1
        fake_ledger.require_active_hypothesis.assert_not_called()

    def test_research_batch_ledger_failure_keeps_result(self, monkeypatch, capsys) -> None:
        """台账写入失败不丢评估结果，但显式提示 N 可能被低估。"""
        ledger = MagicMock()
        ledger.record_research_batch.side_effect = RuntimeError("db down")
        args = SimpleNamespace(
            strategy_id="s1", execution_model="t_plus_1_open", hypothesis_id=None
        )
        info = cli._record_research_batch(
            ledger, args, [], SimpleNamespace(variants=[], windows=[])
        )
        assert "低估" in info["error"]
