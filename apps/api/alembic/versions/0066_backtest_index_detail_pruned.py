"""回测逐指数明细瘦身标记 backtest_run.index_detail_pruned_at。

Revision ID: 0066_backtest_index_detail_pruned
Revises: 0065_system_alert
Create Date: 2026-11-06

研究数据瘦身只删除 ``backtest_index_result`` 逐指数明细，汇总与逐日结果保留；
该列记录明细被删除的时间，读取归因 / 逐指数结果时据此明确返回"明细已清理"，
而不是把缺失的明细当成真实数据。可空列无默认值，无需回填。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0066_backtest_index_detail_pruned"
down_revision = "0065_system_alert"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 index_detail_pruned_at 列。"""
    op.add_column(
        "backtest_run",
        sa.Column(
            "index_detail_pruned_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="逐指数明细（backtest_index_result）被研究数据瘦身删除的时间（UTC），"
            "NULL 表示明细完整",
        ),
    )


def downgrade() -> None:
    """删除 index_detail_pruned_at 列。"""
    op.drop_column("backtest_run", "index_detail_pruned_at")
