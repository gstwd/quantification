"""将因子值形态收敛为每资产值或市场级值。

正式因子最终都作用于指数资产，行业面板仅作为内部计算输入，不再作为因子输出形态。

Revision ID: 0041_restrict_factor_value_shape
Revises: 0040_remove_factor_asset_domain
Create Date: 2026-09-11
"""

from __future__ import annotations

from alembic import op

revision = "0041_restrict_factor_value_shape"
down_revision = "0040_remove_factor_asset_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """将历史未知值归一为每资产值，并增加数据库检查约束。"""
    op.execute(
        "UPDATE factor_definition SET value_shape = 'asset' "
        "WHERE value_shape NOT IN ('asset', 'market')"
    )
    op.create_check_constraint(
        "ck_factor_definition_value_shape",
        "factor_definition",
        "value_shape IN ('asset', 'market')",
    )


def downgrade() -> None:
    """删除因子值形态检查约束。"""
    op.drop_constraint(
        "ck_factor_definition_value_shape",
        "factor_definition",
        type_="check",
    )
