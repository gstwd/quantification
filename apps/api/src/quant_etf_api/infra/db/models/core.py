from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from quant_etf_api.infra.db.base import Base


class EtfUniverseModel(Base):
    __tablename__ = "etf_universe"

    etf_code: Mapped[str] = mapped_column(
        String(16), primary_key=True, comment="ETF 代码，如 510300"
    )
    exchange: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="交易所代码，SSE=上交所，SZSE=深交所"
    )
    name_cn: Mapped[str] = mapped_column(String(128), nullable=False, comment="ETF 中文简称")
    fund_full_name: Mapped[str | None] = mapped_column(String(256), comment="基金全称")
    tracking_index_code: Mapped[str | None] = mapped_column(
        String(32), comment="跟踪指数代码，如 000300"
    )
    tracking_index_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="跟踪指数名称，如 沪深300"
    )
    fund_company: Mapped[str | None] = mapped_column(String(128), comment="基金管理公司名称")
    listing_date: Mapped[Date | None] = mapped_column(Date, comment="上市日期")
    delisting_date: Mapped[Date | None] = mapped_column(Date, comment="退市日期，NULL 表示仍在交易")
    category: Mapped[str] = mapped_column(
        String(64), default="broad_index", comment="ETF 分类，如 broad_index=宽基指数"
    )
    is_a_share_etf: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否为 A 股 ETF")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="是否在交易中，退市后置 False"
    )
    data_source: Mapped[str] = mapped_column(
        String(32), default="seed", comment="数据来源，seed=内置种子数据"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="记录创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="记录最后更新时间（UTC）",
    )


class BenchmarkIndexModel(Base):
    __tablename__ = "benchmark_index"

    index_code: Mapped[str] = mapped_column(
        String(32), primary_key=True, comment="指数代码，如 000300"
    )
    name_cn: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="指数中文名称，如 沪深300"
    )
    exchange: Mapped[str] = mapped_column(
        String(16), default="CN", comment="所属市场，CN=中国 A 股"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="记录创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="记录最后更新时间（UTC）",
    )


class EtfDailyBarModel(Base):
    __tablename__ = "etf_daily_bar"
    __table_args__ = (UniqueConstraint("trade_date", "etf_code", name="uq_etf_daily_bar"),)

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="交易日期")
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("etf_universe.etf_code"),
        nullable=False,
        comment="ETF 代码，外键关联 etf_universe",
    )
    open_price: Mapped[float | None] = mapped_column(Float, comment="开盘价（前复权）")
    high_price: Mapped[float | None] = mapped_column(Float, comment="最高价（前复权）")
    low_price: Mapped[float | None] = mapped_column(Float, comment="最低价（前复权）")
    close_price: Mapped[float | None] = mapped_column(Float, comment="收盘价（前复权）")
    prev_close_price: Mapped[float | None] = mapped_column(Float, comment="前收盘价（前复权）")
    change_pct: Mapped[float | None] = mapped_column(
        Float, comment="涨跌幅，单位 %，如 1.23 表示涨 1.23%"
    )
    volume: Mapped[float | None] = mapped_column(Float, comment="成交量，单位 手（100 股）")
    turnover: Mapped[float | None] = mapped_column(Float, comment="成交额，单位 元")
    amplitude: Mapped[float | None] = mapped_column(Float, comment="振幅，单位 %")
    source: Mapped[str] = mapped_column(
        String(32), default="stub", comment="数据来源，akshare=AkShare，stub=占位数据"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="数据入库时间（UTC）"
    )


class IndexDailyBarModel(Base):
    __tablename__ = "index_daily_bar"
    __table_args__ = (UniqueConstraint("trade_date", "index_code", name="uq_index_daily_bar"),)

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="交易日期")
    index_code: Mapped[str] = mapped_column(
        ForeignKey("benchmark_index.index_code"),
        nullable=False,
        comment="指数代码，外键关联 benchmark_index",
    )
    open_price: Mapped[float | None] = mapped_column(Float, comment="开盘点位")
    high_price: Mapped[float | None] = mapped_column(Float, comment="最高点位")
    low_price: Mapped[float | None] = mapped_column(Float, comment="最低点位")
    close_price: Mapped[float | None] = mapped_column(Float, comment="收盘点位")
    prev_close_price: Mapped[float | None] = mapped_column(Float, comment="前收盘点位")
    change_pct: Mapped[float | None] = mapped_column(Float, comment="涨跌幅，单位 %")
    volume: Mapped[float | None] = mapped_column(Float, comment="成交量，单位 手")
    turnover: Mapped[float | None] = mapped_column(Float, comment="成交额，单位 元")
    source: Mapped[str] = mapped_column(
        String(32), default="stub", comment="数据来源，stub=占位数据"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="数据入库时间（UTC）"
    )


class EtfDailyShareModel(Base):
    __tablename__ = "etf_daily_share"
    __table_args__ = (UniqueConstraint("trade_date", "etf_code", name="uq_etf_daily_share"),)

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="交易日期")
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("etf_universe.etf_code"),
        nullable=False,
        comment="ETF 代码，外键关联 etf_universe",
    )
    shares_total: Mapped[float | None] = mapped_column(Float, comment="总份额，单位 亿份")
    shares_delta: Mapped[float | None] = mapped_column(
        Float, comment="当日份额变化量，单位 亿份，正=申购，负=赎回"
    )
    shares_delta_pct: Mapped[float | None] = mapped_column(Float, comment="当日份额变化率，单位 %")
    nav: Mapped[float | None] = mapped_column(Float, comment="单位净值，单位 元/份")
    aum: Mapped[float | None] = mapped_column(Float, comment="资产管理规模（AUM），单位 亿元")
    source: Mapped[str] = mapped_column(
        String(32), default="stub", comment="数据来源，akshare=AkShare，stub=占位数据"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="数据入库时间（UTC）"
    )


class SourcePayloadLogModel(Base):
    __tablename__ = "source_payload_log"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    source_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="数据源名称，如 akshare、akshare_index"
    )
    resource_type: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="资源类型，如 daily_bar、share_snapshot"
    )
    resource_key: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="资源标识，如 ETF 代码或指数代码"
    )
    trade_date: Mapped[Date | None] = mapped_column(
        Date, comment="对应交易日期，NULL 表示非日频数据"
    )
    request_meta: Mapped[dict | None] = mapped_column(JSON, comment="请求元数据，如 URL、参数等")
    response_payload: Mapped[dict | None] = mapped_column(
        JSON, comment="原始响应数据，用于调试和审计"
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="数据拉取时间（UTC）"
    )


class FactorDefinitionModel(Base):
    __tablename__ = "factor_definition"

    factor_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="因子唯一标识，如 volume_ratio_20d"
    )
    name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="因子中文名称，如 20日量比"
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="因子计算逻辑说明")
    version: Mapped[str] = mapped_column(
        String(32), default="1.0.0", comment="因子版本号，遵循语义化版本"
    )
    owner_plugin: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="定义该因子的策略插件 ID，NULL 表示独立因子"
    )
    category: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="因子类别：volume/momentum/volatility/flow/valuation",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
        comment="是否启用，禁用后不参与计算且前端隐藏",
    )
    required_data: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="依赖的数据源列表，如 ['etf_bars']，由代码同步"
    )


class EtfFactorValueModel(Base):
    __tablename__ = "etf_factor_value"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "etf_code", "factor_id", "strategy_id", name="uq_etf_factor_value"
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="交易日期")
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("etf_universe.etf_code"),
        nullable=False,
        comment="ETF 代码，外键关联 etf_universe",
    )
    factor_id: Mapped[str] = mapped_column(
        ForeignKey("factor_definition.factor_id"),
        nullable=False,
        comment="因子 ID，外键关联 factor_definition",
    )
    factor_value_numeric: Mapped[float | None] = mapped_column(
        Float, comment="因子数值，如量比 1.92、概率得分 78.5"
    )
    factor_value_text: Mapped[str | None] = mapped_column(
        String(128), comment="因子文本值，用于枚举类因子"
    )
    factor_payload: Mapped[dict | None] = mapped_column(
        JSON, comment="因子计算中间数据，用于调试和解释"
    )
    strategy_id: Mapped[str | None] = mapped_column(
        String(64), comment="产生该因子值的策略 ID，NULL 表示通用因子"
    )


class IndexFactorValueModel(Base):
    """指数因子值表，存储指数级别的因子计算结果。

    与 etf_factor_value 并行，用于指数级因子（volume/momentum/volatility/valuation）。
    """

    __tablename__ = "index_factor_value"
    __table_args__ = (
        UniqueConstraint(
            "trade_date", "index_code", "factor_id", "strategy_id",
            name="uq_index_factor_value",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="交易日期")
    index_code: Mapped[str] = mapped_column(
        ForeignKey("benchmark_index.index_code"),
        nullable=False,
        comment="指数代码，外键关联 benchmark_index",
    )
    factor_id: Mapped[str] = mapped_column(
        ForeignKey("factor_definition.factor_id"),
        nullable=False,
        comment="因子 ID，外键关联 factor_definition",
    )
    factor_value_numeric: Mapped[float | None] = mapped_column(
        Float, comment="因子数值，如量比 1.92、收益率 3.5"
    )
    factor_value_text: Mapped[str | None] = mapped_column(
        String(128), comment="因子文本值，用于枚举类因子"
    )
    factor_payload: Mapped[dict | None] = mapped_column(
        JSON, comment="因子计算中间数据，用于调试和解释"
    )
    strategy_id: Mapped[str | None] = mapped_column(
        String(64), comment="产生该因子值的策略 ID，NULL 表示通用因子"
    )


class SignalDefinitionModel(Base):
    __tablename__ = "signal_definition"

    signal_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="信号唯一标识，如 volume_breakout_signal"
    )
    strategy_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="产生该信号的策略 ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="信号中文名称")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="信号含义和使用说明")
    version: Mapped[str] = mapped_column(String(32), default="1.0.0", comment="信号版本号")


class EtfSignalModel(Base):
    __tablename__ = "etf_signal"
    __table_args__ = (
        UniqueConstraint("trade_date", "etf_code", "strategy_id", name="uq_etf_signal"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="信号对应的交易日期")
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("etf_universe.etf_code"),
        nullable=False,
        comment="ETF 代码，外键关联 etf_universe",
    )
    strategy_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="产生该信号的策略 ID"
    )
    signal_score: Mapped[float] = mapped_column(
        Float, nullable=False, comment="综合得分，0-100，越高表示信号越强"
    )
    signal_level: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="信号等级：HIGH≥70，MID 50-69，LOW<50"
    )
    signal_label: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="信号中文标签，如 高确信、中等关注、正常"
    )
    signal_payload: Mapped[dict | None] = mapped_column(
        JSON, comment="信号计算明细，包含各因子得分等中间数据"
    )
    run_id: Mapped[str | None] = mapped_column(
        String(64), comment="产生该信号的研究运行 ID，NULL 表示手动触发"
    )


class StrategyPluginModel(Base):
    __tablename__ = "strategy_plugin"

    strategy_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="策略唯一标识，如 volume_breakout_daily"
    )
    display_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="策略中文显示名称"
    )
    plugin_module: Mapped[str] = mapped_column(
        String(256), nullable=False, comment="插件 Python 模块路径"
    )
    plugin_version: Mapped[str] = mapped_column(String(32), nullable=False, comment="插件版本号")
    status: Mapped[str] = mapped_column(
        String(32), default="active", comment="插件状态：active=启用，disabled=禁用"
    )
    default_params: Mapped[dict | None] = mapped_column(JSON, comment="策略默认参数，JSON 格式")
    result_schema: Mapped[dict | None] = mapped_column(
        JSON, comment="策略输出结果的 JSON Schema 定义"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="记录创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="记录最后更新时间（UTC）",
    )


class ResearchRunModel(Base):
    __tablename__ = "research_run"

    run_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="运行唯一 ID，UUID 格式"
    )
    run_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="运行类型：daily_ingest=日频入库，strategy_run=策略运行，universe_refresh=标的刷新",
    )
    strategy_id: Mapped[str | None] = mapped_column(
        String(64), comment="关联策略 ID，仅 strategy_run 类型有值"
    )
    trade_date: Mapped[Date | None] = mapped_column(Date, comment="运行对应的交易日期")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="运行状态：pending=待执行，running=执行中，success=成功，failed=失败",
    )
    params: Mapped[dict | None] = mapped_column(JSON, comment="运行参数，JSON 格式")
    metrics: Mapped[dict | None] = mapped_column(
        JSON, comment="运行结果指标，如处理 ETF 数量、耗时等"
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败时的错误信息")
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="运行开始时间（UTC）"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="运行结束时间（UTC），NULL 表示未完成"
    )


class ResearchRunItemModel(Base):
    __tablename__ = "research_run_item"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("research_run.run_id"),
        nullable=False,
        comment="所属运行 ID，外键关联 research_run",
    )
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("etf_universe.etf_code"),
        nullable=False,
        comment="处理的 ETF 代码，外键关联 etf_universe",
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="单个 ETF 处理状态：success=成功，failed=失败，skipped=跳过",
    )
    message: Mapped[str | None] = mapped_column(Text, comment="处理结果说明或错误信息")
    metrics: Mapped[dict | None] = mapped_column(
        JSON, comment="单个 ETF 处理指标，如因子值、信号得分等"
    )


class BacktestRunModel(Base):
    """回测任务主表，记录每次回测的配置和汇总结果。"""

    __tablename__ = "backtest_run"

    backtest_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="回测唯一 ID，UUID 格式"
    )
    strategy_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="关联策略 ID")
    start_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="回测起始日期")
    end_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="回测结束日期")
    universe_filter: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        comment='标的过滤条件，{"mode":"all"} 或 {"mode":"subset","etf_codes":[...]}',
    )
    params: Mapped[dict | None] = mapped_column(JSON, comment="策略参数覆盖，NULL 表示使用默认参数")
    weighting: Mapped[str] = mapped_column(
        String(32), default="equal", comment="组合加权方式：equal=等权，signal_weighted=信号加权"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        comment="回测状态：pending=待执行，running=执行中，success=成功，failed=失败",
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败时的错误信息")
    metrics: Mapped[dict | None] = mapped_column(
        JSON, comment="汇总绩效指标，完成后写入，包含累计收益、最大回撤、夏普比率等"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="回测创建时间（UTC）"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, comment="回测开始执行时间（UTC）")
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="回测完成时间（UTC），NULL 表示未完成"
    )


class BacktestDailyResultModel(Base):
    """回测每日组合绩效，每行对应一个交易日的组合表现。"""

    __tablename__ = "backtest_daily_result"
    __table_args__ = (
        UniqueConstraint("backtest_id", "trade_date", name="uq_backtest_daily"),
        Index("ix_backtest_daily_backtest_id", "backtest_id"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键"
    )
    backtest_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_run.backtest_id"), nullable=False, comment="所属回测 ID"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="交易日期")
    portfolio_return: Mapped[float] = mapped_column(
        Float, nullable=False, comment="当日组合收益率，单位 %"
    )
    cumulative_return: Mapped[float] = mapped_column(
        Float, nullable=False, comment="自回测起始日的累计收益率，单位 %"
    )
    drawdown: Mapped[float] = mapped_column(
        Float, nullable=False, comment="当日回撤幅度，单位 %，负值"
    )
    high_signal_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="当日 HIGH 信号 ETF 数量"
    )
    mid_signal_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="当日 MID 信号 ETF 数量"
    )
    low_signal_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="当日 LOW 信号 ETF 数量"
    )


class BacktestEtfResultModel(Base):
    """回测每日每只 ETF 的信号和实际收益，用于信号准确率分析。"""

    __tablename__ = "backtest_etf_result"
    __table_args__ = (
        UniqueConstraint("backtest_id", "trade_date", "etf_code", name="uq_backtest_etf"),
        Index("ix_backtest_etf_backtest_id", "backtest_id"),
        Index("ix_backtest_etf_code", "backtest_id", "etf_code"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键"
    )
    backtest_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_run.backtest_id"), nullable=False, comment="所属回测 ID"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="信号生成日期（T 日）")
    etf_code: Mapped[str] = mapped_column(
        ForeignKey("etf_universe.etf_code"), nullable=False, comment="ETF 代码"
    )
    signal_score: Mapped[float] = mapped_column(
        Float, nullable=False, comment="信号综合得分，0-100"
    )
    signal_level: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="信号等级：HIGH/MID/LOW"
    )
    in_portfolio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, comment="是否纳入当日组合（HIGH 信号且满足加权条件）"
    )
    etf_return: Mapped[float | None] = mapped_column(
        Float, comment="T+1 日实际收益率，单位 %，末日为 NULL"
    )


class BacktestIndexResultModel(Base):
    """回测每日每只指数的信号和实际收益，用于指数级回测。"""

    __tablename__ = "backtest_index_result"
    __table_args__ = (
        UniqueConstraint("backtest_id", "trade_date", "index_code", name="uq_backtest_index"),
        Index("ix_backtest_index_backtest_id", "backtest_id"),
        Index("ix_backtest_index_code", "backtest_id", "index_code"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键"
    )
    backtest_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_run.backtest_id"), nullable=False, comment="所属回测 ID"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="信号生成日期（T 日）")
    index_code: Mapped[str] = mapped_column(
        ForeignKey("benchmark_index.index_code"), nullable=False, comment="指数代码"
    )
    signal_score: Mapped[float] = mapped_column(
        Float, nullable=False, comment="信号综合得分，0-100"
    )
    signal_level: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="信号等级：HIGH/MID/LOW"
    )
    in_portfolio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, comment="是否纳入当日组合"
    )
    index_return: Mapped[float | None] = mapped_column(
        Float, comment="T+1 日指数收益率，单位 %，末日为 NULL"
    )


class IndexValuationModel(Base):
    """指数估值数据（PE/PB 及历史分位），按指数代码 + 日期唯一。

    用于构建估值类 ETF 因子（如 PE 分位、PB 分位），
    数据来源于 AkShare 的 legulegu PE/PB 接口。
    """

    __tablename__ = "index_valuation"
    __table_args__ = (UniqueConstraint("trade_date", "index_code", name="uq_index_valuation"),)

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="估值日期")
    index_code: Mapped[str] = mapped_column(
        ForeignKey("benchmark_index.index_code"),
        nullable=False,
        comment="指数代码，外键关联 benchmark_index",
    )
    pe: Mapped[float | None] = mapped_column(Float, comment="市盈率 PE(TTM)")
    pe_percentile: Mapped[float | None] = mapped_column(
        Float, comment="PE 历史分位，0-100，数值越小越低估"
    )
    pb: Mapped[float | None] = mapped_column(Float, comment="市净率 PB")
    pb_percentile: Mapped[float | None] = mapped_column(
        Float, comment="PB 历史分位，0-100，数值越小越低估"
    )
    dividend_yield: Mapped[float | None] = mapped_column(Float, comment="股息率，单位 %")
    source: Mapped[str] = mapped_column(
        String(32), default="akshare", comment="数据来源，akshare=AkShare 客户端"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="数据入库时间（UTC）"
    )


class StrategyConfigModel(Base):
    """策略配置表，存储 JSON 格式的完整策略定义。

    所有策略通过 config_json 字段的 JSON 配置驱动，
    引擎运行时动态加载，无需硬编码策略逻辑。
    """

    __tablename__ = "strategy_config"

    strategy_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="策略唯一标识，如 etf_allocation"
    )
    display_name: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="策略中文显示名称"
    )
    version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0.0", comment="策略版本号"
    )
    description: Mapped[str | None] = mapped_column(Text, comment="策略描述")
    frequency: Mapped[str] = mapped_column(
        String(32), nullable=False, default="daily", comment="运行频率：daily/weekly/monthly"
    )
    asset_scope: Mapped[str] = mapped_column(
        String(64), nullable=False, default="a_share_etf", comment="资产范围"
    )
    config_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, comment="完整策略配置 JSON，包含 score/filters/rank/portfolio/risk 等模块"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", comment="状态：active=启用, disabled=禁用"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="记录创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="记录最后更新时间（UTC）",
    )


class MacroIndicatorModel(Base):
    """宏观经济指标数据，按指标代码 + 周期唯一。

    支持 CPI（月度同比）、PMI（制造业月度）、LPR（1年/5年期），
    数据来源于 AkShare 宏观接口。
    """

    __tablename__ = "macro_indicator"
    __table_args__ = (UniqueConstraint("indicator_code", "period", name="uq_macro_indicator"),)

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    indicator_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="指标代码：cpi=CPI 同比，pmi=制造业PMI，lpr1y=LPR 1年期，lpr5y=LPR 5年期",
    )
    indicator_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="指标中文名，如 居民消费价格指数(CPI)同比"
    )
    period: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="数据周期，如 2024-01（月度）、2024-01-20（LPR 报价日）"
    )
    value: Mapped[float] = mapped_column(Float, nullable=False, comment="指标数值")
    unit: Mapped[str | None] = mapped_column(String(32), comment="单位，如 %")
    source: Mapped[str] = mapped_column(
        String(32), default="akshare", comment="数据来源，akshare=AkShare 客户端"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), comment="数据入库时间（UTC）"
    )
