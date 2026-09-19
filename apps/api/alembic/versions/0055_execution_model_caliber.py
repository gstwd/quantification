"""优化会话与稳健性批次固化执行模型，避免口径分叉。

Revision ID: 0055_execution_model_caliber
Revises: 0054_backtest_index_selection_rebalanced
Create Date: 2026-09-19

执行模型（``t_plus_1_open`` / ``t_plus_1_close``）此前只是回测请求体上的参数：
优化会话与稳健性批次在派生子回测时不带该参数，"T+1 收盘口径的优化"无从表达，
只能退化为默认的 T+1 开盘口径。更关键的是，**开盘与收盘口径的逐日收益归属不同**
（收盘口径下 T 日信号在 T+1 收盘成交，首日收益为 0），两者的指标不可相互比较。

本迁移把执行模型固化到两处：

- ``strategy_optimization.execution_model``：会话创建时写入，``evaluate``/``finish``
  一律复用会话值。此前该值只能靠"记得在每条命令上重复传参"保证一致，
  异步评估（``evaluate --async`` 入队、之后另起进程收口）时极易分叉。
- ``robustness_run.execution_model``：批次创建时写入，全部变体回测共用，
  验收清单的"参数邻域"项据此要求邻域证据与本次会话同一执行口径。

历史行按 ``t_plus_1_open`` 回填（server_default）：这正是旧代码派生子回测时唯一
可能的取值，回填不会改变既有证据的含义。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0055_execution_model_caliber"
down_revision = "0054_backtest_index_selection_rebalanced"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为优化会话与稳健性批次表增加 execution_model 列。"""
    op.add_column(
        "strategy_optimization",
        sa.Column(
            "execution_model",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'t_plus_1_open'"),
            comment="本次会话的回测执行模型（t_plus_1_open / t_plus_1_close），evaluate 复用",
        ),
    )
    op.add_column(
        "robustness_run",
        sa.Column(
            "execution_model",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'t_plus_1_open'"),
            comment="本批次回测的执行模型，全部变体共用（口径不可与另一模型混比）",
        ),
    )


def downgrade() -> None:
    """删除 execution_model 列，回退到"执行模型只存在于回测 params"的旧口径。"""
    op.drop_column("robustness_run", "execution_model")
    op.drop_column("strategy_optimization", "execution_model")
