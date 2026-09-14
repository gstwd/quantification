"""研究工具链元数据：变体策略标记、验证期消费留痕、稳健性批次扫描参数（D 类）。

对应回测流程问题清单 D 类：

- D-4 稳健性变体策略的清理没有元数据支撑：``strategy_config`` 增加
  ``source_batch_id``（派生批次）与 ``is_variant``，让"一次 scan 产生的
  ``<base>__rbXXXX_*`` 草稿"可以被 ``strategy prune-variants --batch`` 精确清理，
  不必再按 ID 前缀猜；
- D-5 验证期留痕只覆盖回测、不覆盖"研究者的眼睛"：``strategy_config`` 增加
  ``validation_consumed_at`` / ``validation_consumed_note``，一旦看过验证期结果
  即标记"该策略的验证期已被消费"，此后这段数据只能用于否决；
- D-2 ``robustness scan`` 的规模与口径需要可审计：``robustness_run`` 增加
  ``scan_params``，记录本次使用的预设、关键旋钮清单与窗口数。

存量回填（均按既有事实推导，不引入新语义）：

1. 变体标记：``description`` 中带"稳健性验证批次 <32位批次号>"的草稿策略，
   回填 ``source_batch_id`` 与 ``is_variant=true``；
2. 验证期消费：2026-01-01 起存在回测记录的策略，按最早一条回测时间回填
   ``validation_consumed_at``。

Revision ID: 0049_research_tooling
Revises: 0048_backtest_reference_integrity
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049_research_tooling"
down_revision = "0048_backtest_reference_integrity"
branch_labels = None
depends_on = None

# 验证期起始日（与 Settings.validation_period_start 一致，用于存量回填）
_VALIDATION_START = "2026-01-01"

# 从 description 中回填批次号：仅匹配"稳健性验证批次 <32 位十六进制>"
_BACKFILL_VARIANTS = """
UPDATE strategy_config
SET source_batch_id = substring(description from '稳健性验证批次 ([0-9a-f]{32})'),
    is_variant = true
WHERE description ~ '稳健性验证批次 [0-9a-f]{32}'
"""

# 回填验证期消费：存在越过研究期末端的回测即视为验证期已被消费
_BACKFILL_VALIDATION_CONSUMED = f"""
UPDATE strategy_config s
SET validation_consumed_at = b.first_used_at,
    validation_consumed_note = '迁移 0049 回填：该策略已有使用验证期（{_VALIDATION_START} 起）数据的回测'
FROM (
    SELECT strategy_id, min(created_at) AS first_used_at
    FROM backtest_run
    WHERE end_date >= DATE '{_VALIDATION_START}'
    GROUP BY strategy_id
) b
WHERE s.strategy_id = b.strategy_id
  AND s.validation_consumed_at IS NULL
"""


def upgrade() -> None:
    """新增研究工具链所需的元数据列并回填存量事实。"""
    op.add_column(
        "strategy_config",
        sa.Column(
            "source_batch_id",
            sa.String(length=64),
            nullable=True,
            comment="派生来源批次 ID（稳健性验证批次），手工策略为 NULL（D-4）",
        ),
    )
    op.add_column(
        "strategy_config",
        sa.Column(
            "is_variant",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="是否为稳健性验证派生的变体草稿策略（可批量清理，D-4）",
        ),
    )
    op.add_column(
        "strategy_config",
        sa.Column(
            "validation_consumed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="验证期（2026-01-01 起）数据首次被消费的时间，NULL 表示尚未消费（D-5）",
        ),
    )
    op.add_column(
        "strategy_config",
        sa.Column(
            "validation_consumed_note",
            sa.Text(),
            nullable=True,
            comment="验证期消费说明（首次消费来源或人工备注，D-5）",
        ),
    )
    op.create_index(
        "ix_strategy_config_source_batch",
        "strategy_config",
        ["source_batch_id"],
    )

    op.add_column(
        "robustness_run",
        sa.Column(
            "scan_params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="本次扫描口径：预设（quick/standard）、关键旋钮清单、窗口数（D-2）",
        ),
    )

    op.execute(sa.text(_BACKFILL_VARIANTS))
    op.execute(sa.text(_BACKFILL_VALIDATION_CONSUMED))


def downgrade() -> None:
    """移除研究工具链元数据列（回填数据不恢复）。"""
    op.drop_column("robustness_run", "scan_params")
    op.drop_index("ix_strategy_config_source_batch", table_name="strategy_config")
    op.drop_column("strategy_config", "validation_consumed_note")
    op.drop_column("strategy_config", "validation_consumed_at")
    op.drop_column("strategy_config", "is_variant")
    op.drop_column("strategy_config", "source_batch_id")
