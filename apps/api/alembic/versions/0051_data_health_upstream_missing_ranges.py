"""Persist confirmed upstream gaps for stock daily data health checks.

Revision ID: 0051_data_health_upstream_missing_ranges
Revises: 0050_status_query_indexes
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0051_data_health_upstream_missing_ranges"
down_revision = "0050_status_query_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the durable per-partition upstream-missing exemption ranges."""
    op.add_column(
        "data_health_snapshot",
        sa.Column(
            "upstream_missing_ranges",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="已由上游成功请求确认缺失的交易日区间",
        ),
    )


def downgrade() -> None:
    op.drop_column("data_health_snapshot", "upstream_missing_ranges")
