"""新增稳健性验证批次表与策略生命周期监控表，并给回测增加用途标记。

稳健性验证（robustness_run）承载候选集级别的过拟合风险检验并充当试验次数
台账；策略生命周期（strategy_lifecycle / strategy_health_snapshot）只覆盖
人工标记上线之后的监控与诊断；backtest_run 增加 purpose / purpose_reason，
用于把 2016-2025 研究期与 2026 起的验证期固化为可留痕的系统约束。

Revision ID: 0044_robustness_and_lifecycle
Revises: 0043_drop_industry_factor_value
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0044_robustness_and_lifecycle"
down_revision = "0043_drop_industry_factor_value"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建稳健性与生命周期相关表，并扩展回测用途字段。"""
    op.add_column(
        "backtest_run",
        sa.Column(
            "purpose",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'research'"),
            comment="回测用途：research/validation/monitor",
        ),
    )
    op.add_column(
        "backtest_run",
        sa.Column(
            "purpose_reason",
            sa.Text(),
            nullable=True,
            comment="用途说明与触发来源，用于验证期留痕",
        ),
    )
    op.create_table(
        "robustness_run",
        sa.Column("robustness_id", sa.String(length=64), primary_key=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=32), nullable=False, server_default=""),
        sa.Column(
            "baseline_config_hash", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("windows", JSONB, nullable=True),
        sa.Column("variants", JSONB, nullable=True),
        sa.Column("summary", JSONB, nullable=True),
        sa.Column("statistics", JSONB, nullable=True),
        sa.Column("trial_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_robustness_run_strategy", "robustness_run", ["strategy_id"])
    op.create_table(
        "strategy_lifecycle",
        sa.Column("strategy_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "lifecycle_status", sa.String(length=16), nullable=False, server_default="LIVE"
        ),
        sa.Column("live_at", sa.Date(), nullable=False),
        sa.Column("retired_at", sa.Date(), nullable=True),
        sa.Column(
            "frozen_config_hash", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("frozen_config_snapshot", JSONB, nullable=True),
        sa.Column("research_backtest_id", sa.String(length=64), nullable=True),
        sa.Column("validation_backtest_id", sa.String(length=64), nullable=True),
        sa.Column("baseline_distribution", JSONB, nullable=True),
        sa.Column("latest_health_level", sa.String(length=16), nullable=True),
        sa.Column("latest_diagnosis", sa.String(length=32), nullable=True),
        sa.Column("latest_recommended_action", sa.String(length=16), nullable=True),
        sa.Column("last_refreshed_at", sa.DateTime(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "strategy_health_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_id", sa.String(length=64), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("live_start", sa.Date(), nullable=False),
        sa.Column("live_end", sa.Date(), nullable=False),
        sa.Column("metrics", JSONB, nullable=True),
        sa.Column("health_level", sa.String(length=16), nullable=False),
        sa.Column("diagnosis", sa.String(length=32), nullable=False),
        sa.Column("recommended_action", sa.String(length=16), nullable=False),
        sa.Column("reasons", JSONB, nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("computed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_strategy_health_snapshot_strategy", "strategy_health_snapshot", ["strategy_id"]
    )


def downgrade() -> None:
    """回滚稳健性与生命周期相关表及回测用途字段。"""
    op.drop_index("ix_strategy_health_snapshot_strategy", table_name="strategy_health_snapshot")
    op.drop_table("strategy_health_snapshot")
    op.drop_table("strategy_lifecycle")
    op.drop_index("ix_robustness_run_strategy", table_name="robustness_run")
    op.drop_table("robustness_run")
    op.drop_column("backtest_run", "purpose_reason")
    op.drop_column("backtest_run", "purpose")
