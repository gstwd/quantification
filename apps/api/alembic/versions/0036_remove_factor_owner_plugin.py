"""移除因子定义中已废弃的插件归属字段。

因子注册与同步已经由代码内置注册表负责，系统不存在按插件归属因子的运行逻辑，
``factor_definition.owner_plugin`` 仅为早期插件机制留下的数据库残留。

Revision ID: 0036_remove_factor_owner_plugin
Revises: 0035_remove_backtest_asset_domain
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_remove_factor_owner_plugin"
down_revision = "0035_remove_backtest_asset_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除因子定义表中无消费方的插件归属列。"""
    op.drop_column("factor_definition", "owner_plugin")


def downgrade() -> None:
    """恢复因子定义表的插件归属列以兼容旧版本数据库。"""
    op.add_column(
        "factor_definition",
        sa.Column(
            "owner_plugin",
            sa.String(64),
            nullable=True,
            comment="历史遗留字段，所有内置因子均为 NULL",
        ),
    )
