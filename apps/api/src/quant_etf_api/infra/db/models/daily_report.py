"""每日量化研究简报持久化模型。"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from quant_etf_api.infra.db.base import Base
from quant_etf_api.infra.time import utcnow_aware


class MarketDailyReportModel(Base):
    """已发布日报的不可变修订版本。"""

    __tablename__ = "market_daily_report"
    __table_args__ = (
        UniqueConstraint("report_date", "revision_no", name="uq_market_daily_report_revision"),
        Index("ix_market_daily_report_date", "report_date"),
        Index(
            "uq_market_daily_report_published",
            "report_date",
            unique=True,
            postgresql_where=text("status = 'published'"),
        ),
    )

    report_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="日报唯一 ID，UUID 格式"
    )
    report_date: Mapped[date] = mapped_column(Date, nullable=False, comment="报告交易日")
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, comment="同日修订号")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="published / superseded"
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False, comment="日报标题")
    market_regime: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="当日市场状态标签"
    )
    readiness_status: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="ready / partial"
    )
    generator_version: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="生成器版本"
    )
    payload_version: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="报告内容契约版本"
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="内容生成时间（UTC）"
    )
    data_cutoff_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="数据截止时间（UTC）"
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow_aware,
        nullable=False,
        comment="发布时间（UTC）",
    )
    content_json: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="完整日报内容")
    fact_snapshot_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="发布时绑定的确定性事实快照"
    )
    fact_snapshot_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="事实快照 SHA-256"
    )
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="完整内容 SHA-256"
    )
