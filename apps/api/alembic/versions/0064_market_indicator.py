"""日频市场指标表 market_indicator。

Revision ID: 0064_market_indicator
Revises: 0063_campaign_desktop_orchestration
Create Date: 2026-10-24

国债 10 年收益率、沪深两融余额与买入额、股指期货主力合约收盘与剩余天数、中金所股指期权
PCR 等按交易日更新的市场级序列。与 macro_indicator（按发布周期）分表，数据管理按交易日
新鲜度判定；新表无存量数据，不影响任何历史回测。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0064_market_indicator"
down_revision = "0063_campaign_desktop_orchestration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 market_indicator 表与 (指标, 交易日) 唯一约束。"""
    op.create_table(
        "market_indicator",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("indicator_code", sa.String(length=32), nullable=False, comment="指标代码"),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日"),
        sa.Column("value", sa.Float(), nullable=False, comment="指标数值"),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="tushare",
            comment="数据来源：tushare / akshare",
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="数据入库时间（UTC）",
        ),
        sa.UniqueConstraint("indicator_code", "trade_date", name="uq_market_indicator"),
        comment="日频市场指标（国债收益率、两融、股指期货主力、股指期权 PCR）",
    )
    op.create_index(
        "ix_market_indicator_code_date", "market_indicator", ["indicator_code", "trade_date"]
    )


def downgrade() -> None:
    """删除 market_indicator 表。"""
    op.drop_index("ix_market_indicator_code_date", table_name="market_indicator")
    op.drop_table("market_indicator")
