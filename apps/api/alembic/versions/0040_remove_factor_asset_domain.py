"""删除已废弃的因子值挂载域字段。

正式因子全部作用于可交易的 benchmark_index；申万行业与个股仅作为复合因子的
内部研究数据，不再作为因子挂载域，因此 factor_definition.asset_domain 不再需要。

Revision ID: 0040_remove_factor_asset_domain
Revises: 0039_merge_backtest_and_main_heads
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040_remove_factor_asset_domain"
down_revision = "0039_merge_backtest_and_main_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除因子定义表中的废弃挂载域字段。"""
    op.drop_column("factor_definition", "asset_domain")


def downgrade() -> None:
    """恢复因子定义表中的挂载域字段，并统一填充为指数域。"""
    op.add_column(
        "factor_definition",
        sa.Column(
            "asset_domain",
            sa.String(16),
            nullable=False,
            server_default="index",
            comment="因子值挂载域：全部为可交易指数域",
        ),
    )
