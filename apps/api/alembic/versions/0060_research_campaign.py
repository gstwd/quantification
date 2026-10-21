"""新增研究战役：research_campaign、research_campaign_step，假设关联战役。

Revision ID: 0060_research_campaign
Revises: 0059_strategy_lineage
Create Date: 2026-10-19

研究战役是自动研究循环的状态：目标、范围、限额、停止原因与逐步记录。
只新增两张表与 ``research_hypothesis.campaign_id`` 一列，不改动既有数据。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0060_research_campaign"
down_revision = "0059_strategy_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建战役与步骤表，并为假设表增加 campaign_id。"""
    op.create_table(
        "research_campaign",
        sa.Column(
            "campaign_id",
            sa.String(length=64),
            nullable=False,
            comment="战役唯一 ID（c + 12 位十六进制）",
        ),
        sa.Column("objective", sa.Text(), nullable=False, comment="研究目标"),
        sa.Column(
            "strategy_id",
            sa.String(length=64),
            nullable=False,
            comment="种子基线策略 ID（战役只能改动其研究线内的策略）",
        ),
        sa.Column(
            "lineage_id",
            sa.String(length=64),
            nullable=False,
            comment="种子策略所在研究线 ID（N、预算按此汇总）",
        ),
        sa.Column(
            "index_codes", postgresql.JSONB(), nullable=True, comment="研究范围涉及的指数代码"
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'running'"),
            nullable=False,
            comment="状态：running=进行中，stopped=触发停止条件或人工停止，completed=研究员判定无新方向",
        ),
        sa.Column(
            "max_trials",
            sa.Integer(),
            nullable=False,
            comment="本战役最多新增的试验次数（研究线 N 的增量）",
        ),
        sa.Column("max_hours", sa.Float(), nullable=False, comment="墙钟时长上限（小时）"),
        sa.Column("max_rounds", sa.Integer(), nullable=False, comment="研究员轮数上限"),
        sa.Column(
            "max_consecutive_rejects", sa.Integer(), nullable=False, comment="连续否决的假设数上限"
        ),
        sa.Column(
            "max_step_usd",
            sa.Float(),
            nullable=True,
            comment="单步 agent 会话的花费上限（美元，传给 claude --max-budget-usd）",
        ),
        sa.Column("model", sa.String(length=64), nullable=True, comment="agent 使用的模型（可选）"),
        sa.Column(
            "trials_at_start", sa.Integer(), nullable=False, comment="开始时研究线已用试验次数"
        ),
        sa.Column("stop_reason", sa.Text(), nullable=True, comment="停止原因"),
        sa.Column("created_by", sa.String(length=64), nullable=True, comment="发起者标识"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="创建时间（UTC，timestamptz）",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="最后更新时间（UTC，timestamptz）",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="结束时间（UTC，timestamptz），进行中为 NULL",
        ),
        sa.PrimaryKeyConstraint("campaign_id"),
    )
    op.create_index("ix_research_campaign_status", "research_campaign", ["status"])

    op.create_table(
        "research_campaign_step",
        sa.Column("step_id", sa.String(length=64), nullable=False, comment="步骤唯一 ID"),
        sa.Column("campaign_id", sa.String(length=64), nullable=False, comment="所属战役 ID"),
        sa.Column("round", sa.Integer(), nullable=False, comment="轮次（从 1 开始）"),
        sa.Column(
            "role", sa.String(length=16), nullable=False, comment="角色：researcher / reviewer"
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'running'"),
            nullable=False,
            comment="状态：running / done / failed",
        ),
        sa.Column(
            "agent_session_id",
            sa.String(length=64),
            nullable=True,
            comment="headless agent 会话 ID（可用 claude --resume 复查）",
        ),
        sa.Column(
            "hypothesis_id", sa.String(length=64), nullable=True, comment="本步涉及的假设 ID"
        ),
        sa.Column(
            "optimization_id",
            sa.String(length=64),
            nullable=True,
            comment="本步提交评审 / 被评审的优化会话 ID",
        ),
        sa.Column(
            "outcome",
            sa.String(length=32),
            nullable=True,
            comment="研究员：review_requested/rejected/blocked/no_idea；"
            "评审员：recommend_accept/reject/needs_more",
        ),
        sa.Column("summary", sa.Text(), nullable=True, comment="本步结论摘要（agent 回报）"),
        sa.Column("cost_usd", sa.Float(), nullable=True, comment="本步 agent 花费（美元）"),
        sa.Column("error", sa.Text(), nullable=True, comment="失败原因"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="开始时间（UTC，timestamptz）",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="结束时间（UTC，timestamptz）",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["research_campaign.campaign_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("step_id"),
    )
    op.create_index("ix_research_campaign_step_campaign", "research_campaign_step", ["campaign_id"])

    op.add_column(
        "research_hypothesis",
        sa.Column(
            "campaign_id",
            sa.String(length=64),
            nullable=True,
            comment="所属研究战役 ID（自动研究循环登记），人工登记为 NULL",
        ),
    )
    op.create_foreign_key(
        "fk_research_hypothesis_campaign",
        "research_hypothesis",
        "research_campaign",
        ["campaign_id"],
        ["campaign_id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """删除战役相关表与列（战役记录随之丢失）。"""
    op.drop_constraint("fk_research_hypothesis_campaign", "research_hypothesis", type_="foreignkey")
    op.drop_column("research_hypothesis", "campaign_id")
    op.drop_index("ix_research_campaign_step_campaign", table_name="research_campaign_step")
    op.drop_table("research_campaign_step")
    op.drop_index("ix_research_campaign_status", table_name="research_campaign")
    op.drop_table("research_campaign")
