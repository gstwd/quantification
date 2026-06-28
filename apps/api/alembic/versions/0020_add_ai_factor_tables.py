"""add ai factor tables: news_item, ai_sentiment_result, daily_sentiment_aggregate

Revision ID: 91d10638a7b6
Revises: c27fec08be21
Create Date: 2026-06-28 17:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "91d10638a7b6"
down_revision = "c27fec08be21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 AI 因子层 3 张新表。"""
    # 1. news_item — 原始新闻存储
    op.create_table(
        "news_item",
        sa.Column("id", sa.String(36), primary_key=True, comment="新闻唯一标识（UUID）"),
        sa.Column("source_id", sa.String(64), nullable=False, comment="来源平台 ID"),
        sa.Column("source_name", sa.String(128), nullable=True, comment="来源平台中文名"),
        sa.Column("title", sa.Text(), nullable=False, comment="新闻标题（已清洗）"),
        sa.Column("url", sa.Text(), nullable=True, comment="新闻链接（已规范化）"),
        sa.Column("rank", sa.Integer(), nullable=True, comment="热榜排名（1=榜首）"),
        sa.Column("crawl_date", sa.Date(), nullable=False, comment="采集日期"),
        sa.Column("first_seen_at", sa.DateTime(), nullable=True, comment="首次上榜时间"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True, comment="最后上榜时间"),
        sa.Column("appear_count", sa.Integer(), server_default=sa.text("1"), comment="出现次数"),
        sa.Column("raw_payload", sa.JSON(), nullable=True, comment="原始 API 返回数据"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() AT TIME ZONE 'utc')"),
            comment="记录创建时间（UTC）",
        ),
        sa.UniqueConstraint(
            "source_id",
            "title",
            "crawl_date",
            name="uq_news_source_title_date",
        ),
    )

    # 2. ai_sentiment_result — AI 分析结果
    op.create_table(
        "ai_sentiment_result",
        sa.Column("id", sa.String(36), primary_key=True, comment="分析结果唯一标识（UUID）"),
        sa.Column(
            "news_id",
            sa.String(36),
            sa.ForeignKey("news_item.id", ondelete="CASCADE"),
            nullable=False,
            comment="关联的原始新闻 ID",
        ),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="关联交易日"),
        sa.Column("asset_tags", sa.JSON(), nullable=True, comment="资产标签 JSON 数组"),
        sa.Column("topics", sa.JSON(), nullable=True, comment="主题标签 JSON 数组"),
        sa.Column("sentiment_score", sa.Float(), nullable=True, comment="情绪分 [-1.0, 1.0]"),
        sa.Column("attention_score", sa.Float(), nullable=True, comment="关注度分 [0, 100]"),
        sa.Column("relevance_score", sa.Float(), nullable=True, comment="A 股市场相关度 [0, 1]"),
        sa.Column("summary", sa.String(256), nullable=True, comment="AI 生成摘要"),
        sa.Column("llm_model", sa.String(128), nullable=True, comment="使用的 LLM 模型标识"),
        sa.Column("llm_response", sa.JSON(), nullable=True, comment="LLM 完整响应（调试用）"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() AT TIME ZONE 'utc')"),
            comment="记录创建时间（UTC）",
        ),
        sa.UniqueConstraint("news_id", name="uq_ai_sentiment_news"),
    )

    # 3. daily_sentiment_aggregate — 每日情绪聚合
    op.create_table(
        "daily_sentiment_aggregate",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, comment="自增主键"),
        sa.Column("trade_date", sa.Date(), nullable=False, comment="交易日"),
        sa.Column(
            "asset_tag", sa.String(64), nullable=False, comment="资产标签（指数代码或行业名）"
        ),
        sa.Column("avg_sentiment", sa.Float(), nullable=True, comment="算术平均情绪分"),
        sa.Column("weighted_sentiment", sa.Float(), nullable=True, comment="关注度加权情绪分"),
        sa.Column("total_attention", sa.Float(), nullable=True, comment="总关注度"),
        sa.Column("news_count", sa.Integer(), server_default=sa.text("0"), comment="相关新闻数量"),
        sa.Column("top_topics", sa.JSON(), nullable=True, comment="Top 主题 JSON 数组"),
        sa.Column("positive_ratio", sa.Float(), nullable=True, comment="正面新闻占比 [0, 1]"),
        sa.Column("negative_ratio", sa.Float(), nullable=True, comment="负面新闻占比 [0, 1]"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(now() AT TIME ZONE 'utc')"),
            comment="记录创建时间（UTC）",
        ),
        sa.UniqueConstraint(
            "trade_date",
            "asset_tag",
            name="uq_daily_sentiment_date_tag",
        ),
    )


def downgrade() -> None:
    """移除 AI 因子层 3 张表。"""
    op.drop_table("daily_sentiment_aggregate")
    op.drop_table("ai_sentiment_result")
    op.drop_table("news_item")
