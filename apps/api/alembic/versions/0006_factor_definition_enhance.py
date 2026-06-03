"""因子定义表增强：新增 is_active 和 required_data 列

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # is_active: 控制因子是否参与计算和前端展示，默认启用
    op.add_column(
        "factor_definition",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default="true",
            comment="是否启用，禁用后不参与计算且前端隐藏",
        ),
    )

    # required_data: 存储因子依赖的数据源列表（如 ["etf_bars"]），由代码同步
    op.add_column(
        "factor_definition",
        sa.Column(
            "required_data",
            sa.JSON(),
            nullable=True,
            comment="依赖的数据源列表，如 ['etf_bars']，由代码同步",
        ),
    )


def downgrade() -> None:
    op.drop_column("factor_definition", "required_data")
    op.drop_column("factor_definition", "is_active")
