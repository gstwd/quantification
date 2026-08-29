"""backtest_run 增加配置快照列，新建 strategy_optimization 优化会话表

Revision ID: 0028_backtest_snapshot_optimization
Revises: 0027_remove_etf
Create Date: 2026-08-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0028_backtest_snapshot"
down_revision = "0027_remove_etf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增回测配置快照列与策略优化会话表。"""
    op.add_column(
        "backtest_run",
        sa.Column(
            "config_snapshot",
            JSONB(),
            nullable=True,
            comment="创建时的策略配置快照（元数据 + config_json），保证回测结果可复现",
        ),
    )
    op.add_column(
        "backtest_run",
        sa.Column(
            "config_hash",
            sa.String(length=64),
            nullable=True,
            comment="config_json 规范化序列化的 sha256 哈希，用于前后版本比对",
        ),
    )
    op.add_column(
        "backtest_run",
        sa.Column(
            "data_cutoff_date",
            sa.Date(),
            nullable=True,
            comment="回测执行时行情数据截止日期，用于评估数据口径",
        ),
    )
    op.add_column(
        "backtest_run",
        sa.Column(
            "optimization_id",
            sa.String(length=64),
            nullable=True,
            comment="关联的策略优化会话 ID（由优化 CLI 写入），普通回测为 NULL",
        ),
    )
    op.create_table(
        "strategy_optimization",
        sa.Column("optimization_id", sa.String(length=64), primary_key=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("baseline_version", sa.String(length=32), nullable=False),
        sa.Column("baseline_config_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_strategy_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_version", sa.String(length=32), nullable=False),
        sa.Column("candidate_config_hash", sa.String(length=64), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="running",
        ),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("folds", JSONB(), nullable=True),
        sa.Column("baseline_backtest_id", sa.String(length=64), nullable=True),
        sa.Column("candidate_backtest_id", sa.String(length=64), nullable=True),
        sa.Column("fold_backtests", JSONB(), nullable=True),
        sa.Column("metrics_full", JSONB(), nullable=True),
        sa.Column("metrics_folds", JSONB(), nullable=True),
        sa.Column("fold_summary", JSONB(), nullable=True),
        sa.Column("report", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """删除优化会话表与回测快照列。"""
    op.drop_table("strategy_optimization")
    op.drop_column("backtest_run", "optimization_id")
    op.drop_column("backtest_run", "data_cutoff_date")
    op.drop_column("backtest_run", "config_hash")
    op.drop_column("backtest_run", "config_snapshot")
