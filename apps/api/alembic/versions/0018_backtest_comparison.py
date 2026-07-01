"""策略对比回测功能 — 新增 backtest_comparison 表

Revision ID: 0018
Revises: 0016
Create Date: 2026-06-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0018"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_comparison",
        sa.Column(
            "comparison_id", sa.String(64), primary_key=True, comment="对比唯一 ID，UUID 格式"
        ),
        sa.Column("name", sa.String(128), nullable=True, comment="对比名称，用户可选标签"),
        sa.Column("strategy_a_id", sa.String(64), nullable=False, comment="策略 A 的 ID"),
        sa.Column("strategy_b_id", sa.String(64), nullable=False, comment="策略 B 的 ID"),
        sa.Column(
            "backtest_a_id",
            sa.String(64),
            sa.ForeignKey("backtest_run.backtest_id"),
            nullable=False,
            comment="策略 A 的子回测 ID",
        ),
        sa.Column(
            "backtest_b_id",
            sa.String(64),
            sa.ForeignKey("backtest_run.backtest_id"),
            nullable=False,
            comment="策略 B 的子回测 ID",
        ),
        sa.Column("start_date", sa.Date, nullable=False, comment="回测起始日期（两个策略共享）"),
        sa.Column("end_date", sa.Date, nullable=False, comment="回测结束日期（两个策略共享）"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="对比状态：pending/running/success/failed/partial",
        ),
        sa.Column("params", sa.JSON, nullable=True, comment="共享参数（基准配置、标的范围等）"),
        sa.Column(
            "comparison_metrics", sa.JSON, nullable=True, comment="对比级别汇总指标，完成后写入"
        ),
        sa.Column("error_message", sa.Text, nullable=True, comment="失败/部分失败时的错误信息"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
            comment="记录创建时间（UTC）",
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True, comment="开始执行时间（UTC）"
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), nullable=True, comment="完成时间（UTC）"
        ),
        sa.Column(
            "progress",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
            comment="对比整体进度 0-100",
        ),
    )
    op.create_index("ix_backtest_comparison_status", "backtest_comparison", ["status"])
    op.create_index("ix_backtest_comparison_created_at", "backtest_comparison", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_backtest_comparison_created_at")
    op.drop_index("ix_backtest_comparison_status")
    op.drop_table("backtest_comparison")
