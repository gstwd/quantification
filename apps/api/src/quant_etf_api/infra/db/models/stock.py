"""个股元数据模型（个股目录 + 日线质量快照）。"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from quant_etf_api.infra.db.base import Base, utcnow


class StockUniverseModel(Base):
    """个股元数据表，对标 benchmark_index 与 index_daily_bar 的关系。

    stock_universe 存放个股目录（代码/名称/申万行业/上市与退市状态）以及由
    “数据质量检查”刷新维护的日线质量快照；行情明细仍在 stock_daily_close，
    本表不改动其列结构。
    """

    __tablename__ = "stock_universe"

    stock_code: Mapped[str] = mapped_column(
        String(16), primary_key=True, comment="个股代码，如 600000"
    )
    name_cn: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default="", comment="股票中文名称"
    )
    industry_code: Mapped[str | None] = mapped_column(
        ForeignKey("industry_universe.industry_code"),
        nullable=True,
        comment="当前有效申万一级行业代码，可空",
    )
    ipo_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="上市日期")
    delist_date: Mapped[date | None] = mapped_column(Date, nullable=True, comment="退市日期")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.text("TRUE"),
        comment="是否活跃，False=已退市/停止交易",
    )
    source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="元数据来源，如 akshare_exchange"
    )
    data_start_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="库内日线最早日期（质量快照）"
    )
    data_end_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="库内日线最晚日期（质量快照）"
    )
    bar_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="库内日线行数（质量快照）"
    )
    missing_day_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="按交易日历校验的缺失交易日数（质量快照）"
    )
    quality_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最近一次数据质量检查时间（UTC）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="记录创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        comment="记录最后更新时间（UTC）",
    )
