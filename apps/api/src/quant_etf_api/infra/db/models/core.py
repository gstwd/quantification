from datetime import datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from quant_etf_api.infra.db.base import Base


class EtfUniverseModel(Base):
    __tablename__ = "etf_universe"

    etf_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    fund_full_name: Mapped[str | None] = mapped_column(String(256))
    tracking_index_code: Mapped[str | None] = mapped_column(String(32))
    tracking_index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    fund_company: Mapped[str | None] = mapped_column(String(128))
    listing_date: Mapped[Date | None] = mapped_column(Date)
    delisting_date: Mapped[Date | None] = mapped_column(Date)
    category: Mapped[str] = mapped_column(String(64), default="broad_index")
    is_a_share_etf: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    data_source: Mapped[str] = mapped_column(String(32), default="seed")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BenchmarkIndexModel(Base):
    __tablename__ = "benchmark_index"

    index_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False)
    exchange: Mapped[str] = mapped_column(String(16), default="CN")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EtfDailyBarModel(Base):
    __tablename__ = "etf_daily_bar"
    __table_args__ = (UniqueConstraint("trade_date", "etf_code", name="uq_etf_daily_bar"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False)
    etf_code: Mapped[str] = mapped_column(ForeignKey("etf_universe.etf_code"), nullable=False)
    open_price: Mapped[float | None] = mapped_column(Float)
    high_price: Mapped[float | None] = mapped_column(Float)
    low_price: Mapped[float | None] = mapped_column(Float)
    close_price: Mapped[float | None] = mapped_column(Float)
    prev_close_price: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    amplitude: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="stub")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IndexDailyBarModel(Base):
    __tablename__ = "index_daily_bar"
    __table_args__ = (UniqueConstraint("trade_date", "index_code", name="uq_index_daily_bar"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False)
    index_code: Mapped[str] = mapped_column(ForeignKey("benchmark_index.index_code"), nullable=False)
    open_price: Mapped[float | None] = mapped_column(Float)
    high_price: Mapped[float | None] = mapped_column(Float)
    low_price: Mapped[float | None] = mapped_column(Float)
    close_price: Mapped[float | None] = mapped_column(Float)
    prev_close_price: Mapped[float | None] = mapped_column(Float)
    change_pct: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="stub")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EtfDailyShareModel(Base):
    __tablename__ = "etf_daily_share"
    __table_args__ = (UniqueConstraint("trade_date", "etf_code", name="uq_etf_daily_share"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False)
    etf_code: Mapped[str] = mapped_column(ForeignKey("etf_universe.etf_code"), nullable=False)
    shares_total: Mapped[float | None] = mapped_column(Float)
    shares_delta: Mapped[float | None] = mapped_column(Float)
    shares_delta_pct: Mapped[float | None] = mapped_column(Float)
    nav: Mapped[float | None] = mapped_column(Float)
    aum: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32), default="stub")
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SourcePayloadLogModel(Base):
    __tablename__ = "source_payload_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_key: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_date: Mapped[Date | None] = mapped_column(Date)
    request_meta: Mapped[dict | None] = mapped_column(JSON)
    response_payload: Mapped[dict | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FactorDefinitionModel(Base):
    __tablename__ = "factor_definition"

    factor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    owner_plugin: Mapped[str] = mapped_column(String(64), nullable=False)


class EtfFactorValueModel(Base):
    __tablename__ = "etf_factor_value"
    __table_args__ = (UniqueConstraint("trade_date", "etf_code", "factor_id", "strategy_id", name="uq_etf_factor_value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False)
    etf_code: Mapped[str] = mapped_column(ForeignKey("etf_universe.etf_code"), nullable=False)
    factor_id: Mapped[str] = mapped_column(ForeignKey("factor_definition.factor_id"), nullable=False)
    factor_value_numeric: Mapped[float | None] = mapped_column(Float)
    factor_value_text: Mapped[str | None] = mapped_column(String(128))
    factor_payload: Mapped[dict | None] = mapped_column(JSON)
    strategy_id: Mapped[str | None] = mapped_column(String(64))


class SignalDefinitionModel(Base):
    __tablename__ = "signal_definition"

    signal_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")


class EtfSignalModel(Base):
    __tablename__ = "etf_signal"
    __table_args__ = (UniqueConstraint("trade_date", "etf_code", "strategy_id", name="uq_etf_signal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False)
    etf_code: Mapped[str] = mapped_column(ForeignKey("etf_universe.etf_code"), nullable=False)
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    signal_score: Mapped[float] = mapped_column(Float, nullable=False)
    signal_level: Mapped[str] = mapped_column(String(32), nullable=False)
    signal_label: Mapped[str] = mapped_column(String(128), nullable=False)
    signal_payload: Mapped[dict | None] = mapped_column(JSON)
    run_id: Mapped[str | None] = mapped_column(String(64))


class StrategyPluginModel(Base):
    __tablename__ = "strategy_plugin"

    strategy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    plugin_module: Mapped[str] = mapped_column(String(256), nullable=False)
    plugin_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active")
    default_params: Mapped[dict | None] = mapped_column(JSON)
    result_schema: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchRunModel(Base):
    __tablename__ = "research_run"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_type: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_id: Mapped[str | None] = mapped_column(String(64))
    trade_date: Mapped[Date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    params: Mapped[dict | None] = mapped_column(JSON)
    metrics: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class ResearchRunItemModel(Base):
    __tablename__ = "research_run_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("research_run.run_id"), nullable=False)
    etf_code: Mapped[str] = mapped_column(ForeignKey("etf_universe.etf_code"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    metrics: Mapped[dict | None] = mapped_column(JSON)
