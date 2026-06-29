"""add keyword_tag_config table

Revision ID: 0022_keyword_tag_config
Revises: 0021_market_synthesis
Create Date: 2026-06-29 11:00:00.000000
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0022_keyword_tag_config"
down_revision = "0021_market_synthesis"
branch_labels = None
depends_on = None

# 从 classifier.py 的 _KEYWORD_TAG_MAP 提取的种子数据
_SEED_DATA: list[dict] = [
    # 指数
    {"keyword": "沪深300", "tag": "000300", "priority": 10},
    {"keyword": "沪深 300", "tag": "000300", "priority": 10},
    {"keyword": "中证500", "tag": "000905", "priority": 10},
    {"keyword": "中证 500", "tag": "000905", "priority": 10},
    {"keyword": "上证50", "tag": "000016", "priority": 10},
    {"keyword": "上证 50", "tag": "000016", "priority": 10},
    {"keyword": "创业板", "tag": "399006", "priority": 10},
    {"keyword": "科创板", "tag": "000688", "priority": 10},
    {"keyword": "科创50", "tag": "000688", "priority": 10},
    {"keyword": "科创 50", "tag": "000688", "priority": 10},
    {"keyword": "中证1000", "tag": "000852", "priority": 10},
    {"keyword": "中证 1000", "tag": "000852", "priority": 10},
    # 行业 — 金融
    {"keyword": "银行", "tag": "金融", "priority": 5},
    {"keyword": "证券", "tag": "金融", "priority": 5},
    {"keyword": "保险", "tag": "金融", "priority": 5},
    {"keyword": "基金", "tag": "金融", "priority": 5},
    # 行业 — 科技/AI
    {"keyword": "AI", "tag": "人工智能", "priority": 8},
    {"keyword": "大模型", "tag": "人工智能", "priority": 8},
    {"keyword": "GPT", "tag": "人工智能", "priority": 8},
    # 行业 — 半导体
    {"keyword": "芯片", "tag": "半导体", "priority": 8},
    {"keyword": "集成电路", "tag": "半导体", "priority": 8},
    {"keyword": "光刻", "tag": "半导体", "priority": 8},
    # 行业 — 新能源
    {"keyword": "新能源车", "tag": "新能源", "priority": 8},
    {"keyword": "电动车", "tag": "新能源", "priority": 8},
    {"keyword": "光伏", "tag": "新能源", "priority": 8},
    {"keyword": "储能", "tag": "新能源", "priority": 8},
    {"keyword": "锂电池", "tag": "新能源", "priority": 8},
    {"keyword": "锂电", "tag": "新能源", "priority": 8},
    {"keyword": "固态电池", "tag": "新能源", "priority": 8},
    # 行业 — 医药
    {"keyword": "医药", "tag": "医药", "priority": 5},
    {"keyword": "创新药", "tag": "医药", "priority": 5},
    {"keyword": "医疗器械", "tag": "医药", "priority": 5},
    # 行业 — 地产
    {"keyword": "房地产", "tag": "地产", "priority": 5},
    {"keyword": "楼市", "tag": "地产", "priority": 5},
    # 行业 — 军工
    {"keyword": "军工", "tag": "军工", "priority": 5},
    {"keyword": "国防", "tag": "军工", "priority": 5},
    # 行业 — 消费
    {"keyword": "消费", "tag": "消费", "priority": 5},
    {"keyword": "零售", "tag": "消费", "priority": 5},
    {"keyword": "电商", "tag": "消费", "priority": 5},
    # 概念 — 机器人
    {"keyword": "机器人", "tag": "机器人", "priority": 5},
    {"keyword": "人形机器人", "tag": "人形机器人", "priority": 8},
    # 概念 — 自动驾驶
    {"keyword": "自动驾驶", "tag": "自动驾驶", "priority": 5},
    {"keyword": "智能驾驶", "tag": "自动驾驶", "priority": 5},
    # 概念 — 数字经济
    {"keyword": "数字经济", "tag": "数字经济", "priority": 5},
    {"keyword": "数据要素", "tag": "数字经济", "priority": 5},
    # 概念 — 央企改革
    {"keyword": "央企", "tag": "央企改革", "priority": 5},
    {"keyword": "国企改革", "tag": "央企改革", "priority": 5},
    # 概念 — 低空经济
    {"keyword": "低空经济", "tag": "低空经济", "priority": 8},
    {"keyword": "eVTOL", "tag": "低空经济", "priority": 5},
]


def upgrade() -> None:
    """创建 keyword_tag_config 表并写入种子数据。"""
    op.create_table(
        "keyword_tag_config",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "keyword",
            sa.String(128),
            unique=True,
            nullable=False,
            comment="匹配关键词",
        ),
        sa.Column(
            "tag",
            sa.String(64),
            nullable=False,
            comment="映射到的资产标签",
        ),
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

    # 写入种子数据
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        {
            "keyword": item["keyword"],
            "tag": item["tag"],
            "is_active": True,
            "priority": item.get("priority", 0),
            "created_at": now,
            "updated_at": now,
        }
        for item in _SEED_DATA
    ]
    op.bulk_insert(
        sa.table(
            "keyword_tag_config",
            sa.Column("keyword", sa.String(128)),
            sa.Column("tag", sa.String(64)),
            sa.Column("is_active", sa.Boolean()),
            sa.Column("priority", sa.Integer()),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("updated_at", sa.DateTime()),
        ),
        rows,
    )


def downgrade() -> None:
    """删除 keyword_tag_config 表。"""
    op.drop_table("keyword_tag_config")
