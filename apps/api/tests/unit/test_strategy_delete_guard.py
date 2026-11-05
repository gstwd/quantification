"""删除策略的保护：上线中、仍有成员的研究线根、已有试验的策略不能删除。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quant_etf_api.services.strategy_config_service import StrategyConfigService


def _service(
    *,
    lineage_id: str | None = None,
    lifecycle: str | None = None,
    members: list[str] | None = None,
    trials: int = 0,
) -> StrategyConfigService:
    """构造 Mock 依赖的策略配置服务（待删除策略为 s1）。"""
    svc = StrategyConfigService(db=MagicMock())
    svc._repo = MagicMock()
    svc._repo.find_by_id.return_value = SimpleNamespace(strategy_id="s1", lineage_id=lineage_id)
    svc._repo.find_lineage_members.return_value = [
        SimpleNamespace(strategy_id=sid) for sid in (members or ["s1"])
    ]
    svc._repo.delete_by_id.return_value = True
    svc._db.get.return_value = (
        SimpleNamespace(lifecycle_status=lifecycle) if lifecycle is not None else None
    )
    svc._ledger_repo = MagicMock()
    svc._ledger_repo.sum_robustness_trials.return_value = trials
    svc._ledger_repo.count_evaluated_optimizations.return_value = 0
    svc._ledger_repo.sum_ledger_trials.return_value = {}
    return svc


class TestDeleteGuard:
    """删除策略前的保护条件。"""

    @pytest.mark.parametrize("status", ["LIVE", "SUSPENDED"])
    def test_live_or_suspended_refused(self, status: str) -> None:
        """上线或暂停中的策略拒绝删除。"""
        svc = _service(lifecycle=status)
        with pytest.raises(ValueError, match=status):
            svc.delete_config("s1")
        svc._repo.delete_by_id.assert_not_called()

    def test_retired_allowed(self) -> None:
        """已退役且无其他保护条件时可以删除。"""
        svc = _service(lifecycle="RETIRED")
        assert svc.delete_config("s1") is True
        svc._db.commit.assert_called_once()

    def test_root_with_members_refused(self) -> None:
        """研究线根仍有成员时拒绝删除。"""
        svc = _service(members=["s1", "s1__opt_a"])
        with pytest.raises(ValueError, match="研究线根"):
            svc.delete_config("s1")

    def test_member_without_trials_allowed(self) -> None:
        """无试验的普通成员可以删除（不检查成员列表）。"""
        svc = _service(lineage_id="root", members=["root", "s1"])
        assert svc.delete_config("s1") is True

    def test_own_trials_refused(self) -> None:
        """自身已有试验的策略拒绝删除，提示 N 会被拿走。"""
        svc = _service(lineage_id="root", trials=3)
        with pytest.raises(ValueError, match="3 次试验"):
            svc.delete_config("s1")
        svc._repo.delete_by_id.assert_not_called()

    def test_missing_returns_false(self) -> None:
        """策略不存在返回 False。"""
        svc = _service()
        svc._repo.find_by_id.return_value = None
        assert svc.delete_config("s1") is False
