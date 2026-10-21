"""研究战役诊断限次：research_campaign 新增 max_diagnostics / diagnostics_used。

Revision ID: 0062_campaign_diagnostics
Revises: 0061_trial_strategy_nullable
Create Date: 2026-10-21

诊断命令（factor-ic / buckets / signal-lab）只读且不计入 N，但 agent 可以先用诊断
筛几十个方向再只检验最好的一个。战役内按次数限额：放行时计数，超限拒绝。
存量战役取默认上限 30、已用 0。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0062_campaign_diagnostics"
down_revision = "0061_trial_strategy_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增诊断上限与已用次数两列（带默认值，无数据迁移）。"""
    op.add_column(
        "research_campaign",
        sa.Column(
            "max_diagnostics",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
            comment="战役内诊断命令（factor-ic / buckets / signal-lab）次数上限",
        ),
    )
    op.add_column(
        "research_campaign",
        sa.Column(
            "diagnostics_used",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="战役内已执行的诊断命令次数（放行时计数）",
        ),
    )


def downgrade() -> None:
    """删除两列。"""
    op.drop_column("research_campaign", "diagnostics_used")
    op.drop_column("research_campaign", "max_diagnostics")
