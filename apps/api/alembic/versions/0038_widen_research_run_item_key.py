"""加宽运行明细对象键列以容纳数据集键。

Revision ID: 0038_widen_research_run_item_key
Revises: 0037_data_management_health_snapshots
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_widen_research_run_item_key"
down_revision = "0037_data_management_health_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """将 research_run_item.index_code 从 16 加宽到 64。

    该列历史上只存 6 位指数代码；统一数据管理把数据集键（如
    industry_daily_bar）写入后触发 StringDataRightTruncation。
    """
    op.alter_column(
        "research_run_item",
        "index_code",
        existing_type=sa.String(16),
        type_=sa.String(64),
        existing_nullable=False,
    )


def downgrade() -> None:
    """还原为 16 字符（需先确认不存在超长键值）。"""
    op.alter_column(
        "research_run_item",
        "index_code",
        existing_type=sa.String(64),
        type_=sa.String(16),
        existing_nullable=False,
    )
