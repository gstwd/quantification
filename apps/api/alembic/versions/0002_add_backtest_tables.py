"""add backtest tables

Revision ID: 0002
Revises: ac0cbcadbda1
Create Date: 2026-05-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "ac0cbcadbda1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_run",
        sa.Column("backtest_id", sa.String(64), primary_key=True, comment="回测唯一 ID，UUID 格式"),
        sa.Column("strategy_id", sa.String(64), nullable=False, comment="关联策略 ID"),
        sa.Column("start_date", sa.Date, nullable=False, comment="回测起始日期"),
        sa.Column("end_date", sa.Date, nullable=False, comment="回测结束日期"),
        sa.Column("universe_filter", postgresql.JSONB, nullable=False, comment="标的过滤条件"),
        sa.Column("params", postgresql.JSONB, nullable=True, comment="策略参数覆盖"),
        sa.Column("weighting", sa.String(32), nullable=False, server_default="equal", comment="组合加权方式"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending", comment="回测状态"),
        sa.Column("error_message", sa.Text, nullable=True, comment="失败时的错误信息"),
        sa.Column("metrics", postgresql.JSONB, nullable=True, comment="汇总绩效指标"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()"), comment="创建时间（UTC）"),
        sa.Column("started_at", sa.DateTime, nullable=True, comment="开始执行时间（UTC）"),
        sa.Column("finished_at", sa.DateTime, nullable=True, comment="完成时间（UTC）"),
    )

    op.create_table(
        "backtest_daily_result",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("backtest_id", sa.String(64), sa.ForeignKey("backtest_run.backtest_id"), nullable=False, comment="所属回测 ID"),
        sa.Column("trade_date", sa.Date, nullable=False, comment="交易日期"),
        sa.Column("portfolio_return", sa.Float, nullable=False, comment="当日组合收益率（%）"),
        sa.Column("cumulative_return", sa.Float, nullable=False, comment="累计收益率（%）"),
        sa.Column("drawdown", sa.Float, nullable=False, comment="当日回撤（%，负值）"),
        sa.Column("high_signal_count", sa.Integer, nullable=False, comment="HIGH 信号 ETF 数量"),
        sa.Column("mid_signal_count", sa.Integer, nullable=False, comment="MID 信号 ETF 数量"),
        sa.Column("low_signal_count", sa.Integer, nullable=False, comment="LOW 信号 ETF 数量"),
        sa.UniqueConstraint("backtest_id", "trade_date", name="uq_backtest_daily"),
    )
    op.create_index("ix_backtest_daily_backtest_id", "backtest_daily_result", ["backtest_id"])

    op.create_table(
        "backtest_etf_result",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("backtest_id", sa.String(64), sa.ForeignKey("backtest_run.backtest_id"), nullable=False, comment="所属回测 ID"),
        sa.Column("trade_date", sa.Date, nullable=False, comment="信号生成日期（T 日）"),
        sa.Column("etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False, comment="ETF 代码"),
        sa.Column("signal_score", sa.Float, nullable=False, comment="信号综合得分，0-100"),
        sa.Column("signal_level", sa.String(32), nullable=False, comment="信号等级：HIGH/MID/LOW"),
        sa.Column("in_portfolio", sa.Boolean, nullable=False, comment="是否纳入当日组合"),
        sa.Column("etf_return", sa.Float, nullable=True, comment="T+1 日实际收益率（%），末日为 NULL"),
        sa.UniqueConstraint("backtest_id", "trade_date", "etf_code", name="uq_backtest_etf"),
    )
    op.create_index("ix_backtest_etf_backtest_id", "backtest_etf_result", ["backtest_id"])
    op.create_index("ix_backtest_etf_code", "backtest_etf_result", ["backtest_id", "etf_code"])


def downgrade() -> None:
    op.drop_index("ix_backtest_etf_code", table_name="backtest_etf_result")
    op.drop_index("ix_backtest_etf_backtest_id", table_name="backtest_etf_result")
    op.drop_table("backtest_etf_result")
    op.drop_index("ix_backtest_daily_backtest_id", table_name="backtest_daily_result")
    op.drop_table("backtest_daily_result")
    op.drop_table("backtest_run")
