"""删除 industry_universe 中与统一健康快照重复的质量字段。

Revision ID: 0052_remove_industry_quality_snapshot_columns
Revises: 0051_data_health_upstream_missing_ranges
Create Date: 2026-09-16

行业日线质量此前存在两个存储：`industry_universe` 的 5 个质量列（由
`IndustryDataService` 自行计算并写入）与 `data_health_snapshot`（由
`DataManagementService` 在统一口径下计算）。本次把历史快照迁入统一表后
删除这 5 列，行业质量与其他数据集一样只保留一个口径。
"""

from __future__ import annotations

from alembic import op

revision = "0052_remove_industry_quality_snapshot_columns"
down_revision = "0051_data_health_upstream_missing_ranges"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """把旧质量列迁入统一健康快照后删除。"""
    # 迁移期间把旧表中仍有价值的快照转入统一表，避免历史检查结果丢失。
    op.execute(
        """
        INSERT INTO data_health_snapshot (
            dataset_key, partition_key, partition_name, health_status,
            earliest_date, latest_date, expected_date, record_count,
            missing_count, invalid_count, last_checked_at
        )
        SELECT
            'industry_daily_bar', industry_code, name_cn,
            CASE
                WHEN COALESCE(bar_count, 0) = 0 THEN 'error'
                WHEN COALESCE(missing_day_count, 0) > 0 THEN 'warning'
                ELSE 'healthy'
            END,
            data_start_date, data_end_date, data_end_date,
            COALESCE(bar_count, 0), COALESCE(missing_day_count, 0), 0,
            quality_checked_at
        FROM industry_universe
        WHERE data_end_date IS NOT NULL
        ON CONFLICT (dataset_key, partition_key) DO NOTHING
        """
    )
    for column in (
        "data_start_date",
        "data_end_date",
        "bar_count",
        "missing_day_count",
        "quality_checked_at",
    ):
        op.drop_column("industry_universe", column)


def downgrade() -> None:
    """恢复行业目录表中的历史质量快照字段。"""
    import sqlalchemy as sa

    op.add_column("industry_universe", sa.Column("data_start_date", sa.Date(), nullable=True))
    op.add_column("industry_universe", sa.Column("data_end_date", sa.Date(), nullable=True))
    op.add_column("industry_universe", sa.Column("bar_count", sa.Integer(), nullable=True))
    op.add_column("industry_universe", sa.Column("missing_day_count", sa.Integer(), nullable=True))
    op.add_column("industry_universe", sa.Column("quality_checked_at", sa.DateTime(), nullable=True))
