from __future__ import annotations
from datetime import date, datetime
from uuid import uuid4 as _uuid4

import sqlalchemy as sa
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from quant_etf_api.infra.db.base import Base, utcnow


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
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.text("TRUE"), comment="是否活跃，False=已退市/停发"
    )
    delisting_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="退市/停发日期"
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
        DateTime, default=utcnow, comment="数据入库时间（UTC）"
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
        String(64), nullable=False, comment="资源标识，如指数代码"
    )
    trade_date: Mapped[Date | None] = mapped_column(
        Date, comment="对应交易日期，NULL 表示非日频数据"
    )
    request_meta: Mapped[dict | None] = mapped_column(JSON, comment="请求元数据，如 URL、参数等")
    response_payload: Mapped[dict | None] = mapped_column(
        JSON, comment="原始响应数据，用于调试和审计"
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="数据拉取时间（UTC）"
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
        String(64), nullable=True, comment="历史遗留字段，所有内置因子均为 NULL"
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
        JSON, nullable=True, comment="依赖的数据源列表，如 ['index_bars']，由代码同步"
    )


class IndexFactorValueModel(Base):
    """指数因子值表，存储指数级别的因子计算结果。"""

    __tablename__ = "index_factor_value"
    __table_args__ = (
        UniqueConstraint(
            "trade_date",
            "index_code",
            "factor_id",
            "strategy_id",
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


class IndexSignalModel(Base):
    """指数信号表，存储策略引擎对指数的信号计算结果。"""

    __tablename__ = "index_signal"
    __table_args__ = (
        UniqueConstraint("trade_date", "index_code", "strategy_id", name="uq_index_signal"),
    )

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[Date] = mapped_column(Date, nullable=False, comment="信号对应的交易日期")
    index_code: Mapped[str] = mapped_column(
        ForeignKey("benchmark_index.index_code"),
        nullable=False,
        comment="指数代码，外键关联 benchmark_index",
    )
    strategy_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="产生该信号的策略 ID"
    )
    signal_score: Mapped[float] = mapped_column(Float, nullable=False, comment="综合得分，0-100")
    signal_level: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="信号等级：HIGH/MID/LOW"
    )
    signal_label: Mapped[str] = mapped_column(String(128), nullable=False, comment="信号中文标签")
    signal_payload: Mapped[dict | None] = mapped_column(JSON, comment="信号计算明细")
    run_id: Mapped[str | None] = mapped_column(String(64), comment="产生该信号的研究运行 ID")


class ResearchRunModel(Base):
    __tablename__ = "research_run"

    run_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="运行唯一 ID，UUID 格式"
    )
    run_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="运行类型：daily_ingest=日频入库，strategy_run=策略运行，index_refresh=指数刷新等",
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
        JSON, comment="运行结果指标，如处理标的数量、耗时等"
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败时的错误信息")
    started_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="运行开始时间（UTC）"
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
    index_code: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="处理的指数代码"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        comment="单个标的处理状态：success=成功，failed=失败，skipped=跳过",
    )
    message: Mapped[str | None] = mapped_column(Text, comment="处理结果说明或错误信息")
    metrics: Mapped[dict | None] = mapped_column(
        JSON, comment="单个标的处理指标，如因子值、信号得分等"
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
        comment='标的过滤条件，{"mode":"all"} 或 {"mode":"subset","index_codes":[...]}',
    )
    params: Mapped[dict | None] = mapped_column(JSON, comment="策略参数覆盖，NULL 表示使用默认参数")
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        comment="回测状态：pending=待执行，running=执行中，success=成功，failed=失败",
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败时的错误信息")
    metrics: Mapped[dict | None] = mapped_column(
        JSON, comment="汇总绩效指标，完成后写入，包含累计收益、最大回撤、夏普比率等"
    )
    warnings: Mapped[list | None] = mapped_column(
        JSONB,
        comment="回测执行过程中的结构化提示（level/code/message/trade_date/index_code），"
        "如预热期、因子缺失、数据缺口、部分结果等",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="回测创建时间（UTC）"
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, comment="回测开始执行时间（UTC）")
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="回测完成时间（UTC），NULL 表示未完成"
    )
    progress: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=sa.text("0"),
        comment="回测执行进度（0-100），每完成约 10% 交易日更新一次",
    )
    config_snapshot: Mapped[dict | None] = mapped_column(
        JSONB,
        comment="创建时的策略配置快照（元数据 + config_json），保证回测结果可复现",
    )
    config_hash: Mapped[str | None] = mapped_column(
        String(64),
        comment="config_json 规范化序列化的 sha256 哈希，用于前后版本比对",
    )
    data_cutoff_date: Mapped[date | None] = mapped_column(
        Date,
        comment="回测执行时行情数据截止日期，用于评估数据口径",
    )
    optimization_id: Mapped[str | None] = mapped_column(
        String(64),
        comment="关联的策略优化会话 ID（由优化 CLI 写入），普通回测为 NULL",
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
        Integer, nullable=False, comment="当日 HIGH 信号指数数量"
    )
    mid_signal_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="当日 MID 信号指数数量"
    )
    low_signal_count: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="当日 LOW 信号指数数量"
    )
    timing_regime: Mapped[str | None] = mapped_column(
        String(32), comment="择时状态：offensive/neutral/defensive（配置模式）"
    )
    total_exposure: Mapped[float | None] = mapped_column(
        Float, comment="总仓位比例，0-1（配置模式）"
    )
    cash_ratio: Mapped[float | None] = mapped_column(Float, comment="现金比例，0-1（配置模式）")
    positions: Mapped[dict | None] = mapped_column(
        JSON, comment="持仓明细，index_code → 权重（配置模式）"
    )
    missing_bar_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="当日受数据缺口影响的持仓资产数（B10）",
    )
    benchmark_return: Mapped[float | None] = mapped_column(
        Float, comment="基准指数当日收益率，单位 %"
    )
    turnover: Mapped[float | None] = mapped_column(Float, comment="当日换手率，0-1")


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
        Float, nullable=False, comment="信号综合得分，0-100（与实时 index_signal.signal_score 同义）"
    )
    signal_level: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="信号等级：HIGH/MID/LOW"
    )
    in_portfolio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, comment="信号是否纳入当日目标组合（目标权重>0）"
    )
    index_return: Mapped[float | None] = mapped_column(
        Float, comment="T+1 日指数收益率，单位 %，末日为 NULL"
    )
    target_weight: Mapped[float | None] = mapped_column(
        Float, comment="信号目标仓位权重（0-1），与实时信号 payload.target_weight 同义"
    )


class BacktestComparisonModel(Base):
    """策略对比回测主表，记录一次双策略对比回测的配置和结果。

    一次对比包含两个子回测（backtest_a / backtest_b），
    它们在同一时间区间、同一标的范围内独立执行。
    对比完成后，comparison_metrics 存储两策略各项指标的并排对比数据。
    """

    __tablename__ = "backtest_comparison"

    comparison_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="对比唯一 ID，UUID 格式"
    )
    name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="对比名称，用户可选标签"
    )
    strategy_a_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="策略 A 的 ID")
    strategy_b_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="策略 B 的 ID")
    backtest_a_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_run.backtest_id"),
        nullable=False,
        comment="策略 A 的子回测 ID",
    )
    backtest_b_id: Mapped[str] = mapped_column(
        ForeignKey("backtest_run.backtest_id"),
        nullable=False,
        comment="策略 B 的子回测 ID",
    )
    start_date: Mapped[Date] = mapped_column(
        Date, nullable=False, comment="回测起始日期（两个策略共享）"
    )
    end_date: Mapped[Date] = mapped_column(
        Date, nullable=False, comment="回测结束日期（两个策略共享）"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default=sa.text("'pending'"),
        comment="对比状态：pending/running/success/failed/partial",
    )
    params: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="共享参数（基准配置、标的范围等）"
    )
    comparison_metrics: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="对比级别汇总指标，完成后写入"
    )
    error_message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="失败/部分失败时的错误信息"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        comment="记录创建时间（UTC）",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, comment="开始执行时间（UTC）")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, comment="完成时间（UTC）")
    progress: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=sa.text("0"),
        comment="对比整体进度 0-100",
    )


class IndexValuationModel(Base):
    """指数估值数据（PE/PB 及历史分位），按指数代码 + 日期唯一。

    用于构建估值类因子（如 PE 分位、PB 分位），
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
        DateTime, default=utcnow, comment="数据入库时间（UTC）"
    )


class StrategyConfigModel(Base):
    """策略配置表，存储 JSON 格式的完整策略定义。

    所有策略通过 config_json 字段的 JSON 配置驱动，
    引擎运行时动态加载，无需硬编码策略逻辑。
    """

    __tablename__ = "strategy_config"

    strategy_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="策略唯一标识，如 index_allocation"
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
    config_json: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        comment="完整策略配置 JSON，包含 score/filters/rank/portfolio/risk 等模块",
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", comment="状态：active=启用, disabled=禁用"
    )
    is_starred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否星标关注"
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


class StrategyOptimizationModel(Base):
    """策略优化会话表，记录一次 AI 优化迭代的基线、候选与评估结果。

    候选策略以 status=draft 的 strategy_config 行存在；
    本表保存会话级元数据、评估区间、回测 ID 与绩效指标，
    支撑 Codex 等 agent 的自动优化闭环与每次迭代的优化报告。
    """

    __tablename__ = "strategy_optimization"

    optimization_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="优化会话唯一 ID"
    )
    strategy_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="基线策略 ID"
    )
    baseline_version: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="基线策略版本"
    )
    baseline_config_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="基线配置哈希"
    )
    candidate_strategy_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="候选草稿策略 ID"
    )
    candidate_version: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="候选策略版本"
    )
    candidate_config_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="候选配置哈希"
    )
    hypothesis: Mapped[str] = mapped_column(
        Text, nullable=False, comment="本轮优化的假设与改动意图"
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="running",
        comment="状态：running/evaluated/accepted/rejected/failed",
    )
    start_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="评估区间起始日期（含）"
    )
    end_date: Mapped[date] = mapped_column(
        Date, nullable=False, comment="评估区间截止日期（含）"
    )
    folds: Mapped[list | None] = mapped_column(
        JSONB, comment="验证折边界列表，元素为 {start, end} 字典"
    )
    baseline_backtest_id: Mapped[str | None] = mapped_column(
        String(64), comment="基线策略全区间回测 ID"
    )
    candidate_backtest_id: Mapped[str | None] = mapped_column(
        String(64), comment="候选策略全区间回测 ID"
    )
    fold_backtests: Mapped[list | None] = mapped_column(
        JSONB,
        comment="逐折回测 ID 列表，元素含 fold/start/end/baseline_backtest_id/candidate_backtest_id",
    )
    metrics_full: Mapped[dict | None] = mapped_column(
        JSONB, comment="全区间绩效指标 {baseline: {...}, candidate: {...}}"
    )
    metrics_folds: Mapped[list | None] = mapped_column(
        JSONB, comment="逐折绩效指标列表，元素含 fold/start/end/baseline/candidate"
    )
    fold_summary: Mapped[dict | None] = mapped_column(
        JSONB, comment="逐折聚合统计：均值/中位数/候选胜出折数"
    )
    report: Mapped[str | None] = mapped_column(
        Text, comment="最终优化报告 Markdown 全文"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="会话创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        comment="会话最后更新时间（UTC）",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="会话完成时间（UTC），未完成时为 NULL"
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
    period_date: Mapped[date | None] = mapped_column(
        Date, nullable=True, comment="标准化周期日期，CPI/PMI 取当月首日，LPR 取报价日"
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="数据入库时间（UTC）"
    )


class TradingCalendarModel(Base):
    """A 股交易日历表，记录每个日期是否为交易日。

    数据来源：AkShare tool_trade_date_hist_sina()，
    用于替代周末判断，支持正确识别节假日和调休。
    """

    __tablename__ = "trading_calendar"

    trade_date: Mapped[Date] = mapped_column(Date, primary_key=True, comment="日期")
    is_trading_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否为交易日"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="记录创建时间（UTC）"
    )


# ============================================================================
# AI 因子相关表（ai_factors 层的数据持久化）
# ============================================================================


class NewsItemModel(Base):
    """原始新闻存储表。

    存储从 NewsNow API / RSS 采集的原始新闻条目，
    每日去重后供 AI 分析使用。
    """

    __tablename__ = "news_item"
    __table_args__ = (
        UniqueConstraint("source_id", "title", "crawl_date", name="uq_news_source_title_date"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(_uuid4()), comment="新闻唯一标识（UUID）"
    )
    source_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="来源平台 ID，如 toutiao/baidu"
    )
    source_name: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="来源平台中文名"
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="新闻标题（已清洗）")
    url: Mapped[str | None] = mapped_column(Text, nullable=True, comment="新闻链接（已规范化）")
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="热榜排名（1=榜首）")
    crawl_date: Mapped[date] = mapped_column(Date, nullable=False, comment="采集日期")
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="首次上榜时间"
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="最后上榜时间"
    )
    appear_count: Mapped[int] = mapped_column(Integer, default=1, comment="出现次数")
    raw_payload: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="原始 API 返回数据"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="记录创建时间（UTC）"
    )


class AISentimentResultModel(Base):
    """AI 情绪分析结果表。

    存储 LLM 对每条新闻的情绪分析结果，
    包括情绪分、关注度、相关度、主题标签和资产关联。
    """

    __tablename__ = "ai_sentiment_result"
    __table_args__ = (UniqueConstraint("news_id", name="uq_ai_sentiment_news"),)

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(_uuid4()),
        comment="分析结果唯一标识（UUID）",
    )
    news_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("news_item.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联的原始新闻 ID",
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="关联交易日")
    asset_tags: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="资产标签 JSON 数组（指数代码/行业/概念）"
    )
    topics: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="主题标签 JSON 数组")
    sentiment_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="情绪分 [-1.0, 1.0]"
    )
    attention_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="关注度分 [0, 100]"
    )
    relevance_score: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="A 股市场相关度 [0, 1]"
    )
    summary: Mapped[str | None] = mapped_column(String(256), nullable=True, comment="AI 生成摘要")
    llm_model: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="使用的 LLM 模型标识"
    )
    llm_response: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="LLM 完整响应（调试用）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="记录创建时间（UTC）"
    )


class DailySentimentAggregateModel(Base):
    """每日情绪聚合表。

    按 asset_tag 对当日所有 AI 分析结果聚合生成，
    每条记录对应一个资产标签在某一交易日的汇总数据。
    该表的聚合数据通过 FactorService 加载到 FactorContext，
    供 AI 因子计算器使用。
    """

    __tablename__ = "daily_sentiment_aggregate"
    __table_args__ = (
        UniqueConstraint("trade_date", "asset_tag", name="uq_daily_sentiment_date_tag"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键"
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    asset_tag: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="资产标签（指数代码 或 行业名）"
    )
    avg_sentiment: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="算术平均情绪分"
    )
    weighted_sentiment: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="关注度加权情绪分"
    )
    total_attention: Mapped[float | None] = mapped_column(Float, nullable=True, comment="总关注度")
    news_count: Mapped[int] = mapped_column(Integer, default=0, comment="相关新闻数量")
    top_topics: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="Top 主题 JSON 数组"
    )
    positive_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="正面新闻占比 [0, 1]"
    )
    negative_ratio: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="负面新闻占比 [0, 1]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="记录创建时间（UTC）"
    )


class MarketSynthesisModel(Base):
    """每日市场综合研判表。

    由 MarketSynthesisAnalyzer 调用 LLM 生成，综合当日各指数情绪
    聚合数据和热门主题，输出一份 200-300 字的中文市场概况。
    每天最多一条记录。
    """

    __tablename__ = "market_synthesis"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(_uuid4()), comment="UUID 主键"
    )
    trade_date: Mapped[date] = mapped_column(
        Date, unique=True, nullable=False, comment="交易日，每天一条综合研判"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="200-300 字中文市场研判正文"
    )
    sentiment_summary: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, comment="关键指数情绪摘要"
    )
    key_topics: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, comment="Top 5-8 市场主题"
    )
    risk_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="风险提示，来自 AI 分析"
    )
    llm_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="使用的 LLM 模型标识"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="记录创建时间（UTC）"
    )


class KeywordTagConfigModel(Base):
    """关键词→资产标签映射配置表。

    替代硬编码的 _KEYWORD_TAG_MAP，支持通过 API/前端动态管理。
    分类器优先使用此表中的活跃映射，DB 无数据时回退到静态默认值。
    """

    __tablename__ = "keyword_tag_config"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="自增主键"
    )
    keyword: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, comment="匹配关键词"
    )
    tag: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="映射到的资产标签"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="是否启用"
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="优先级（越大越先匹配）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, comment="创建时间（UTC）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, comment="更新时间（UTC）"
    )


class BackgroundJobModel(Base):
    """后台任务队列表。

    所有后台任务（数据摄取、因子计算、回测、对比回测、AI 分析、补数等）
    统一通过本表入队，由固定 worker 线程池认领执行。
    job_key 在 pending/running 状态下唯一，用于幂等去重。
    """

    __tablename__ = "background_job"
    __table_args__ = (
        Index(
            "uq_background_job_active_key",
            "job_key",
            unique=True,
            postgresql_where=sa.text("status IN ('pending', 'running')"),
        ),
    )

    job_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="任务唯一 ID，UUID 格式"
    )
    job_type: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="任务类型：daily_ingest/backtest/data_fill 等"
    )
    job_key: Mapped[str | None] = mapped_column(
        String(256), comment="去重键，pending/running 状态下唯一"
    )
    payload: Mapped[dict | None] = mapped_column(JSON, comment="任务参数，JSON 格式")
    status: Mapped[str] = mapped_column(
        String(32),
        default="pending",
        server_default=sa.text("'pending'"),
        comment="任务状态：pending=待执行，running=执行中，success=成功，failed=失败",
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=sa.text("0"),
        comment="优先级（越大越先执行）",
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default=sa.text("0"),
        comment="已尝试次数",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=sa.text("1"),
        comment="最大尝试次数",
    )
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败时的错误信息")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=sa.func.now(), comment="创建时间（UTC）"
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="开始执行时间（UTC）"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="完成时间（UTC）"
    )
