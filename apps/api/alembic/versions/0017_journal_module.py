"""市场记忆与研究日志模块 — 初始表结构

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-17
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# 预设标签种子数据（名称、颜色、说明）
PRESET_TAGS = [
    ("科技主导", "#EF4444", "科技/成长方向主导市场"),
    ("红利防御", "#3B82F6", "红利/价值方向占优"),
    ("成长风格", "#F59E0B", "成长因子跑赢价值因子"),
    ("价值风格", "#6366F1", "价值因子跑赢成长因子"),
    ("大盘强", "#10B981", "大盘股显著跑赢小盘股"),
    ("小盘强", "#EC4899", "小盘股显著跑赢大盘股"),
    ("趋势行情", "#84CC16", "趋势明确，顺势操作获利"),
    ("震荡行情", "#EAB308", "缺乏方向，区间震荡"),
    ("放量突破", "#F97316", "关键位置放量突破"),
    ("缩量调整", "#94A3B8", "缩量回调/整理"),
    ("普涨", "#22C55E", "全市场普遍上涨"),
    ("普跌", "#DC2626", "全市场普遍下跌"),
    ("分化行情", "#A855F7", "行业/风格严重分化"),
    ("情绪高潮", "#FF4500", "市场情绪极度亢奋"),
    ("恐慌", "#991B1B", "市场恐慌抛售"),
    ("超跌反弹", "#14B8A6", "超跌后的技术性反弹"),
    ("高低切换", "#8B5CF6", "高位方向切换到低位方向"),
    ("主线行情", "#F43F5E", "存在清晰的主线板块"),
    ("轮动加速", "#FB923C", "板块轮动速度明显加快"),
    ("北向大幅流入", "#06B6D4", "北向资金单日净流入超过 50 亿"),
    ("北向大幅流出", "#0891B2", "北向资金单日净流出超过 50 亿"),
    ("政策驱动", "#7C3AED", "政策消息主导市场"),
    ("业绩驱动", "#059669", "财报/业绩预告主导市场"),
    ("事件驱动", "#D946EF", "特定事件（地缘/宏观数据等）主导市场"),
]


def upgrade() -> None:
    # 1. 日志主表
    op.create_table(
        "journal_entry",
        sa.Column("id", sa.String(36), primary_key=True, comment="日志唯一标识（UUID）"),
        sa.Column("trade_date", sa.Date, nullable=False, comment="交易日期"),
        sa.Column("market_temperature", sa.SmallInteger, nullable=True, comment="市场温度 0-100"),
        sa.Column("profit_effect", sa.SmallInteger, nullable=True, comment="赚钱效应 0-100"),
        sa.Column("risk_preference", sa.SmallInteger, nullable=True, comment="风险偏好 0-100"),
        sa.Column("trading_difficulty", sa.SmallInteger, nullable=True, comment="交易难度 0-100"),
        sa.Column("market_consistency", sa.SmallInteger, nullable=True, comment="市场一致性 0-100"),
        sa.Column("market_phase", sa.String(32), nullable=True, comment="市场阶段枚举值"),
        sa.Column("one_line_summary", sa.String(256), nullable=True, comment="一句话市场摘要"),
        sa.Column("is_complete", sa.Boolean, nullable=False, server_default=sa.text("FALSE"), comment="是否已完成填写"),
        sa.Column("word_count", sa.Integer, nullable=False, server_default=sa.text("0"), comment="非结构化内容总字数"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()"), comment="记录创建时间（UTC）"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()"), comment="记录最后更新时间（UTC）"),
        sa.UniqueConstraint("trade_date", name="uq_journal_entry_trade_date"),
    )
    op.create_index("idx_journal_entry_trade_date", "journal_entry", ["trade_date"])
    op.create_index("idx_journal_entry_phase", "journal_entry", ["market_phase"])
    op.create_index("idx_journal_entry_complete", "journal_entry", ["is_complete"])

    # 2. 指数快照表
    op.create_table(
        "journal_index_snapshot",
        sa.Column("id", sa.String(36), primary_key=True, comment="快照唯一标识（UUID）"),
        sa.Column("entry_id", sa.String(36), sa.ForeignKey("journal_entry.id", ondelete="CASCADE"), nullable=False, comment="关联的日志 ID"),
        sa.Column("index_code", sa.String(32), nullable=False, comment="指数代码"),
        sa.Column("index_name", sa.String(128), nullable=False, comment="指数中文名称"),
        sa.Column("index_category", sa.String(32), nullable=True, comment="指数分类：broad / industry / theme"),
        sa.Column("sort_order", sa.SmallInteger, nullable=False, server_default=sa.text("0"), comment="展示排序"),
        sa.Column("close_price", sa.Float, nullable=True, comment="收盘点位"),
        sa.Column("change_pct", sa.Float, nullable=True, comment="日涨跌幅（%）"),
        sa.Column("volume_ratio_20d", sa.Float, nullable=True, comment="20 日量比"),
        sa.Column("return_5d", sa.Float, nullable=True, comment="5 日收益率（%）"),
        sa.Column("return_20d", sa.Float, nullable=True, comment="20 日收益率（%）"),
        sa.Column("return_60d", sa.Float, nullable=True, comment="60 日收益率（%）"),
        sa.Column("return_120d", sa.Float, nullable=True, comment="120 日收益率（%）"),
        sa.Column("ma_20d_deviation", sa.Float, nullable=True, comment="收盘价偏离 MA20（%）"),
        sa.Column("ma_60d_deviation", sa.Float, nullable=True, comment="收盘价偏离 MA60（%）"),
        sa.Column("ma_120d_deviation", sa.Float, nullable=True, comment="收盘价偏离 MA120（%）"),
        sa.Column("volatility_20d", sa.Float, nullable=True, comment="20 日波动率（%）"),
        sa.Column("max_drawdown_60d", sa.Float, nullable=True, comment="60 日最大回撤（%）"),
        sa.UniqueConstraint("entry_id", "index_code", name="uq_journal_snapshot_entry_index"),
    )
    op.create_index("idx_journal_snapshot_entry", "journal_index_snapshot", ["entry_id"])

    # 3. 手动市场数据表（一对一）
    op.create_table(
        "journal_market_data",
        sa.Column("id", sa.String(36), primary_key=True, comment="市场数据唯一标识（UUID）"),
        sa.Column("entry_id", sa.String(36), sa.ForeignKey("journal_entry.id", ondelete="CASCADE"), nullable=False, unique=True, comment="关联的日志 ID（一对一）"),
        sa.Column("market_up_stocks", sa.Integer, nullable=True, comment="全市场上涨家数"),
        sa.Column("market_down_stocks", sa.Integer, nullable=True, comment="全市场下跌家数"),
        sa.Column("market_flat_stocks", sa.Integer, nullable=True, comment="全市场平盘家数"),
        sa.Column("limit_up_stocks", sa.Integer, nullable=True, comment="涨停家数"),
        sa.Column("limit_down_stocks", sa.Integer, nullable=True, comment="跌停家数"),
        sa.Column("total_turnover_yi", sa.Float, nullable=True, comment="全市场成交额（亿元）"),
        sa.Column("turnover_vs_prev_pct", sa.Float, nullable=True, comment="成交额较前日变化（%）"),
        sa.Column("north_bound_net_yi", sa.Float, nullable=True, comment="北向资金净流入（亿元）"),
        sa.Column("margin_balance_change_yi", sa.Float, nullable=True, comment="两融余额变化（亿元）"),
        sa.Column("size_style", sa.String(16), nullable=True, comment="大小盘风格：large_cap / small_cap / balanced"),
        sa.Column("growth_style", sa.String(16), nullable=True, comment="成长价值风格：growth / value / balanced"),
        sa.Column("sector_leading", sa.String(32), nullable=True, comment="行业主导方向"),
        sa.Column("top_sectors", sa.Text, nullable=True, comment="领涨行业，逗号分隔"),
        sa.Column("bottom_sectors", sa.Text, nullable=True, comment="领跌行业，逗号分隔"),
        sa.Column("data_source", sa.String(128), nullable=True, comment="数据来源说明"),
        sa.Column("notes", sa.Text, nullable=True, comment="补充备注"),
    )

    # 4. 观察分区表
    op.create_table(
        "journal_observation",
        sa.Column("id", sa.String(36), primary_key=True, comment="观察唯一标识（UUID）"),
        sa.Column("entry_id", sa.String(36), sa.ForeignKey("journal_entry.id", ondelete="CASCADE"), nullable=False, comment="关联的日志 ID"),
        sa.Column("section_key", sa.String(32), nullable=False, comment="分区标识"),
        sa.Column("section_label", sa.String(64), nullable=False, comment="分区中文标签"),
        sa.Column("content", sa.Text, nullable=True, comment="纯文本内容"),
        sa.Column("sort_order", sa.SmallInteger, nullable=False, server_default=sa.text("0"), comment="分区展示顺序"),
        sa.UniqueConstraint("entry_id", "section_key", name="uq_journal_observation_entry_section"),
    )
    op.create_index("idx_journal_observation_entry", "journal_observation", ["entry_id"])

    # 5. 标签定义表
    op.create_table(
        "journal_tag",
        sa.Column("id", sa.String(36), primary_key=True, comment="标签唯一标识（UUID）"),
        sa.Column("name", sa.String(64), nullable=False, comment="标签名称"),
        sa.Column("color", sa.String(7), nullable=False, server_default=sa.text("'#3B82F6'"), comment="十六进制颜色值"),
        sa.Column("description", sa.String(256), nullable=True, comment="标签说明"),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.text("FALSE"), comment="是否为预设标签"),
        sa.Column("usage_count", sa.Integer, nullable=False, server_default=sa.text("0"), comment="使用次数"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()"), comment="记录创建时间（UTC）"),
        sa.UniqueConstraint("name", name="uq_journal_tag_name"),
    )

    # 6. 日志-标签映射表
    op.create_table(
        "journal_entry_tag",
        sa.Column("entry_id", sa.String(36), sa.ForeignKey("journal_entry.id", ondelete="CASCADE"), primary_key=True, comment="日志 ID"),
        sa.Column("tag_id", sa.String(36), sa.ForeignKey("journal_tag.id", ondelete="CASCADE"), primary_key=True, comment="标签 ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()"), comment="关联创建时间（UTC）"),
    )

    # 7. AI 分析表
    op.create_table(
        "journal_ai_analysis",
        sa.Column("id", sa.String(36), primary_key=True, comment="分析结果唯一标识（UUID）"),
        sa.Column("entry_id", sa.String(36), sa.ForeignKey("journal_entry.id", ondelete="CASCADE"), nullable=False, unique=True, comment="关联的日志 ID（一对一）"),
        sa.Column("model", sa.String(64), nullable=False, comment="使用的 LLM 模型标识"),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'"), comment="分析状态：pending / running / success / failed"),
        sa.Column("market_summary", sa.Text, nullable=True, comment="AI 生成的市场总结"),
        sa.Column("phase_judgment", sa.Text, nullable=True, comment="AI 生成的市场阶段判断"),
        sa.Column("style_judgment", sa.Text, nullable=True, comment="AI 生成的风格判断"),
        sa.Column("core_narrative", sa.Text, nullable=True, comment="AI 生成的核心叙事"),
        sa.Column("risk_alert", sa.Text, nullable=True, comment="AI 生成的风险提示"),
        sa.Column("focus_direction", sa.Text, nullable=True, comment="AI 生成的后续关注方向"),
        sa.Column("prompt_version", sa.String(32), nullable=True, comment="使用的 prompt 版本号"),
        sa.Column("raw_response", sa.Text, nullable=True, comment="LLM 原始响应（调试用）"),
        sa.Column("error_message", sa.Text, nullable=True, comment="失败时的错误信息"),
        sa.Column("tokens_used", sa.Integer, nullable=True, comment="消耗的 token 数"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()"), comment="记录创建时间（UTC）"),
    )

    # 插入预设标签
    for name, color, desc in PRESET_TAGS:
        op.execute(
            sa.text(
                "INSERT INTO journal_tag (id, name, color, description, is_system) "
                "VALUES (gen_random_uuid()::text, :name, :color, :desc, TRUE) "
                "ON CONFLICT (name) DO NOTHING"
            ).bindparams(name=name, color=color, desc=desc)
        )


def downgrade() -> None:
    op.drop_table("journal_entry_tag")
    op.drop_table("journal_ai_analysis")
    op.drop_table("journal_observation")
    op.drop_table("journal_market_data")
    op.drop_table("journal_index_snapshot")
    op.drop_table("journal_tag")
    op.drop_table("journal_entry")
