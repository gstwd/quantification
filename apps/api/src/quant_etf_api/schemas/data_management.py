"""统一数据管理接口的请求与响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from quant_etf_api.schemas.types import UtcDatetime


class DataSetHealthSummary(BaseModel):
    """一个逻辑数据集的健康摘要。"""

    dataset_key: str
    display_name: str
    frequency: str
    partition_label: str | None = None
    source_label: str
    source_name: str | None = None
    supported_operations: list[str]
    health_status: str
    earliest_date: date | None = None
    latest_date: date | None = None
    expected_date: date | None = None
    record_count: int = 0
    missing_count: int = 0
    invalid_count: int = 0
    issue_summary: dict[str, Any] | None = None
    last_run_id: str | None = None
    last_run_status: str | None = None
    last_checked_at: UtcDatetime | None = None
    last_synced_at: UtcDatetime | None = None
    last_success_at: UtcDatetime | None = None


class DataPartitionHealth(DataSetHealthSummary):
    """数据集内单个可维护分区的健康快照。"""

    partition_key: str
    partition_name: str | None = None


class DataSetDetailResponse(BaseModel):
    """数据集详情及其分页分区健康状态。"""

    dataset: DataSetHealthSummary
    quality_rules: list[str]
    items: list[DataPartitionHealth]
    total: int
    offset: int
    limit: int


class DataManagementOverview(BaseModel):
    """数据管理首页总览。"""

    schedule_time: str
    datasets: list[DataSetHealthSummary]
    healthy_count: int
    warning_count: int
    error_count: int
    unknown_count: int


class DataManagementOperationRequest(BaseModel):
    """提交统一数据维护操作的请求。"""

    operation: Literal["sync_latest", "check", "repair_gaps", "rebuild"]
    dataset_key: str | None = None
    partition_key: str | None = None
    force: bool = Field(default=False, description="绕过'已是最新/未到刷新周期'节流，强制拉取上游")
    confirmation_token: str | None = Field(default=None, max_length=160)


class DataManagementOperationAccepted(BaseModel):
    """数据维护任务已入队的响应。"""

    status: str = "accepted"
    run_id: str
    run_type: str
    operation: str
