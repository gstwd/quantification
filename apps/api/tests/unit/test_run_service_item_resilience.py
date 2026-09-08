"""运行明细写入失败后的会话恢复测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from quant_etf_api.services.run_service import RunService


def test_add_item_rolls_back_session_when_repo_write_fails() -> None:
    """明细写入失败时 add_item 必须回滚会话，避免毒化后续数据集处理。"""
    db = MagicMock()
    repo = MagicMock()
    repo.add_item.side_effect = RuntimeError("value too long")
    svc = RunService(db=db, run_repo=repo)

    svc.add_item("run-1", "industry_daily_bar", "success")

    db.rollback.assert_called_once()


def test_add_item_repo_rolls_back_before_reraise() -> None:
    """仓库层在 commit 失败时也应先回滚再抛出，供上层恢复会话。"""
    from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository

    db = MagicMock()
    db.commit.side_effect = RuntimeError("boom")
    repo = ResearchRunRepository(db=db)

    try:
        repo.add_item("run-1", "index_daily_bar", "success")
    except RuntimeError:
        pass

    db.rollback.assert_called_once()
