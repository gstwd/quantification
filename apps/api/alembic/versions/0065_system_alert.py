"""站内告警表 system_alert。

Revision ID: 0065_system_alert
Revises: 0064_market_indicator
Create Date: 2026-10-24

LIVE 策略每日定时体检的健康等级恶化、体检失败与每日数据同步失败写入站内告警，
前端顶栏展示未读数。新表无存量数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065_system_alert"
down_revision = "0064_market_indicator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 system_alert 表。"""
    op.create_table(
        "system_alert",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("kind", sa.String(length=32), nullable=False, comment="告警类型"),
        sa.Column("severity", sa.String(length=16), nullable=False, comment="严重程度"),
        sa.Column("strategy_id", sa.String(length=64), nullable=True, comment="相关策略 ID"),
        sa.Column("title", sa.String(length=200), nullable=False, comment="告警标题"),
        sa.Column("detail", postgresql.JSONB(), nullable=True, comment="告警明细"),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False, comment="去重键"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="告警时间（UTC）"),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True, comment="已读时间（UTC）"),
        sa.UniqueConstraint("dedupe_key", name="uq_system_alert_dedupe"),
        comment="站内告警（健康恶化、体检失败、数据同步失败）",
    )
    op.create_index("ix_system_alert_created_at", "system_alert", ["created_at"])
    op.create_index("ix_system_alert_read_at", "system_alert", ["read_at"])


def downgrade() -> None:
    """删除 system_alert 表。"""
    op.drop_index("ix_system_alert_read_at", table_name="system_alert")
    op.drop_index("ix_system_alert_created_at", table_name="system_alert")
    op.drop_table("system_alert")
