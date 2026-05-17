from datetime import date, datetime

from pydantic import BaseModel

from quant_etf_api.schemas.run import ResearchRunSummary


class DataFreshnessItem(BaseModel):
    """单个 ETF 或指数的数据新鲜度。"""

    code: str
    name: str
    latest_date: date | None = None
    is_stale: bool = False


class DataFreshnessGroup(BaseModel):
    """数据表维度的新鲜度汇总。"""

    total: int
    up_to_date: int
    stale: list[DataFreshnessItem] = []
    missing: list[DataFreshnessItem] = []
    latest_date: date | None = None


class DataQualityResponse(BaseModel):
    """数据质量总览。"""

    etf_bars: DataFreshnessGroup
    etf_shares: DataFreshnessGroup
    index_bars: DataFreshnessGroup
    index_valuation: DataFreshnessGroup
    checked_at: datetime


class DataSourceSnapshot(BaseModel):
    """单个数据表/数据源的快照信息。

    包含记录总数、最新数据日期和最近一次入库时间，
    供前端"数据源状态"区域展示各表新鲜度。
    """

    source_name: str  # 展示名称，如 "腾讯日线行情"
    table_name: str  # 数据库表名，如 "etf_daily_bar"
    record_count: int  # 该表记录总数
    latest_trade_date: date | None  # 该表最新交易日期
    latest_ingested_at: datetime | None  # 该表最晚入库时间


class SystemStatusResponse(BaseModel):
    """系统运行状态完整响应。

    包含数据概览统计、各数据表快照、最近运行记录和平台配置，
    供前端"数据状态"页面渲染。
    """

    active_etf_count: int  # 活跃 ETF 数量
    latest_trade_date: date | None  # 全局最新交易日（取各表最大值）
    data_sources: list[DataSourceSnapshot]  # 各数据表快照
    recent_runs: list[ResearchRunSummary]  # 最近运行记录（最多 5 条）
    asset_scope: str  # 资产范围，固定 "a_share_etf"
    frequency: str  # 数据频率，固定 "daily"
    database: str  # 数据库类型，固定 "postgresql"
    db_connected: bool  # 数据库连接是否正常
