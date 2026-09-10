"""合并回测执行持仓迁移与主迁移链。

Revision ID: 0039_merge_backtest_and_main_heads
Revises: 0035_backtest_executed_positions, 0038_widen_research_run_item_key
Create Date: 2026-09-10
"""

from __future__ import annotations

revision = "0039_merge_backtest_and_main_heads"
down_revision = ("0035_backtest_executed_positions", "0038_widen_research_run_item_key")
branch_labels = None
depends_on = None


def upgrade() -> None:
    """合并两条已完成的迁移分支，不执行额外数据库变更。"""
    pass


def downgrade() -> None:
    """回滚合并节点，不回滚两条父分支中的实际变更。"""
    pass
