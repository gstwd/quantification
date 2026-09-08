"""移除策略信号模式残留字段并补全组合配置。

回测每日 HIGH/MID/LOW 信号数量未参与任何策略决策或前端展示，
移除对应持久化列。历史策略中缺失或为 null 的 portfolio 统一补为
默认等权、50% 总仓位的组合配置，使其继续能被新版引擎执行。

Revision ID: 0034_remove_signal_mode_residue
Revises: 0033_index_member_and_generic_factor_params
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_remove_signal_mode_residue"
down_revision = "0033_index_member_and_generic_factor_params"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """移除无消费方的每日信号计数，并补全历史组合配置。"""
    op.execute(
        "UPDATE strategy_config "
        "SET config_json = jsonb_set("
        "config_json::jsonb, "
        "'{portfolio}', "
        "'{\"method\": \"equal_weight\", \"default_exposure\": 0.5}'::jsonb, "
        "true"
        ")::json "
        "WHERE config_json::jsonb -> 'portfolio' IS NULL "
        "OR config_json::jsonb -> 'portfolio' = 'null'::jsonb"
    )
    op.drop_column("backtest_daily_result", "low_signal_count")
    op.drop_column("backtest_daily_result", "mid_signal_count")
    op.drop_column("backtest_daily_result", "high_signal_count")


def downgrade() -> None:
    """恢复每日信号计数列，历史数据以零值回填。"""
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "high_signal_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="当日 HIGH 信号指数数量",
        ),
    )
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "mid_signal_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="当日 MID 信号指数数量",
        ),
    )
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "low_signal_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="当日 LOW 信号指数数量",
        ),
    )
