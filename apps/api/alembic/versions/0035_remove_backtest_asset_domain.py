"""移除回测主表中不再使用的资产域字段。

回测资产范围已经由策略配置中的 ``index_codes`` 与 ``universe_filter`` 表达，
``backtest_run.asset_domain`` 始终为 ``index``，不再参与创建、执行或查询逻辑。

Revision ID: 0035_remove_backtest_asset_domain
Revises: 0034_remove_signal_mode_residue
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035_remove_backtest_asset_domain"
down_revision = "0034_remove_signal_mode_residue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除回测主表中无消费方的历史资产域列。"""
    op.drop_column("backtest_run", "asset_domain")


def downgrade() -> None:
    """恢复回测主表资产域列，并使用指数域作为默认值。"""
    op.add_column(
        "backtest_run",
        sa.Column(
            "asset_domain",
            sa.String(16),
            nullable=False,
            server_default="index",
            comment="历史遗留列：始终为 index（行业域回测已移除）",
        ),
    )
