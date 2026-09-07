"""申万行业轮动子系统数据模型（与 benchmark_index/index_daily_bar 完全隔离）。"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from quant_etf_api.infra.db.base import Base, utcnow


class IndustryUniverseModel(Base):
    """申万一级行业目录。"""

    __tablename__ = "industry_universe"

    industry_code: Mapped[str] = mapped_column(
        String(16), primary_key=True, comment="申万一级行业代码，如 801010"
    )
    name_cn: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="行业中文名称，如 农林牧渔"
    )
    classification: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="sw",
        server_default="sw",
        comment="行业分类体系：sw=申万一级",
    )
    is_benchmark_excluded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa.text("FALSE"),
        comment="是否从 RRG 行业等权基准中剔除（申万综合=是）",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=sa.text("TRUE"),
        comment="是否启用",
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


class IndustryDailyBarModel(Base):
    """申万一级行业指数日线（独立于 index_daily_bar 存储）。"""

    __tablename__ = "industry_daily_bar"
    __table_args__ = (
        UniqueConstraint("trade_date", "industry_code", name="uq_industry_daily_bar"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    industry_code: Mapped[str] = mapped_column(
        ForeignKey("industry_universe.industry_code"),
        nullable=False,
        comment="申万一级行业代码",
    )
    open_price: Mapped[float | None] = mapped_column(Float, comment="开盘点位")
    high_price: Mapped[float | None] = mapped_column(Float, comment="最高点位")
    low_price: Mapped[float | None] = mapped_column(Float, comment="最低点位")
    close_price: Mapped[float | None] = mapped_column(Float, comment="收盘点位")
    prev_close_price: Mapped[float | None] = mapped_column(Float, comment="前收盘点位")
    change_pct: Mapped[float | None] = mapped_column(Float, comment="涨跌幅，单位 %")
    volume: Mapped[float | None] = mapped_column(Float, comment="成交量")
    turnover: Mapped[float | None] = mapped_column(Float, comment="成交额")
    source: Mapped[str] = mapped_column(String(32), default="akshare_sw", comment="数据来源")
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="数据入库时间（UTC）"
    )


class IndustryMembershipEventModel(Base):
    """申万行业成分归属事件（时变，start_date 为归属生效日）。"""

    __tablename__ = "industry_membership_event"
    __table_args__ = (
        UniqueConstraint(
            "stock_code",
            "industry_code",
            "start_date",
            name="uq_industry_membership_event",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    stock_code: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="个股代码，如 600000"
    )
    industry_code: Mapped[str] = mapped_column(
        ForeignKey("industry_universe.industry_code"),
        nullable=False,
        comment="申万一级行业代码",
    )
    start_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="归属生效日（非公告日，PIT 边界与研报一致）"
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="sw_classification_2021", comment="来源"
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="入库时间（UTC）"
    )


class StockDailyCloseModel(Base):
    """个股收盘价（仅用于数量占比扩散，不保存 OHLC）。"""

    __tablename__ = "stock_daily_close"
    __table_args__ = (UniqueConstraint("trade_date", "stock_code", name="uq_stock_daily_close"),)

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    stock_code: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="个股代码，如 600000"
    )
    close: Mapped[float | None] = mapped_column(Float, comment="收盘价（元）")
    source: Mapped[str] = mapped_column(String(32), default="akshare_em", comment="数据来源")
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="数据入库时间（UTC）"
    )


class IndustryFactorValueModel(Base):
    """行业因子值（RRG / 扩散），独立于通用 index_factor_value 存储。"""

    __tablename__ = "industry_factor_value"
    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "industry_code",
            "factor_id",
            "params_hash",
            name="uq_industry_factor_value_params",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    industry_code: Mapped[str] = mapped_column(
        ForeignKey("industry_universe.industry_code"),
        nullable=False,
        comment="申万一级行业代码",
    )
    factor_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="行业因子 ID")
    factor_value_numeric: Mapped[float | None] = mapped_column(Float, comment="因子数值")
    factor_payload: Mapped[dict | None] = mapped_column(
        JSON, comment="计算中间数据（warm-up/样本数等）"
    )
    params_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        server_default="",
        comment="参数指纹（规范化参数字典 sha256），区分同 factor_id 不同参数计算",
    )
    params: Mapped[dict | None] = mapped_column(
        JSON, comment="计算参数字典（lookback/smooth/基准剔除等）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        comment="最后更新时间（UTC）",
    )
