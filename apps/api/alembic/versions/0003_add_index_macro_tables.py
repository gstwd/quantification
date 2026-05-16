"""add index_valuation and macro_indicator tables, seed benchmark_index

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. 创建指数估值表
    op.create_table(
        "index_valuation",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("trade_date", sa.Date, nullable=False, comment="估值日期"),
        sa.Column(
            "index_code",
            sa.String(32),
            sa.ForeignKey("benchmark_index.index_code"),
            nullable=False,
            comment="指数代码，外键关联 benchmark_index",
        ),
        sa.Column("pe", sa.Float, nullable=True, comment="市盈率 PE(TTM)"),
        sa.Column(
            "pe_percentile",
            sa.Float,
            nullable=True,
            comment="PE 历史分位，0-100，数值越小越低估",
        ),
        sa.Column("pb", sa.Float, nullable=True, comment="市净率 PB"),
        sa.Column(
            "pb_percentile",
            sa.Float,
            nullable=True,
            comment="PB 历史分位，0-100，数值越小越低估",
        ),
        sa.Column("dividend_yield", sa.Float, nullable=True, comment="股息率，单位 %"),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="akshare",
            comment="数据来源，akshare=AkShare 客户端",
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("now()"),
            comment="数据入库时间（UTC）",
        ),
        sa.UniqueConstraint("trade_date", "index_code", name="uq_index_valuation"),
    )

    # 2. 创建宏观指标表
    op.create_table(
        "macro_indicator",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column(
            "indicator_code",
            sa.String(32),
            nullable=False,
            comment="指标代码：cpi=CPI 同比，pmi=制造业PMI，lpr1y=LPR 1年期，lpr5y=LPR 5年期",
        ),
        sa.Column(
            "indicator_name",
            sa.String(64),
            nullable=False,
            comment="指标中文名，如 居民消费价格指数(CPI)同比",
        ),
        sa.Column(
            "period",
            sa.String(16),
            nullable=False,
            comment="数据周期，如 2024-01（月度）、2024-01-20（LPR 报价日）",
        ),
        sa.Column("value", sa.Float, nullable=False, comment="指标数值"),
        sa.Column("unit", sa.String(32), nullable=True, comment="单位，如 %"),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="akshare",
            comment="数据来源，akshare=AkShare 客户端",
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("now()"),
            comment="数据入库时间（UTC）",
        ),
        sa.UniqueConstraint("indicator_code", "period", name="uq_macro_indicator"),
    )

    # 3. 写入常用指数种子数据
    indexes = [
        ("000300", "沪深300", "CN"),
        ("000016", "上证50", "CN"),
        ("000905", "中证500", "CN"),
        ("000688", "科创50", "CN"),
        ("399001", "深证成指", "CN"),
        ("399006", "创业板指", "CN"),
    ]
    benchmark_index = sa.table(
        "benchmark_index",
        sa.column("index_code", sa.String),
        sa.column("name_cn", sa.String),
        sa.column("exchange", sa.String),
    )
    op.bulk_insert(
        benchmark_index,
        [{"index_code": c, "name_cn": n, "exchange": e} for c, n, e in indexes],
    )


def downgrade() -> None:
    op.drop_table("macro_indicator")
    op.drop_table("index_valuation")
    # 不删除种子数据，rollback 时保留不做破坏性删除
