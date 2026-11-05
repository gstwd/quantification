"""研究数据瘦身保护规则（纯函数）。"""

from __future__ import annotations

from quant_etf_api.domain.research.retention import (
    SessionRef,
    SlimCandidate,
    plan_index_detail_slimming,
)


def _bt(
    backtest_id: str,
    *,
    status: str = "success",
    purpose: str = "research",
    optimization_id: str | None = None,
    pruned: bool = False,
) -> SlimCandidate:
    """构造候选回测。"""
    return SlimCandidate(
        backtest_id=backtest_id,
        strategy_id="s",
        status=status,
        purpose=purpose,
        optimization_id=optimization_id,
        pruned=pruned,
    )


def _plan(
    candidates: list[SlimCandidate],
    *,
    lifecycle: set[str] | None = None,
    sessions: list[SessionRef] | None = None,
    latest: set[str] | None = None,
):
    """调用规则。"""
    return plan_index_detail_slimming(
        candidates,
        lifecycle_ids=lifecycle or set(),
        sessions=sessions or [],
        latest_research_ids=latest or set(),
    )


class TestProtection:
    """各保护原因与判定顺序。"""

    def test_each_reason(self) -> None:
        """未结束、非研究用途、生命周期、最新研究回测各自归类，其余可瘦身。"""
        plan = _plan(
            [
                _bt("pending", status="pending"),
                _bt("running", status="running"),
                _bt("monitor", purpose="monitor"),
                _bt("validation", purpose="validation"),
                _bt("life"),
                _bt("latest"),
                _bt("failed", status="failed"),
                _bt("ok"),
                _bt("done", pruned=True),
            ],
            lifecycle={"life"},
            latest={"latest"},
        )
        assert plan.eligible == ("failed", "ok")
        assert plan.already_pruned == 1
        assert plan.total == 9
        assert plan.protected == {
            "unfinished": 2,
            "non_research": 2,
            "lifecycle": 1,
            "latest_research": 1,
        }

    def test_first_match_wins(self) -> None:
        """同时命中多个原因时按顺序取第一个（未结束优先于生命周期）。"""
        plan = _plan([_bt("x", status="running")], lifecycle={"x"}, latest={"x"})
        assert plan.protected == {"unfinished": 1}

    def test_open_sessions_protect_all_their_backtests(self) -> None:
        """running / evaluated 会话名下的折回测与全周期回测全部保护。"""
        sessions = [
            SessionRef("o1", "evaluated", "base_full", "cand_full"),
            SessionRef("o2", "running", None, None),
        ]
        plan = _plan(
            [
                _bt("fold1", optimization_id="o1"),
                _bt("base_full", optimization_id="o1"),
                _bt("fold2", optimization_id="o2"),
            ],
            sessions=sessions,
        )
        assert plan.eligible == ()
        assert plan.protected == {"pending_decision": 3}

    def test_accepted_session_protects_only_full_period_pair(self) -> None:
        """accepted 会话只保护全周期基线 / 候选回测，折回测可以瘦身。"""
        sessions = [SessionRef("o1", "accepted", "base_full", "cand_full")]
        plan = _plan(
            [
                _bt("fold", optimization_id="o1"),
                _bt("base_full", optimization_id="o1"),
                _bt("cand_full", optimization_id="o1"),
            ],
            sessions=sessions,
        )
        assert plan.eligible == ("fold",)
        assert plan.protected == {"pending_decision": 2}

    def test_rejected_session_not_protected(self) -> None:
        """rejected 会话的回测全部可瘦身（逐日结果仍保留给 DSR 校准）。"""
        sessions = [SessionRef("o1", "rejected", "base_full", "cand_full")]
        plan = _plan(
            [_bt("base_full", optimization_id="o1"), _bt("fold", optimization_id="o1")],
            sessions=sessions,
        )
        assert plan.eligible == ("base_full", "fold")

    def test_keeps_input_order(self) -> None:
        """可瘦身列表保持调用方给出的顺序（创建时间升序）。"""
        plan = _plan([_bt("b"), _bt("a"), _bt("c")])
        assert plan.eligible == ("b", "a", "c")
