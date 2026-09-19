"""backtest_index_result 增加 scored 标记，区分"未参与评分"与"得分为 0"。

Revision ID: 0053_backtest_index_scored
Revises: 0052_remove_industry_quality_snapshot_columns
Create Date: 2026-09-19

`backtest_index_result` 是按"当日 universe 全量标的"落库的，而引擎只对通过
候选池与过滤规则的资产产出得分。写库路径把两者混为一谈：未参与评分的资产
`signal_score` 落成 0.0。组合分数 IC 直接读这一列做横截面 Rank IC 时，这些
占位 0 会占据固定底部名次，既扭曲 IC 均值也压缩日间波动、虚高 t 值。

本迁移只新增 `scored` 列（PG 11+ 带常量默认值 ADD COLUMN 为元数据操作，
不重写表），**不回填历史行**：

1. 历史行无法可靠回填——`scored=False` 的判据是"未参与评分"，而不是
   "得分为 0"；zscore 评分模式的合法得分经 clamp 后可以恰好等于 0.0
   （见 `engine/score.py::CrossSectionScorer`），用 `signal_score <> 0`
   回填会误伤这类行。
2. 表规模已达千万行级（本机实测 1.09e7 行、其中 7.76e6 行为 0），全表
   UPDATE 的代价与其收益（只影响历史回测的 IC 复算）不相称。

因此迁移后历史行一律为 `scored=TRUE`，即沿用改造前口径。生命周期体检每次
都用新创建的监控回测取数，不受影响；如需对历史回测复算组合分数 IC，请重新
跑一次回测。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_backtest_index_scored"
down_revision = "0052_remove_industry_quality_snapshot_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """新增 scored 列（历史行按"已评分"处理，见模块说明）。"""
    op.add_column(
        "backtest_index_result",
        sa.Column(
            "scored",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
            comment="该资产当日是否真正参与评分（False=被候选池剔除或被过滤规则拒绝）",
        ),
    )


def downgrade() -> None:
    """删除 scored 列，回退到无法区分"未评分"与"得分为 0"的旧口径。"""
    op.drop_column("backtest_index_result", "scored")
