from __future__ import annotations
from datetime import date

from pydantic import BaseModel

from quant_etf_api.schemas.run import ResearchRunSummary
from quant_etf_api.schemas.types import UtcDatetime


class DataSourceSnapshot(BaseModel):
    """单个数据表/数据源的快照信息。

    包含记录总数、最新数据日期和最近一次入库时间，
    供前端"数据源状态"区域展示各表新鲜度。
    超大表的记录总数取自数据库统计信息估算（避免全表扫描），
    与精确值可能存在 1% 以内的偏差。
    """

    source_name: str  # 展示名称，如 "AkShare 指数日线"
    table_name: str  # 数据库表名，如 "index_daily_bar"
    record_count: int  # 该表记录总数（超大表为统计估算值）
    latest_trade_date: date | None  # 该表最新交易日期
    latest_ingested_at: UtcDatetime | None  # 该表最晚入库时间


class SystemStatusResponse(BaseModel):
    """系统运行状态完整响应。

    包含数据概览统计、各数据表快照、最近运行记录和平台配置，
    供前端"数据状态"页面渲染。
    """

    active_index_count: int  # 活跃指数数量
    latest_trade_date: date | None  # 全局最新交易日（取各表最大值）
    data_sources: list[DataSourceSnapshot]  # 各数据表快照
    recent_runs: list[ResearchRunSummary]  # 最近运行记录（最多 5 条）
    db_connected: bool  # 数据库连接是否正常
