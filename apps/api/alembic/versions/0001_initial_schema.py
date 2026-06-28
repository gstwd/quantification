"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmark_index",
        sa.Column("index_code", sa.String(32), primary_key=True),
        sa.Column("name_cn", sa.String(128), nullable=False),
        sa.Column("exchange", sa.String(16), nullable=False, server_default="CN"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "etf_universe",
        sa.Column("etf_code", sa.String(16), primary_key=True),
        sa.Column("exchange", sa.String(8), nullable=False),
        sa.Column("name_cn", sa.String(128), nullable=False),
        sa.Column("fund_full_name", sa.String(256), nullable=True),
        sa.Column("tracking_index_code", sa.String(32), nullable=True),
        sa.Column("tracking_index_name", sa.String(128), nullable=False),
        sa.Column("fund_company", sa.String(128), nullable=True),
        sa.Column("listing_date", sa.Date, nullable=True),
        sa.Column("delisting_date", sa.Date, nullable=True),
        sa.Column("category", sa.String(64), nullable=False, server_default="broad_index"),
        sa.Column("is_a_share_etf", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("data_source", sa.String(32), nullable=False, server_default="seed"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "etf_daily_bar",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column("open_price", sa.Float, nullable=True),
        sa.Column("high_price", sa.Float, nullable=True),
        sa.Column("low_price", sa.Float, nullable=True),
        sa.Column("close_price", sa.Float, nullable=True),
        sa.Column("prev_close_price", sa.Float, nullable=True),
        sa.Column("change_pct", sa.Float, nullable=True),
        sa.Column("volume", sa.Float, nullable=True),
        sa.Column("turnover", sa.Float, nullable=True),
        sa.Column("amplitude", sa.Float, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="stub"),
        sa.Column("ingested_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("trade_date", "etf_code", name="uq_etf_daily_bar"),
    )

    op.create_table(
        "index_daily_bar",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "index_code", sa.String(32), sa.ForeignKey("benchmark_index.index_code"), nullable=False
        ),
        sa.Column("open_price", sa.Float, nullable=True),
        sa.Column("high_price", sa.Float, nullable=True),
        sa.Column("low_price", sa.Float, nullable=True),
        sa.Column("close_price", sa.Float, nullable=True),
        sa.Column("prev_close_price", sa.Float, nullable=True),
        sa.Column("change_pct", sa.Float, nullable=True),
        sa.Column("volume", sa.Float, nullable=True),
        sa.Column("turnover", sa.Float, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="stub"),
        sa.Column("ingested_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("trade_date", "index_code", name="uq_index_daily_bar"),
    )

    op.create_table(
        "etf_daily_share",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column("shares_total", sa.Float, nullable=True),
        sa.Column("shares_delta", sa.Float, nullable=True),
        sa.Column("shares_delta_pct", sa.Float, nullable=True),
        sa.Column("nav", sa.Float, nullable=True),
        sa.Column("aum", sa.Float, nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="stub"),
        sa.Column("ingested_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("trade_date", "etf_code", name="uq_etf_daily_share"),
    )

    op.create_table(
        "source_payload_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source_name", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_key", sa.String(64), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=True),
        sa.Column("request_meta", sa.JSON, nullable=True),
        sa.Column("response_payload", sa.JSON, nullable=True),
        sa.Column("fetched_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "factor_definition",
        sa.Column("factor_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0.0"),
        sa.Column("owner_plugin", sa.String(64), nullable=False),
    )

    op.create_table(
        "signal_definition",
        sa.Column("signal_id", sa.String(64), primary_key=True),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default="1.0.0"),
    )

    op.create_table(
        "strategy_plugin",
        sa.Column("strategy_id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("plugin_module", sa.String(256), nullable=False),
        sa.Column("plugin_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("default_params", sa.JSON, nullable=True),
        sa.Column("result_schema", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "research_run",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("run_type", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("trade_date", sa.Date, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("params", sa.JSON, nullable=True),
        sa.Column("metrics", sa.JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=False, server_default=sa.text("now()")),
        sa.Column("finished_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "etf_factor_value",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column(
            "factor_id", sa.String(64), sa.ForeignKey("factor_definition.factor_id"), nullable=False
        ),
        sa.Column("factor_value_numeric", sa.Float, nullable=True),
        sa.Column("factor_value_text", sa.String(128), nullable=True),
        sa.Column("factor_payload", sa.JSON, nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.UniqueConstraint(
            "trade_date", "etf_code", "factor_id", "strategy_id", name="uq_etf_factor_value"
        ),
    )

    op.create_table(
        "etf_signal",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column("strategy_id", sa.String(64), nullable=False),
        sa.Column("signal_score", sa.Float, nullable=False),
        sa.Column("signal_level", sa.String(32), nullable=False),
        sa.Column("signal_label", sa.String(128), nullable=False),
        sa.Column("signal_payload", sa.JSON, nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.UniqueConstraint("trade_date", "etf_code", "strategy_id", name="uq_etf_signal"),
    )

    op.create_table(
        "research_run_item",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("research_run.run_id"), nullable=False),
        sa.Column(
            "etf_code", sa.String(16), sa.ForeignKey("etf_universe.etf_code"), nullable=False
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("message", sa.Text, nullable=True),
        sa.Column("metrics", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("research_run_item")
    op.drop_table("etf_signal")
    op.drop_table("etf_factor_value")
    op.drop_table("research_run")
    op.drop_table("strategy_plugin")
    op.drop_table("signal_definition")
    op.drop_table("factor_definition")
    op.drop_table("source_payload_log")
    op.drop_table("etf_daily_share")
    op.drop_table("index_daily_bar")
    op.drop_table("etf_daily_bar")
    op.drop_table("etf_universe")
    op.drop_table("benchmark_index")
