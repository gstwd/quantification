"""新增研究台账：研究假设表与研究试验表。

Revision ID: 0058_research_ledger
Revises: 0057_remove_unused_legacy_tables
Create Date: 2026-10-19

只新增两张表，不改动既有表。``research_hypothesis`` 记录假设、事先写定的
判定标准与结论；``research_trial`` 记录每次检验动作及其试验次数，研究批量
评估与人工补录的试验计入 Deflated Sharpe 的试验次数 N。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0058_research_ledger"
down_revision = "0057_remove_unused_legacy_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建研究假设表与研究试验表。"""
    op.create_table(
        "research_hypothesis",
        sa.Column(
            "hypothesis_id",
            sa.String(length=64),
            nullable=False,
            comment="假设唯一 ID（h + 12 位十六进制）",
        ),
        sa.Column(
            "strategy_id",
            sa.String(length=64),
            nullable=True,
            comment="关联策略 ID；指数级或跨策略假设可为空",
        ),
        sa.Column(
            "index_codes",
            postgresql.JSONB(),
            nullable=True,
            comment="假设涉及的指数代码列表，用于跨策略检索",
        ),
        sa.Column(
            "tags", postgresql.JSONB(), nullable=True, comment="自由标签列表，如 pv_resonance"
        ),
        sa.Column(
            "category",
            sa.String(length=32),
            nullable=False,
            comment="改动类别：timing/score/filter/rank/portfolio/risk/rebalance/factor/universe/other",
        ),
        sa.Column("statement", sa.Text(), nullable=False, comment="假设陈述"),
        sa.Column("rationale", sa.Text(), nullable=True, comment="提出假设的依据（诊断证据等）"),
        sa.Column(
            "pre_registration",
            postgresql.JSONB(),
            nullable=True,
            comment="检验前写定的判定标准 {criteria, execution_models}，检验后不得改写",
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'open'"),
            nullable=False,
            comment="状态：open/testing/accepted/rejected/inconclusive/blocked_by_schema",
        ),
        sa.Column("conclusion", sa.Text(), nullable=True, comment="结论与原因（关闭时必填）"),
        sa.Column(
            "parent_id",
            sa.String(length=64),
            nullable=True,
            comment="父假设 ID（假设树），父假设删除时置空",
        ),
        sa.Column(
            "created_by",
            sa.String(length=64),
            nullable=True,
            comment="登记者标识，如 claude / codex / human",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="登记时间（UTC，timestamptz）",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="最后更新时间（UTC，timestamptz）",
        ),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="关闭时间（UTC，timestamptz），未关闭为 NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["research_hypothesis.hypothesis_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("hypothesis_id"),
    )
    op.create_index("ix_research_hypothesis_strategy", "research_hypothesis", ["strategy_id"])
    op.create_index("ix_research_hypothesis_status", "research_hypothesis", ["status"])

    op.create_table(
        "research_trial",
        sa.Column("trial_id", sa.String(length=64), nullable=False, comment="试验唯一 ID"),
        sa.Column(
            "hypothesis_id",
            sa.String(length=64),
            nullable=True,
            comment="所属假设 ID；未挂假设的探索为 NULL（仍计入 N）",
        ),
        sa.Column(
            "strategy_id",
            sa.String(length=64),
            nullable=False,
            comment="试验所针对的策略 ID（N 按此归属）",
        ),
        sa.Column(
            "kind",
            sa.String(length=32),
            nullable=False,
            comment="来源：research_batch/manual 计入 N；optimization/robustness 仅为关联",
        ),
        sa.Column(
            "ref_id",
            sa.String(length=64),
            nullable=True,
            comment="关联的优化会话 ID 或稳健性批次 ID",
        ),
        sa.Column(
            "execution_model",
            sa.String(length=32),
            nullable=True,
            comment="试验所用执行模型（t_plus_1_open / t_plus_1_close）",
        ),
        sa.Column("start_date", sa.Date(), nullable=True, comment="评估区间起始日期（含）"),
        sa.Column("end_date", sa.Date(), nullable=True, comment="评估区间截止日期（含）"),
        sa.Column(
            "trial_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="本次检验的独立变体数（试验次数）",
        ),
        sa.Column(
            "is_estimated",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
            comment="是否为历史补录的估算值（非运行时自动记录）",
        ),
        sa.Column(
            "config_hashes", postgresql.JSONB(), nullable=True, comment="被检验的变体配置哈希列表"
        ),
        sa.Column("summary", postgresql.JSONB(), nullable=True, comment="关键指标摘要"),
        sa.Column("note", sa.Text(), nullable=True, comment="备注（补录依据、人工说明等）"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="记录时间（UTC，timestamptz）",
        ),
        sa.ForeignKeyConstraint(
            ["hypothesis_id"], ["research_hypothesis.hypothesis_id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("trial_id"),
    )
    op.create_index("ix_research_trial_strategy", "research_trial", ["strategy_id"])
    op.create_index("ix_research_trial_hypothesis", "research_trial", ["hypothesis_id"])


def downgrade() -> None:
    """删除研究台账两张表（台账数据随之丢失）。"""
    op.drop_index("ix_research_trial_hypothesis", table_name="research_trial")
    op.drop_index("ix_research_trial_strategy", table_name="research_trial")
    op.drop_table("research_trial")
    op.drop_index("ix_research_hypothesis_status", table_name="research_hypothesis")
    op.drop_index("ix_research_hypothesis_strategy", table_name="research_hypothesis")
    op.drop_table("research_hypothesis")
