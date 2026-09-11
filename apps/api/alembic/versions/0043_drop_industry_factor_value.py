"""删除已废弃的行业因子物化表。"""

from __future__ import annotations

from alembic import op

revision = "0043_drop_industry_factor_value"
down_revision = "0042_remove_stock_quality_snapshot_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除行业因子中间结果表。"""
    op.drop_table("industry_factor_value")


def downgrade() -> None:
    """恢复行业因子中间结果表结构。"""
    raise NotImplementedError("已废弃的行业因子表不支持自动回滚")
