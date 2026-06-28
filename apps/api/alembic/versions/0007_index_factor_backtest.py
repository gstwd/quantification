"""指数因子值表 + 指数回测结果表

新增 index_factor_value 表存储指数级因子值，
新增 backtest_index_result 表存储指数级回测每日结果。

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── index_factor_value 表 ──────────────────────────────────────────────
    op.create_table(
        "index_factor_value",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("trade_date", sa.Date, nullable=False, comment="交易日期"),
        sa.Column(
            "index_code",
            sa.String(32),
            sa.ForeignKey("benchmark_index.index_code"),
            nullable=False,
            comment="指数代码，外键关联 benchmark_index",
        ),
        sa.Column(
            "factor_id",
            sa.String(64),
            sa.ForeignKey("factor_definition.factor_id"),
            nullable=False,
            comment="因子 ID，外键关联 factor_definition",
        ),
        sa.Column("factor_value_numeric", sa.Float, comment="因子数值"),
        sa.Column("factor_value_text", sa.String(128), comment="因子文本值"),
        sa.Column("factor_payload", sa.JSON, comment="因子计算中间数据"),
        sa.Column("strategy_id", sa.String(64), comment="策略 ID，NULL 表示通用因子"),
        sa.UniqueConstraint(
            "trade_date",
            "index_code",
            "factor_id",
            "strategy_id",
            name="uq_index_factor_value",
        ),
    )
    # partial unique index：独立因子（strategy_id IS NULL）的唯一约束
    op.create_index(
        "uq_index_factor_value_builtin",
        "index_factor_value",
        ["trade_date", "index_code", "factor_id"],
        unique=True,
        postgresql_where=sa.text("strategy_id IS NULL"),
    )

    # ── backtest_index_result 表 ──────────────────────────────────────────
    op.create_table(
        "backtest_index_result",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column(
            "backtest_id",
            sa.String(64),
            sa.ForeignKey("backtest_run.backtest_id"),
            nullable=False,
            comment="所属回测 ID",
        ),
        sa.Column("trade_date", sa.Date, nullable=False, comment="信号生成日期（T 日）"),
        sa.Column(
            "index_code",
            sa.String(32),
            sa.ForeignKey("benchmark_index.index_code"),
            nullable=False,
            comment="指数代码",
        ),
        sa.Column("signal_score", sa.Float, nullable=False, comment="信号综合得分，0-100"),
        sa.Column("signal_level", sa.String(32), nullable=False, comment="信号等级：HIGH/MID/LOW"),
        sa.Column("in_portfolio", sa.Boolean, nullable=False, comment="是否纳入当日组合"),
        sa.Column("index_return", sa.Float, comment="T+1 日指数收益率，单位 %"),
        sa.UniqueConstraint(
            "backtest_id",
            "trade_date",
            "index_code",
            name="uq_backtest_index",
        ),
    )
    op.create_index("ix_backtest_index_backtest_id", "backtest_index_result", ["backtest_id"])
    op.create_index(
        "ix_backtest_index_code",
        "backtest_index_result",
        ["backtest_id", "index_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_backtest_index_code", table_name="backtest_index_result")
    op.drop_index("ix_backtest_index_backtest_id", table_name="backtest_index_result")
    op.drop_table("backtest_index_result")
    op.drop_index(
        "uq_index_factor_value_builtin",
        table_name="index_factor_value",
        postgresql_where=sa.text("strategy_id IS NULL"),
    )
    op.drop_table("index_factor_value")
