"""因子元数据四轴与行业因子参数指纹。

- factor_definition 增加代码管控四轴列：asset_domain（资产域）、
  value_shape（值形态）、usage（适用位置）、default_params（默认参数）；
- industry_factor_value 增加 params_hash/params 列，唯一键扩为
  (trade_date, industry_code, factor_id, params_hash)，不同参数计算结果分行，
  避免同 factor_id 不同参数互相覆盖（P12）。

Revision ID: 0032_factor_meta_industry_params
Revises: 0031_industry_data_management
Create Date: 2026-09-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_factor_meta_industry_params"
down_revision = "0031_industry_data_management"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """增加因子元数据四轴列与行业因子参数指纹列。"""
    op.add_column(
        "factor_definition",
        sa.Column(
            "asset_domain",
            sa.String(16),
            nullable=False,
            server_default="index",
            comment="因子资产域：index=宽基/行业指数域，industry=申万一级行业域",
        ),
    )
    op.add_column(
        "factor_definition",
        sa.Column(
            "value_shape",
            sa.String(16),
            nullable=False,
            server_default="asset",
            comment="因子值形态：asset=每资产值，market=市场级值，panel=面板值",
        ),
    )
    op.add_column(
        "factor_definition",
        sa.Column(
            "usage",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[\"timing\",\"score\",\"filter\",\"rank\"]'::json"),
            comment="适用位置：timing/score/filter/rank/rotation_input",
        ),
    )
    op.add_column(
        "factor_definition",
        sa.Column(
            "default_params",
            sa.JSON(),
            nullable=True,
            comment="因子默认参数（参数化因子，如行业 RRG/扩散的默认口径）",
        ),
    )
    op.add_column(
        "industry_factor_value",
        sa.Column(
            "params_hash",
            sa.String(64),
            nullable=False,
            server_default="",
            comment="参数指纹（规范化参数字典 sha256），区分同 factor_id 不同参数计算",
        ),
    )
    op.add_column(
        "industry_factor_value",
        sa.Column(
            "params",
            sa.JSON(),
            nullable=True,
            comment="计算参数字典（lookback/smooth/基准剔除等）",
        ),
    )
    op.add_column(
        "backtest_run",
        sa.Column(
            "asset_domain",
            sa.String(16),
            nullable=False,
            server_default="index",
            comment="回测资产域：index=宽基/行业指数域，industry=申万一级行业域",
        ),
    )
    # 行业轮动回测结果允许写 801xxx 申万行业代码，去掉指数外键
    op.drop_constraint(
        "backtest_index_result_index_code_fkey",
        "backtest_index_result",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_industry_factor_value",
        "industry_factor_value",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_industry_factor_value_params",
        "industry_factor_value",
        ["trade_date", "industry_code", "factor_id", "params_hash"],
    )


def downgrade() -> None:
    """删除因子元数据四轴列并回退行业因子值唯一键。"""
    op.create_foreign_key(
        "backtest_index_result_index_code_fkey",
        "backtest_index_result",
        "benchmark_index",
        ["index_code"],
        ["index_code"],
    )
    op.drop_column("backtest_run", "asset_domain")
    op.drop_constraint(
        "uq_industry_factor_value_params",
        "industry_factor_value",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_industry_factor_value",
        "industry_factor_value",
        ["trade_date", "industry_code", "factor_id"],
    )
    op.drop_column("industry_factor_value", "params")
    op.drop_column("industry_factor_value", "params_hash")
    op.drop_column("factor_definition", "default_params")
    op.drop_column("factor_definition", "usage")
    op.drop_column("factor_definition", "value_shape")
    op.drop_column("factor_definition", "asset_domain")
