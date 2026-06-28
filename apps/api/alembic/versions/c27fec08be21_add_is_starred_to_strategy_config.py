"""add is_starred to strategy_config

Revision ID: c27fec08be21
Revises: 0018
Create Date: 2026-06-27 20:09:35.466788
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c27fec08be21"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """在 strategy_config 表添加 is_starred 列，默认不星标。"""
    op.add_column(
        "strategy_config",
        sa.Column(
            "is_starred",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
            comment="是否星标关注",
        ),
    )


def downgrade() -> None:
    """移除 is_starred 列。"""
    op.drop_column("strategy_config", "is_starred")
