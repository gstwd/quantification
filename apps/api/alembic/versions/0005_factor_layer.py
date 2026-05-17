"""因子层独立化：owner_plugin 改可空、新增 category 列、partial unique index

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # factor_definition.owner_plugin: NOT NULL → nullable
    # 独立因子（非插件所有）的 owner_plugin 置为 NULL
    op.alter_column(
        "factor_definition",
        "owner_plugin",
        existing_type=sa.String(64),
        nullable=True,
        comment="定义该因子的策略插件 ID，NULL 表示独立因子",
    )

    # factor_definition.category: 新增因子类别列
    op.add_column(
        "factor_definition",
        sa.Column(
            "category",
            sa.String(32),
            nullable=True,
            comment="因子类别：volume/momentum/volatility/flow/valuation",
        ),
    )

    # etf_factor_value: 新增 partial unique index
    # 解决 PostgreSQL 中 NULL != NULL 导致独立因子（strategy_id IS NULL）重复插入的问题。
    # ON CONFLICT DO UPDATE 必须引用此 partial index 才能正确识别冲突键。
    op.create_index(
        "uq_etf_factor_value_builtin",
        "etf_factor_value",
        ["trade_date", "etf_code", "factor_id"],
        unique=True,
        postgresql_where=sa.text("strategy_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_etf_factor_value_builtin",
        table_name="etf_factor_value",
        postgresql_where=sa.text("strategy_id IS NULL"),
    )
    op.drop_column("factor_definition", "category")
    # 注意：downgrade 前需确保 DB 中 owner_plugin 无 NULL 值，否则会报错
    op.alter_column(
        "factor_definition",
        "owner_plugin",
        existing_type=sa.String(64),
        nullable=False,
    )
