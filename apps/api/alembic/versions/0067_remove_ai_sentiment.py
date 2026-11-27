"""永久下线 AI 舆情分析层。

Revision ID: 0067_remove_ai_sentiment
Revises: 0066_backtest_index_detail_pruned
Create Date: 2026-11-28

删除舆情业务表及共享运行表中的 ``ai_analysis`` 记录。升级会永久删除新闻、
情绪分析、聚合、市场研判和关键词映射历史数据；降级只恢复空表结构。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_remove_ai_sentiment"
down_revision = "0066_backtest_index_detail_pruned"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """删除 AI 舆情表与共享运行记录。"""
    op.execute(
        sa.text(
            """
            DELETE FROM research_run_item
            WHERE run_id IN (
                SELECT run_id FROM research_run WHERE run_type = 'ai_analysis'
            )
            """
        )
    )
    op.execute(sa.text("DELETE FROM background_job WHERE job_type = 'ai_analysis'"))
    op.execute(sa.text("DELETE FROM research_run WHERE run_type = 'ai_analysis'"))

    op.drop_table("ai_sentiment_result")
    op.drop_table("daily_sentiment_aggregate")
    op.drop_table("market_synthesis")
    op.drop_table("keyword_tag_config")
    op.drop_table("news_item")


def downgrade() -> None:
    """恢复 AI 舆情空表结构；已删除的历史数据无法恢复。"""
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
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
            comment="关键指数情绪摘要",
        ),
        sa.Column(
            "key_topics",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
            comment="Top 5-8 市场主题",
        ),
        sa.Column("risk_notes", sa.Text(), nullable=True, comment="风险提示，来自 AI 分析"),
        sa.Column(
            "llm_model",
            sa.String(128),
            nullable=False,
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
    op.create_table(
        "keyword_tag_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("keyword", sa.String(128), unique=True, nullable=False, comment="匹配关键词"),
        sa.Column("tag", sa.String(64), nullable=False, comment="映射到的资产标签"),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
            comment="是否启用",
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="优先级（越大越先匹配）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
