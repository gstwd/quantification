"""市场日志模块 API 路由。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.journal import (
    AIAnalysisResponse,
    CalendarResponse,
    IndexSnapshotRow,
    JournalEntryCreate,
    JournalEntryDetail,
    JournalEntrySummary,
    JournalEntryUpdate,
    ObservationRow,
    ObservationsBatchUpdate,
    SetTagsRequest,
    TagCreate,
    TagSummary,
    TagUpdate,
)
from quant_etf_api.schemas.pagination import PaginatedResponse
from quant_etf_api.services.journal_service import JournalService

router = APIRouter(tags=["journal"])


# =============================================================================
# 日历
# =============================================================================


@router.get("/journal/calendar", response_model=CalendarResponse)
def get_calendar(
    year: int = Query(..., ge=2000, le=2100, description="年份"),
    month: int | None = Query(default=None, ge=1, le=12, description="月份（1-12），不传返回全年"),
    db: Session = Depends(get_db),
) -> CalendarResponse:
    """获取指定年/月的日历视图数据，包含交易日标记和已有日志摘要。"""
    return JournalService(db).get_calendar(year=year, month=month)


# =============================================================================
# 日志 CRUD
# =============================================================================


@router.get("/journal/entries", response_model=PaginatedResponse[JournalEntrySummary])
def list_entries(
    date_from: date | None = Query(default=None, description="起始日期（含）"),
    date_to: date | None = Query(default=None, description="结束日期（含）"),
    tag: str | None = Query(default=None, description="按标签 ID 过滤"),
    phase: str | None = Query(default=None, description="按市场阶段过滤"),
    is_complete: bool | None = Query(default=None, description="按完成状态过滤"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse[JournalEntrySummary]:
    """分页查询日志列表，支持多种筛选条件。"""
    items, total = JournalService(db).list_entries(
        date_from=date_from,
        date_to=date_to,
        tag_id=tag,
        phase=phase,
        is_complete=is_complete,
        offset=offset,
        limit=limit,
    )
    return PaginatedResponse(items=items, total=total, offset=offset, limit=limit)


@router.post("/journal/entries", response_model=JournalEntryDetail, status_code=201)
def create_entry(
    req: JournalEntryCreate,
    db: Session = Depends(get_db),
) -> JournalEntryDetail:
    """创建一条新的日志记录，自动填充指数行情快照和空观察分区。"""
    try:
        return JournalService(db).create_entry(req)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/journal/entries/by-date", response_model=JournalEntryDetail)
def get_entry_by_date(
    trade_date: date = Query(..., description="交易日期"),
    db: Session = Depends(get_db),
) -> JournalEntryDetail:
    """按交易日期查询日志详情。"""
    entry = JournalService(db).get_entry_by_date(trade_date)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"日期 {trade_date} 的日志不存在")
    return entry


@router.get("/journal/entries/{entry_id}", response_model=JournalEntryDetail)
def get_entry(
    entry_id: str,
    db: Session = Depends(get_db),
) -> JournalEntryDetail:
    """按 ID 查询日志完整详情。"""
    entry = JournalService(db).get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="日志不存在")
    return entry


@router.put("/journal/entries/{entry_id}", response_model=JournalEntryDetail)
def update_entry(
    entry_id: str,
    data: JournalEntryUpdate,
    db: Session = Depends(get_db),
) -> JournalEntryDetail:
    """更新日志内容（仅更新传入的字段）。"""
    try:
        entry = JournalService(db).update_entry(entry_id, data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if entry is None:
        raise HTTPException(status_code=404, detail="日志不存在")
    return entry


@router.delete("/journal/entries/{entry_id}", status_code=204)
def delete_entry(
    entry_id: str,
    db: Session = Depends(get_db),
) -> None:
    """删除日志（CASCADE 自动清理关联数据）。"""
    if not JournalService(db).delete_entry(entry_id):
        raise HTTPException(status_code=404, detail="日志不存在")


# =============================================================================
# 快照
# =============================================================================


@router.post("/journal/entries/{entry_id}/snapshot/refresh", response_model=list[IndexSnapshotRow])
def refresh_snapshots(
    entry_id: str,
    db: Session = Depends(get_db),
) -> list[IndexSnapshotRow]:
    """刷新指定日志的指数快照数据。"""
    try:
        return JournalService(db).refresh_snapshots(entry_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# =============================================================================
# 观察分区
# =============================================================================


@router.put("/journal/entries/{entry_id}/observations", response_model=list[ObservationRow])
def save_observations(
    entry_id: str,
    data: ObservationsBatchUpdate,
    db: Session = Depends(get_db),
) -> list[ObservationRow]:
    """批量保存观察分区内容。"""
    return JournalService(db).save_observations(entry_id, data)


# =============================================================================
# 标签
# =============================================================================


@router.get("/journal/tags", response_model=list[TagSummary])
def list_tags(
    db: Session = Depends(get_db),
) -> list[TagSummary]:
    """获取所有标签列表（按使用次数降序）。"""
    return JournalService(db).list_tags()


@router.post("/journal/tags", response_model=TagSummary, status_code=201)
def create_tag(
    data: TagCreate,
    db: Session = Depends(get_db),
) -> TagSummary:
    """创建新标签。"""
    try:
        return JournalService(db).create_tag(data)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/journal/tags/{tag_id}", response_model=TagSummary)
def update_tag(
    tag_id: str,
    data: TagUpdate,
    db: Session = Depends(get_db),
) -> TagSummary:
    """更新标签信息。"""
    tag = JournalService(db).update_tag(tag_id, data)
    if tag is None:
        raise HTTPException(status_code=404, detail="标签不存在")
    return tag


@router.delete("/journal/tags/{tag_id}", status_code=204)
def delete_tag(
    tag_id: str,
    db: Session = Depends(get_db),
) -> None:
    """删除标签（系统预设标签不可删除）。"""
    try:
        if not JournalService(db).delete_tag(tag_id):
            raise HTTPException(status_code=404, detail="标签不存在")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# =============================================================================
# 日志标签关联
# =============================================================================


@router.put("/journal/entries/{entry_id}/tags", response_model=list[TagSummary])
def set_entry_tags(
    entry_id: str,
    data: SetTagsRequest,
    db: Session = Depends(get_db),
) -> list[TagSummary]:
    """设置日志的标签（全量替换，最多 10 个）。"""
    try:
        return JournalService(db).set_entry_tags(entry_id, data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# =============================================================================
# AI 分析（Phase 1 占位）
# =============================================================================


@router.post("/journal/entries/{entry_id}/ai-analysis", status_code=202)
def trigger_ai_analysis(
    entry_id: str,
    db: Session = Depends(get_db),
) -> dict:
    """触发 AI 分析（Phase 1 占位）。"""
    try:
        return JournalService(db).trigger_ai_analysis(entry_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/journal/entries/{entry_id}/ai-analysis", response_model=AIAnalysisResponse)
def get_ai_analysis(
    entry_id: str,
    db: Session = Depends(get_db),
) -> AIAnalysisResponse:
    """获取 AI 分析结果。"""
    result = JournalService(db).get_ai_analysis(entry_id)
    if result is None:
        raise HTTPException(status_code=404, detail="AI 分析结果不存在")
    return result
