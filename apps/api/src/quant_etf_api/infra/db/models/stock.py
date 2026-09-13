"""个股元数据与 Tushare 明细数据模型。"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from quant_etf_api.infra.db.base import Base, utcnow


class StockUniverseModel(Base):
    """个股元数据表，对标 benchmark_index 与 index_daily_bar 的关系。

    stock_universe 仅存放个股目录（Tushare 代码/市场/交易所/申万行业/
    上市与退市状态）；行情明细与质量快照分别存放在 stock_daily_close、
    stock_daily_basic、stock_moneyflow 和 data_health_snapshot。
    """

    __tablename__ = "stock_universe"
    __table_args__ = (Index("uq_stock_universe_ts_code", "ts_code", unique=True),)

    stock_code: Mapped[str] = mapped_column(
        String(16), primary_key=True, comment="个股代码，如 600000"
    )
    name_cn: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default="", comment="股票中文名称"
    )
    ts_code: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="Tushare 股票代码，如 600000.SH"
    )
    market: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="Tushare 市场类别：主板/创业板/科创板"
    )
    exchange: Mapped[str | None] = mapped_column(
        String(8), nullable=True, comment="Tushare 交易所代码：SSE/SZSE"
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="记录创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        comment="记录最后更新时间（UTC）",
    )


class StockDailyBasicModel(Base):
    """个股每日指标（Tushare daily_basic 全字段，单位保持上游原生口径）。"""

    __tablename__ = "stock_daily_basic"
    __table_args__ = (
        UniqueConstraint("trade_date", "stock_code", name="uq_stock_daily_basic"),
        Index("ix_stock_daily_basic_code_date", "stock_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    stock_code: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="个股代码，如 600000"
    )
    close: Mapped[float | None] = mapped_column(Float, comment="当日收盘价（元）")
    turnover_rate: Mapped[float | None] = mapped_column(
        Float, comment="换手率（成交量/无限售流通股数），单位 %"
    )
    turnover_rate_f: Mapped[float | None] = mapped_column(Float, comment="自由流通换手率，单位 %")
    volume_ratio: Mapped[float | None] = mapped_column(Float, comment="量比")
    pe: Mapped[float | None] = mapped_column(Float, comment="市盈率（总市值/净利润）")
    pe_ttm: Mapped[float | None] = mapped_column(Float, comment="市盈率 TTM")
    pb: Mapped[float | None] = mapped_column(Float, comment="市净率")
    ps: Mapped[float | None] = mapped_column(Float, comment="市销率")
    ps_ttm: Mapped[float | None] = mapped_column(Float, comment="市销率 TTM")
    dv_ratio: Mapped[float | None] = mapped_column(Float, comment="股息率，单位 %")
    dv_ttm: Mapped[float | None] = mapped_column(Float, comment="股息率 TTM，单位 %")
    total_share: Mapped[float | None] = mapped_column(Float, comment="总股本（万股）")
    float_share: Mapped[float | None] = mapped_column(Float, comment="流通股本（万股）")
    free_share: Mapped[float | None] = mapped_column(Float, comment="自由流通股本（万股）")
    total_mv: Mapped[float | None] = mapped_column(Float, comment="总市值（万元）")
    circ_mv: Mapped[float | None] = mapped_column(Float, comment="流通市值（万元）")
    limit_status: Mapped[int | None] = mapped_column(
        Integer,
        comment="收盘涨跌状态：0平盘/1上涨/2涨停/3一字涨停/4下跌/5跌停/6一字跌停",
    )
    source: Mapped[str] = mapped_column(
        String(32), default="tushare", server_default="tushare", comment="数据来源"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="数据入库时间（UTC）"
    )


class StockMoneyflowModel(Base):
    """个股资金流向（Tushare moneyflow 全字段，量单位手、额单位万元）。"""

    __tablename__ = "stock_moneyflow"
    __table_args__ = (
        UniqueConstraint("trade_date", "stock_code", name="uq_stock_moneyflow"),
        Index("ix_stock_moneyflow_code_date", "stock_code", "trade_date"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    stock_code: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="个股代码，如 600000"
    )
    buy_sm_vol: Mapped[int | None] = mapped_column(BigInteger, comment="小单买入量（手）")
    buy_sm_amount: Mapped[float | None] = mapped_column(Float, comment="小单买入金额（万元）")
    sell_sm_vol: Mapped[int | None] = mapped_column(BigInteger, comment="小单卖出量（手）")
    sell_sm_amount: Mapped[float | None] = mapped_column(Float, comment="小单卖出金额（万元）")
    buy_md_vol: Mapped[int | None] = mapped_column(BigInteger, comment="中单买入量（手）")
    buy_md_amount: Mapped[float | None] = mapped_column(Float, comment="中单买入金额（万元）")
    sell_md_vol: Mapped[int | None] = mapped_column(BigInteger, comment="中单卖出量（手）")
    sell_md_amount: Mapped[float | None] = mapped_column(Float, comment="中单卖出金额（万元）")
    buy_lg_vol: Mapped[int | None] = mapped_column(BigInteger, comment="大单买入量（手）")
    buy_lg_amount: Mapped[float | None] = mapped_column(Float, comment="大单买入金额（万元）")
    sell_lg_vol: Mapped[int | None] = mapped_column(BigInteger, comment="大单卖出量（手）")
    sell_lg_amount: Mapped[float | None] = mapped_column(Float, comment="大单卖出金额（万元）")
    buy_elg_vol: Mapped[int | None] = mapped_column(BigInteger, comment="特大单买入量（手）")
    buy_elg_amount: Mapped[float | None] = mapped_column(Float, comment="特大单买入金额（万元）")
    sell_elg_vol: Mapped[int | None] = mapped_column(BigInteger, comment="特大单卖出量（手）")
    sell_elg_amount: Mapped[float | None] = mapped_column(Float, comment="特大单卖出金额（万元）")
    net_mf_vol: Mapped[int | None] = mapped_column(BigInteger, comment="净流入量（手）")
    net_mf_amount: Mapped[float | None] = mapped_column(Float, comment="净流入额（万元）")
    source: Mapped[str] = mapped_column(
        String(32), default="tushare", server_default="tushare", comment="数据来源"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="数据入库时间（UTC）"
    )
