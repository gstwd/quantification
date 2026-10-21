"""研究战役桌面编排模式：research_campaign_step 新增 orchestrator_session_id。

Revision ID: 0063_campaign_desktop_orchestration
Revises: 0062_campaign_diagnostics
Create Date: 2026-10-21

桌面编排模式下，每一步由 Claude Code 桌面会话（编排者）新开一个桌面会话执行，
子会话的进程环境无法注入 ``QUANT_CAMPAIGN_ID``。改为按数据库判定：有桌面模式的
运行中步骤时，凡会话 ID 不等于编排会话的 CLI 调用都进入战役模式。
存量步骤为 headless 驱动器模式，该列为空。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0063_campaign_desktop_orchestration"
down_revision = "0062_campaign_diagnostics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增编排会话 ID 列（可空，无数据迁移）。"""
    op.add_column(
        "research_campaign_step",
        sa.Column(
            "orchestrator_session_id",
            sa.String(length=64),
            nullable=True,
            comment="桌面编排模式下编排会话的 CLAUDE_CODE_SESSION_ID：步骤运行期间只有该会话的"
            " CLI 调用不受战役模式约束；headless 驱动器模式为空",
        ),
    )


def downgrade() -> None:
    """删除编排会话 ID 列。"""
    op.drop_column("research_campaign_step", "orchestrator_session_id")
