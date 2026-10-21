"""研究试验的策略可为空：派生因子诊断记录不一定挂策略。

Revision ID: 0061_trial_strategy_nullable
Revises: 0060_research_campaign
Create Date: 2026-10-19

派生因子（expr 模板）的诊断记录（kind=diagnostic）以指纹标识，诊断时可以只给
表达式与别名声明、不挂任何策略；其余试验仍由服务层要求归属策略。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0061_trial_strategy_nullable"
down_revision = "0060_research_campaign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """research_trial.strategy_id 改为可空。"""
    op.alter_column(
        "research_trial",
        "strategy_id",
        existing_type=sa.String(length=64),
        nullable=True,
        comment="试验所针对的策略 ID（N 按此归属）；派生因子诊断记录可为空",
    )


def downgrade() -> None:
    """恢复非空（先删除不挂策略的诊断记录）。"""
    op.execute("DELETE FROM research_trial WHERE strategy_id IS NULL")
    op.alter_column(
        "research_trial",
        "strategy_id",
        existing_type=sa.String(length=64),
        nullable=False,
        comment="试验所针对的策略 ID（N 按此归属）",
    )
