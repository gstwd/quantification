"""移除全部 ETF 相关数据表，research_run_item 改用 index_code

系统收敛为纯指数研究：删除 etf_universe/etf_daily_bar/etf_daily_share/
etf_factor_value/etf_signal/backtest_etf_result 六张表，
并将 research_run_item.etf_code 重命名为 index_code（不新增外键，
历史数据可能包含已不存在的 ETF 代码）。

Revision ID: 0027_remove_etf
Revises: 0026_backtest_daily_missing_bar
Create Date: 2026-08-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_remove_etf"
down_revision = "0026_backtest_daily_missing_bar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除 ETF 表，重命名 research_run_item 列。"""
    # 先删依赖 etf_universe 且无后续代码引用的表
    op.drop_table("backtest_etf_result")
    op.drop_table("etf_signal")
    op.drop_table("etf_factor_value")
    op.drop_table("etf_daily_share")
    op.drop_table("etf_daily_bar")

    # research_run_item：删除指向 etf_universe 的外键并重命名列
    op.drop_constraint("research_run_item_etf_code_fkey", "research_run_item", type_="foreignkey")
    op.alter_column("research_run_item", "etf_code", new_column_name="index_code")

    # 最后删除 etf_universe 主表
    op.drop_table("etf_universe")


def downgrade() -> None:
    """恢复 ETF 表与 research_run_item.etf_code 列。"""
    op.create_table(
        "etf_universe",
        sa.Column("etf_code", sa.String(16), primary_key=True, comment="ETF 代码，如 510300"),
        sa.Column("exchange", sa.String(8), nullable=False, comment="交易所代码"),
        sa.Column("name_cn", sa.String(128), nullable=False, comment="ETF 中文简称"),
        sa.Column("fund_full_name", sa.String(256), nullable=True, comment="基金全称"),
        sa.Column("tracking_index_code", sa.String(32), nullable=True, comment="跟踪指数代码"),
        sa.Column("tracking_index_name", sa.String(128), nullable=False, comment="跟踪指数名称"),
        sa.Column("fund_company", sa.String(128), nullable=True, comment="基金管理公司名称"),
        sa.Column("listing_date", sa.Date(), nullable=True, comment="上市日期"),
        sa.Column("delisting_date", sa.Date(), nullable=True, comment="退市日期"),
        sa.Column("category", sa.String(64), nullable=True, comment="ETF 分类"),
        sa.Column("is_a_share_etf", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean(), nullable=True, comment="是否在交易中"),
        sa.Column("data_source", sa.String(32), nullable=True, comment="数据来源"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )

    # 恢复 research_run_item 列与外键
    op.alter_column("research_run_item", "index_code", new_column_name="etf_code")
    op.create_foreign_key(
        "research_run_item_etf_code_fkey",
        "research_run_item",
        "etf_universe",
        ["etf_code"],
        ["etf_code"],
    )

    op.create_table(
        "etf_daily_bar",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column("open_price", sa.Float, nullable=True),
        sa.Column("high_price", sa.Float, nullable=True),
        sa.Column("low_price", sa.Float, nullable=True),
        sa.Column("close_price", sa.Float, nullable=True),
        sa.Column("prev_close_price", sa.Float, nullable=True),
        sa.Column("change_pct", sa.Float, nullable=True),
        sa.Column("volume", sa.Float, nullable=True),
        sa.Column("turnover", sa.Float, nullable=True),
        sa.Column("amplitude", sa.Float, nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("trade_date", "etf_code", name="uq_etf_daily_bar"),
    )

    op.create_table(
        "etf_daily_share",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column("shares_total", sa.Float, nullable=True),
        sa.Column("shares_delta", sa.Float, nullable=True),
        sa.Column("shares_delta_pct", sa.Float, nullable=True),
        sa.Column("nav", sa.Float, nullable=True),
        sa.Column("aum", sa.Float, nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("trade_date", "etf_code", name="uq_etf_daily_share"),
    )

    op.create_table(
        "etf_factor_value",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column(
            "factor_id", sa.String(64), sa.ForeignKey("factor_definition.factor_id"), nullable=False
        ),
        sa.Column("factor_value_numeric", sa.Float, nullable=True),
        sa.Column("factor_value_text", sa.String(128), nullable=True),
        sa.Column("factor_payload", sa.JSON(), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.UniqueConstraint(
            "trade_date", "etf_code", "factor_id", "strategy_id", name="uq_etf_factor_value"
        ),
    )
    op.create_index(
        "uq_etf_factor_value_builtin",
        "etf_factor_value",
        ["trade_date", "etf_code", "factor_id"],
        unique=True,
        postgresql_where=sa.text("strategy_id IS NULL"),
    )

    op.create_table(
        "etf_signal",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("signal_score", sa.Float, nullable=False),
        sa.Column("signal_level", sa.String(32), nullable=False),
        sa.Column("signal_label", sa.String(128), nullable=False),
        sa.Column("signal_payload", sa.JSON(), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.UniqueConstraint("trade_date", "etf_code", "strategy_id", name="uq_etf_signal"),
    )

    op.create_table(
        "backtest_etf_result",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "backtest_id",
            sa.String(64),
            sa.ForeignKey("backtest_run.backtest_id"),
            nullable=False,
        ),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column("signal_score", sa.Float, nullable=False),
        sa.Column("signal_level", sa.String(32), nullable=False),
        sa.Column("in_portfolio", sa.Boolean(), nullable=False),
        sa.Column("etf_return", sa.Float, nullable=True),
        sa.UniqueConstraint("backtest_id", "trade_date", "etf_code", name="uq_backtest_etf"),
    )
    op.create_index("ix_backtest_etf_backtest_id", "backtest_etf_result", ["backtest_id"])
    op.create_index("ix_backtest_etf_code", "backtest_etf_result", ["backtest_id", "etf_code"])
