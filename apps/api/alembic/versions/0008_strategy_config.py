"""策略配置表

新增 strategy_config 表，存储 JSON 格式的完整策略定义。
所有策略通过配置驱动，无需硬编码策略逻辑。

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "strategy_config",
        sa.Column("strategy_id", sa.String(64), primary_key=True, comment="策略唯一标识"),
        sa.Column("display_name", sa.String(128), nullable=False, comment="策略中文显示名称"),
        sa.Column(
            "version", sa.String(32), nullable=False, server_default="1.0.0", comment="策略版本号"
        ),
        sa.Column("description", sa.Text, nullable=True, comment="策略描述"),
        sa.Column(
            "frequency",
            sa.String(32),
            nullable=False,
            server_default="daily",
            comment="运行频率",
        ),
        sa.Column(
            "asset_scope",
            sa.String(64),
            nullable=False,
            server_default="a_share_etf",
            comment="资产范围",
        ),
        sa.Column("config_json", sa.JSON, nullable=False, comment="完整策略配置 JSON"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="active",
            comment="状态：active=启用, disabled=禁用",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="记录创建时间（UTC）",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            comment="记录最后更新时间（UTC）",
        ),
    )


def downgrade() -> None:
    op.drop_table("strategy_config")
