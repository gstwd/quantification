"""回测模式支持：添加 backtest_mode 和配置模式字段

为 backtest_run 表添加 backtest_mode 列，
为 backtest_daily_result 表添加 timing_regime、total_exposure、cash_ratio、positions 列。

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # backtest_run 添加 backtest_mode 列
    op.add_column(
        "backtest_run",
        sa.Column(
            "backtest_mode",
            sa.String(32),
            nullable=False,
            server_default="signal",
            comment="回测模式：signal=信号评分，allocation=资产配置",
        ),
    )

    # backtest_daily_result 添加配置模式字段
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "timing_regime",
            sa.String(32),
            nullable=True,
            comment="择时状态：offensive/neutral/defensive（配置模式）",
        ),
    )
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "total_exposure",
            sa.Float,
            nullable=True,
            comment="总仓位比例，0-1（配置模式）",
        ),
    )
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "cash_ratio",
            sa.Float,
            nullable=True,
            comment="现金比例，0-1（配置模式）",
        ),
    )
    op.add_column(
        "backtest_daily_result",
        sa.Column(
            "positions",
            sa.JSON,
            nullable=True,
            comment="持仓明细，etf_code → 权重（配置模式）",
        ),
    )


def downgrade() -> None:
    op.drop_column("backtest_daily_result", "positions")
    op.drop_column("backtest_daily_result", "cash_ratio")
    op.drop_column("backtest_daily_result", "total_exposure")
    op.drop_column("backtest_daily_result", "timing_regime")
    op.drop_column("backtest_run", "backtest_mode")
