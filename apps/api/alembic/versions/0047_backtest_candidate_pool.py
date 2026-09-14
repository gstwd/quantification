"""回测有效候选池时间线：backtest_run.candidate_pool（C6）。

回测此前只在 warnings 里给一条"数据缺口剔除 N 个指数"的汇总提示，看不出
"早期实际可交易池只有 13~16 个"。本迁移新增 JSONB 列存放逐日候选池规模
（游程编码）与"因数据缺失被剔除的指数—日期区间"。

Revision ID: 0047_backtest_candidate_pool
Revises: 0046_queue_observability
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_backtest_candidate_pool"
down_revision = "0046_queue_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 candidate_pool 列。"""
    op.add_column(
        "backtest_run",
        sa.Column(
            "candidate_pool",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="逐日有效候选池时间线（游程编码）与数据缺口剔除区间（C6），"
            "由回测成功收尾时写入",
        ),
    )


def downgrade() -> None:
    """回滚 candidate_pool 列。"""
    op.drop_column("backtest_run", "candidate_pool")
