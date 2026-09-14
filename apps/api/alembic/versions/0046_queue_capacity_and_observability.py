"""队列容量与可观测性：任务心跳、批次取消/暂停、时间戳统一 timestamptz。

对应回测流程问题清单 B 类：
- B-2 队列优先级/取消/暂停：background_job 增加 batch_id 与 cancel_requested，
  status 语义扩展出 cancelled / paused；
- B-5 时间戳时区混用：background_job 与 backtest_run / backtest_comparison 的
  时间戳列统一改为 timestamptz（应用写 aware UTC，SQL 侧 now() 亦为带时区值）；
- B-7 卡死任务永久阻塞：background_job 增加 heartbeat_at，配合运行期僵尸任务扫描
  把心跳超时的 running 任务回收为 pending/failed。

存量 naive 时间戳按"应用写入的一直是 UTC"这一既有约定，用
`AT TIME ZONE 'UTC'` 解释后转为 timestamptz。

Revision ID: 0046_queue_observability
Revises: 0045_tushare_stock_data
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0046_queue_observability"
down_revision = "0045_tushare_stock_data"
branch_labels = None
depends_on = None


# (表名, 列名) 列表：统一从 naive timestamp 迁移到 timestamptz
_TIMESTAMP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("background_job", "created_at"),
    ("background_job", "started_at"),
    ("background_job", "finished_at"),
    ("backtest_run", "created_at"),
    ("backtest_run", "started_at"),
    ("backtest_run", "finished_at"),
    ("backtest_comparison", "created_at"),
    ("backtest_comparison", "started_at"),
    ("backtest_comparison", "finished_at"),
)


def upgrade() -> None:
    """扩展队列任务元数据并把相关时间戳列迁移为 timestamptz。"""
    op.add_column(
        "background_job",
        sa.Column(
            "batch_id",
            sa.String(length=64),
            nullable=True,
            comment="批次标识（如稳健性批次 robustness_id），用于整批取消/暂停",
        ),
    )
    op.add_column(
        "background_job",
        sa.Column(
            "cancel_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
            comment="是否已请求取消：运行中的任务由处理器在安全检查点协作退出",
        ),
    )
    op.add_column(
        "background_job",
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="最近一次心跳时间（UTC，timestamptz），worker 执行期周期更新",
        ),
    )
    op.create_index("ix_background_job_batch_id", "background_job", ["batch_id"])
    op.create_index("ix_background_job_status_created", "background_job", ["status", "created_at"])
    op.alter_column(
        "background_job",
        "status",
        existing_type=sa.String(length=32),
        existing_nullable=True,
        comment=(
            "任务状态：pending=待执行，running=执行中，success=成功，failed=失败，"
            "cancelled=已取消，paused=批次暂停"
        ),
    )

    # 存量 naive 时间戳按 UTC 解释后转为 timestamptz，避免会话时区参与换算
    for table, column in _TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(),
            existing_nullable=True,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    """回滚队列元数据列并把时间戳列恢复为 naive timestamp。"""
    for table, column in _TIMESTAMP_COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=True,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
    op.alter_column(
        "background_job",
        "status",
        existing_type=sa.String(length=32),
        existing_nullable=True,
        comment="任务状态：pending=待执行，running=执行中，success=成功，failed=失败",
    )
    op.drop_index("ix_background_job_status_created", table_name="background_job")
    op.drop_index("ix_background_job_batch_id", table_name="background_job")
    op.drop_column("background_job", "heartbeat_at")
    op.drop_column("background_job", "cancel_requested")
    op.drop_column("background_job", "batch_id")
