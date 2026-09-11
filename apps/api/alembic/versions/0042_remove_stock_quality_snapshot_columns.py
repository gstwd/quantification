"""删除 stock_universe 中与统一健康快照重复的质量字段。"""

from __future__ import annotations

from alembic import op

revision = "0042_remove_stock_quality_snapshot_columns"
down_revision = "0041_restrict_factor_value_shape"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除个股元数据表中的冗余质量快照字段。"""
    # 迁移期间把旧表中仍有价值的快照转入统一表，避免历史检查结果丢失。
    op.execute(
        """
        INSERT INTO data_health_snapshot (
            dataset_key, partition_key, partition_name, health_status,
            earliest_date, latest_date, expected_date, record_count,
            missing_count, invalid_count, last_checked_at
        )
        SELECT
            'stock_daily_close', stock_code, name_cn,
            CASE
                WHEN COALESCE(bar_count, 0) = 0 THEN 'error'
                WHEN COALESCE(missing_day_count, 0) > 0 THEN 'warning'
                ELSE 'healthy'
            END,
            data_start_date, data_end_date, data_end_date,
            COALESCE(bar_count, 0), COALESCE(missing_day_count, 0), 0,
            quality_checked_at
        FROM stock_universe
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
        op.drop_column("stock_universe", column)


def downgrade() -> None:
    """恢复个股元数据表中的历史质量快照字段。"""
    import sqlalchemy as sa

    op.add_column("stock_universe", sa.Column("data_start_date", sa.Date(), nullable=True))
    op.add_column("stock_universe", sa.Column("data_end_date", sa.Date(), nullable=True))
    op.add_column("stock_universe", sa.Column("bar_count", sa.Integer(), nullable=True))
    op.add_column("stock_universe", sa.Column("missing_day_count", sa.Integer(), nullable=True))
    op.add_column("stock_universe", sa.Column("quality_checked_at", sa.DateTime(), nullable=True))
