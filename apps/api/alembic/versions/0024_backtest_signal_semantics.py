"""统一 backtest_index_result 信号口径（B5）

背景：
- signal_score 语义从"目标权重×100"改为"综合得分"，与实时 index_signal.signal_score 一致；
- 仓位权重单独存 target_weight 字段；
- original_score 原为综合得分，语义与新的 signal_score 完全重复，删除。

Revision ID: 0024_backtest_signal_semantics
Revises: 0023_background_job
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_backtest_signal_semantics"
down_revision = "0023_background_job"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 target_weight 并迁移旧数据后删除 original_score。"""
    op.add_column(
        "backtest_index_result",
        sa.Column(
            "target_weight",
            sa.Float,
            nullable=True,
            comment="信号目标仓位权重（0-1），与实时信号 payload.target_weight 同义",
        ),
    )
    # 旧数据回填：
    # 1. 旧 signal_score = round(target_weight * 100, 2)，据此反推 target_weight；
    # 2. original_score 为旧综合得分，回填到 signal_score（若缺失则保留原值）。
    op.execute("UPDATE backtest_index_result SET target_weight = signal_score / 100.0")
    op.execute(
        "UPDATE backtest_index_result SET signal_score = original_score "
        "WHERE original_score IS NOT NULL"
    )
    op.drop_column("backtest_index_result", "original_score")


def downgrade() -> None:
    """还原为旧口径：恢复 original_score，signal_score 语义还原为权重×100。"""
    op.add_column(
        "backtest_index_result",
        sa.Column(
            "original_score",
            sa.Float,
            nullable=True,
            comment="保留原始综合得分（配置模式下不会被权重值覆盖）",
        ),
    )
    op.execute("UPDATE backtest_index_result SET original_score = signal_score")
    op.execute(
        "UPDATE backtest_index_result SET signal_score = ROUND(target_weight * 100, 2) "
        "WHERE target_weight IS NOT NULL"
    )
    op.drop_column("backtest_index_result", "target_weight")
