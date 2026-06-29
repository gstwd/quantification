"""add market_synthesis table

Revision ID: 0021_market_synthesis
Revises: 91d10638a7b6
Create Date: 2026-06-29 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON

revision = "0021_market_synthesis"
down_revision = "91d10638a7b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 market_synthesis 表，存储每日 AI 市场综合研判。"""
    op.create_table(
        "market_synthesis",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "trade_date",
            sa.Date(),
            unique=True,
            nullable=False,
            comment="交易日，每天一条综合研判",
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            comment="200-300 字中文市场研判正文",
        ),
        sa.Column(
            "sentiment_summary",
            JSON,
            nullable=False,
            server_default=sa.text("'{}'::json"),
            comment="关键指数情绪摘要，如 {\"000300\": {\"avg\": 0.3, \"weighted\": 0.25}}",
        ),
        sa.Column(
            "key_topics",
            JSON,
            nullable=False,
            server_default=sa.text("'[]'::json"),
            comment="Top 5-8 市场主题",
        ),
        sa.Column(
            "risk_notes",
            sa.Text(),
            nullable=True,
            comment="风险提示，来自 AI 分析",
        ),
        sa.Column(
            "llm_model",
            sa.String(128),
            nullable=False,
            default="",
            comment="使用的 LLM 模型标识",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_market_synthesis_trade_date",
        "market_synthesis",
        ["trade_date"],
    )


def downgrade() -> None:
    """删除 market_synthesis 表。"""
    op.drop_index("ix_market_synthesis_trade_date", table_name="market_synthesis")
    op.drop_table("market_synthesis")
