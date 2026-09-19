"""策略研究数据清理服务测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from quant_etf_api.services.strategy_config_service import StrategyConfigService


class TestClearResearchData:
    """清理操作必须保留策略配置，并保证事务边界明确。"""

    def _service(
        self, *, exists: bool = True
    ) -> tuple[StrategyConfigService, MagicMock, MagicMock]:
        db = MagicMock()
        service = StrategyConfigService(db)
        config_repo = MagicMock()
        config_repo.find_by_id.return_value = object() if exists else None
        research_repo = MagicMock()
        research_repo.find_related_draft_strategy_ids.side_effect = [
            {"momentum__candidate", "momentum__variant"},
            set(),
        ]
        research_repo.delete_by_strategies.return_value = {
            "backtest_daily_result": 12,
            "backtest_index_result": 36,
            "backtest_comparison": 2,
            "backtest_run": 3,
            "robustness_run": 1,
            "strategy_optimization": 1,
        }
        research_repo.delete_draft_strategy_configs.return_value = 2
        service._repo = config_repo
        service._research_repo = research_repo
        return service, db, research_repo

    def test_clears_research_data_and_keeps_strategy(self) -> None:
        service, db, research_repo = self._service()

        result = service.clear_research_data("momentum")

        assert result is not None
        assert result["backtest_run"] == 3
        research_repo.delete_by_strategies.assert_called_once_with(
            {"momentum", "momentum__candidate", "momentum__variant"}
        )
        research_repo.delete_draft_strategy_configs.assert_called_once_with(
            {"momentum__candidate", "momentum__variant"}
        )
        db.commit.assert_called_once()
        db.rollback.assert_not_called()

    def test_returns_none_without_deleting_when_strategy_is_missing(self) -> None:
        service, db, research_repo = self._service(exists=False)

        assert service.clear_research_data("missing") is None
        research_repo.delete_by_strategies.assert_not_called()
        db.commit.assert_not_called()

    def test_rolls_back_when_repository_cleanup_fails(self) -> None:
        service, db, research_repo = self._service()
        research_repo.find_related_draft_strategy_ids.side_effect = RuntimeError("database failure")

        with pytest.raises(RuntimeError, match="database failure"):
            service.clear_research_data("momentum")

        db.rollback.assert_called_once()
        db.commit.assert_not_called()
