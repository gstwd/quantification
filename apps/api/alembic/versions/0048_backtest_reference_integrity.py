"""回测引用完整性：外键 ON DELETE 语义 + 存量悬挂引用预清理（C5）。

用户清空 backtest_run 后，多处引用指向已不存在的回测：

- ``strategy_optimization.baseline_backtest_id / candidate_backtest_id``（41+41 行）；
- ``strategy_lifecycle.research_backtest_id / validation_backtest_id``（1+1 行）；
- ``backtest_comparison.backtest_a_id / b_id`` 与日/指数结果（当前为 0 行，仍需级联语义）。

本迁移先把这些**结构性**悬挂引用清理干净（否则新增外键会校验失败），再重建
外键：可空列用 ``ON DELETE SET NULL``，从属明细用 ``ON DELETE CASCADE``。

注意：``robustness_run.variants[*].backtest_ids`` 与
``strategy_optimization.fold_backtests[*].*`` 存放在 JSONB 中，无法加外键，
由 ``backtest orphans`` 审计与 ``backtest prune-dangling-refs`` 清理。

Revision ID: 0048_backtest_reference_integrity
Revises: 0047_backtest_candidate_pool
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0048_backtest_reference_integrity"
down_revision = "0047_backtest_candidate_pool"
branch_labels = None
depends_on = None

# 预清理：把指向已删除回测的可空引用置空（顺序固定，先置空再重建外键）
_NULL_DANGLING_STATEMENTS: tuple[str, ...] = (
    """
    UPDATE strategy_optimization o SET baseline_backtest_id = NULL
    WHERE o.baseline_backtest_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM backtest_run b WHERE b.backtest_id = o.baseline_backtest_id
      )
    """,
    """
    UPDATE strategy_optimization o SET candidate_backtest_id = NULL
    WHERE o.candidate_backtest_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM backtest_run b WHERE b.backtest_id = o.candidate_backtest_id
      )
    """,
    """
    UPDATE strategy_lifecycle l SET research_backtest_id = NULL
    WHERE l.research_backtest_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM backtest_run b WHERE b.backtest_id = l.research_backtest_id
      )
    """,
    """
    UPDATE strategy_lifecycle l SET validation_backtest_id = NULL
    WHERE l.validation_backtest_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM backtest_run b WHERE b.backtest_id = l.validation_backtest_id
      )
    """,
)

# 预清理：删除失去父回测的从属明细（不可空外键，只能删除）
_DELETE_ORPHAN_STATEMENTS: tuple[str, ...] = (
    """
    DELETE FROM backtest_daily_result d
    WHERE NOT EXISTS (
        SELECT 1 FROM backtest_run b WHERE b.backtest_id = d.backtest_id
    )
    """,
    """
    DELETE FROM backtest_index_result i
    WHERE NOT EXISTS (
        SELECT 1 FROM backtest_run b WHERE b.backtest_id = i.backtest_id
    )
    """,
    """
    DELETE FROM backtest_comparison c
    WHERE NOT EXISTS (
        SELECT 1 FROM backtest_run b WHERE b.backtest_id = c.backtest_a_id
    ) OR NOT EXISTS (
        SELECT 1 FROM backtest_run b WHERE b.backtest_id = c.backtest_b_id
    )
    """,
)

# (表, 列, 旧约束名, 新约束名, ondelete)
_CASCADE_FKEYS: tuple[tuple[str, str, str, str], ...] = (
    (
        "backtest_daily_result",
        "backtest_id",
        "backtest_daily_result_backtest_id_fkey",
        "fk_backtest_daily_result_backtest",
    ),
    (
        "backtest_index_result",
        "backtest_id",
        "backtest_index_result_backtest_id_fkey",
        "fk_backtest_index_result_backtest",
    ),
    (
        "backtest_comparison",
        "backtest_a_id",
        "backtest_comparison_backtest_a_id_fkey",
        "fk_backtest_comparison_backtest_a",
    ),
    (
        "backtest_comparison",
        "backtest_b_id",
        "backtest_comparison_backtest_b_id_fkey",
        "fk_backtest_comparison_backtest_b",
    ),
)

# (表, 列, 新约束名) —— 置空型外键
_SET_NULL_FKEYS: tuple[tuple[str, str, str], ...] = (
    ("strategy_optimization", "baseline_backtest_id", "fk_strategy_optimization_baseline_backtest"),
    (
        "strategy_optimization",
        "candidate_backtest_id",
        "fk_strategy_optimization_candidate_backtest",
    ),
    ("strategy_lifecycle", "research_backtest_id", "fk_strategy_lifecycle_research_backtest"),
    ("strategy_lifecycle", "validation_backtest_id", "fk_strategy_lifecycle_validation_backtest"),
)


def upgrade() -> None:
    """预清理悬挂引用并重建外键的 ON DELETE 语义。"""
    for statement in _NULL_DANGLING_STATEMENTS:
        op.execute(sa.text(statement))
    for statement in _DELETE_ORPHAN_STATEMENTS:
        op.execute(sa.text(statement))

    for table, column, old_name, new_name in _CASCADE_FKEYS:
        op.drop_constraint(old_name, table, type_="foreignkey")
        op.create_foreign_key(
            new_name,
            table,
            "backtest_run",
            [column],
            ["backtest_id"],
            ondelete="CASCADE",
        )

    for table, column, name in _SET_NULL_FKEYS:
        op.create_foreign_key(
            name,
            table,
            "backtest_run",
            [column],
            ["backtest_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """恢复外键的 NO ACTION 语义（预清理删除/置空的数据不恢复）。"""
    for table, column, name in _SET_NULL_FKEYS:
        op.drop_constraint(name, table, type_="foreignkey")

    for table, column, old_name, new_name in _CASCADE_FKEYS:
        op.drop_constraint(new_name, table, type_="foreignkey")
        op.create_foreign_key(
            old_name,
            table,
            "backtest_run",
            [column],
            ["backtest_id"],
        )
