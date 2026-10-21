"""策略研究线：strategy_config 新增 lineage_id。

Revision ID: 0059_strategy_lineage
Revises: 0058_research_ledger
Create Date: 2026-10-19

研究线把基线策略与由它派生的优化候选、稳健性变体（以及手工归入的策略）
归为一组，试验次数 N、试验预算与验证期消费按研究线汇总。

数据迁移：存量策略先取自身 ID 为研究线；再让优化候选（strategy_optimization
的 candidate_strategy_id）与稳健性变体（source_batch_id → robustness_run）
继承基线的研究线。候选可能再派生候选，因此继承步骤重复执行至稳定（上限 10 轮）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_strategy_lineage"
down_revision = "0058_research_ledger"
branch_labels = None
depends_on = None

_INHERIT_FROM_OPTIMIZATION = """
UPDATE strategy_config AS s
SET lineage_id = b.lineage_id
FROM strategy_optimization AS o
JOIN strategy_config AS b ON b.strategy_id = o.strategy_id
WHERE s.strategy_id = o.candidate_strategy_id
  AND s.strategy_id <> b.strategy_id
  AND s.lineage_id IS DISTINCT FROM b.lineage_id
"""

_INHERIT_FROM_ROBUSTNESS = """
UPDATE strategy_config AS s
SET lineage_id = b.lineage_id
FROM robustness_run AS r
JOIN strategy_config AS b ON b.strategy_id = r.strategy_id
WHERE s.source_batch_id = r.robustness_id
  AND s.strategy_id <> b.strategy_id
  AND s.lineage_id IS DISTINCT FROM b.lineage_id
"""


def upgrade() -> None:
    """新增 lineage_id 列与索引，并按既有派生关系回填。"""
    op.add_column(
        "strategy_config",
        sa.Column(
            "lineage_id",
            sa.String(length=64),
            nullable=True,
            comment="研究线 ID（研究线根策略 ID）：优化候选与稳健性变体继承基线的研究线，"
            "试验次数 N、试验预算与验证期消费按研究线汇总；NULL 视同自身 strategy_id",
        ),
    )
    op.create_index("ix_strategy_config_lineage", "strategy_config", ["lineage_id"])
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE strategy_config SET lineage_id = strategy_id"))
    for _ in range(10):
        changed = bind.execute(sa.text(_INHERIT_FROM_OPTIMIZATION)).rowcount
        changed += bind.execute(sa.text(_INHERIT_FROM_ROBUSTNESS)).rowcount
        if not changed:
            break


def downgrade() -> None:
    """删除 lineage_id 列与索引。"""
    op.drop_index("ix_strategy_config_lineage", table_name="strategy_config")
    op.drop_column("strategy_config", "lineage_id")
