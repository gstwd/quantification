"""新增 index_signal 表

策略执行结果统一使用指数数据，新增指数信号表替代 etf_signal。

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "index_signal",
        sa.Column(
            "id", sa.Integer, primary_key=True, autoincrement=True, comment="自增主键"
        ),
        sa.Column(
            "trade_date", sa.Date, nullable=False, comment="信号对应的交易日期"
        ),
        sa.Column(
            "index_code",
            sa.String(32),
            sa.ForeignKey("benchmark_index.index_code"),
            nullable=False,
            comment="指数代码",
        ),
        sa.Column(
            "strategy_id",
            sa.String(64),
            nullable=False,
            comment="产生该信号的策略 ID",
        ),
        sa.Column(
            "signal_score", sa.Float, nullable=False, comment="综合得分，0-100"
        ),
        sa.Column(
            "signal_level",
            sa.String(32),
            nullable=False,
            comment="信号等级：HIGH/MID/LOW",
        ),
        sa.Column(
            "signal_label",
            sa.String(128),
            nullable=False,
            comment="信号中文标签",
        ),
        sa.Column("signal_payload", sa.JSON, nullable=True, comment="信号计算明细"),
        sa.Column(
            "run_id",
            sa.String(64),
            nullable=True,
            comment="产生该信号的研究运行 ID",
        ),
        sa.UniqueConstraint(
            "trade_date", "index_code", "strategy_id", name="uq_index_signal"
        ),
    )
    op.create_index(
        "ix_index_signal_strategy", "index_signal", ["strategy_id", "trade_date"]
    )


def downgrade() -> None:
    op.drop_index("ix_index_signal_strategy", table_name="index_signal")
    op.drop_table("index_signal")
