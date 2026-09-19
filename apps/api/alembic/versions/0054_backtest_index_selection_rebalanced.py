"""backtest_index_result 增加选股调仓标记，供组合分数 IC 定位真实决策日。

Revision ID: 0054_backtest_index_selection_rebalanced
Revises: 0053_backtest_index_scored
Create Date: 2026-09-19

组合分数 IC 衡量的是"策略的排序决策对不对"。周/双周/月频策略只在选股调仓日
重建成分，其余交易日沿用上次持仓——这些日子的 `signal_score` 仍是引擎每天算出
的潜在分数，但**没有被执行**，把它们当观测既稀释了决策含义，又因前瞻窗口重叠
而让标准误偏低。

`selection_rebalanced` 记录当日是否真的执行了选股腿调仓（风险腿只缩放总仓位、
不重建成分，不算决策日）。组合分数 IC 只使用 `TRUE` 的交易日。

历史行一律为 `TRUE`（旧结果未记录实际选股日期，无法可靠重建），即沿用改造前
口径；生命周期体检每次都用新创建的监控回测取数，不受影响。若需对历史回测复算
组合分数 IC，请重跑一次回测。

**迁移历史收敛说明**：本列一度被写进 0053（与 `scored` 同一个迁移，提交 5426744），
随后拆分为独立的 0054。若某个环境已经应用过那个"两列版 0053"，它的
`alembic_version` 仍是 0053，直接 upgrade 会因列已存在而失败——请改用
``alembic stamp 0054_backtest_index_selection_rebalanced`` 或
``alembic stamp 0054`` 对齐版本号，**不要**重跑本迁移。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0054_backtest_index_selection_rebalanced"
down_revision = "0053_backtest_index_scored"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 selection_rebalanced 列（历史行按"已执行选股调仓"处理，见模块说明）。"""
    op.add_column(
        "backtest_index_result",
        sa.Column(
            "selection_rebalanced",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
            comment="该交易日是否执行选股调仓；组合分数 IC 仅使用 True 的交易日",
        ),
    )


def downgrade() -> None:
    """删除 selection_rebalanced 列，回退到"每个交易日都算决策日"的旧口径。"""
    op.drop_column("backtest_index_result", "selection_rebalanced")
