"""每日量化研究简报只读查询接口。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.daily_report import DailyReportDetail, DailyReportListResponse
from quant_etf_api.services.daily_report_service import DailyReportService

router = APIRouter(tags=["daily-reports"])


@router.get("/daily-reports", response_model=DailyReportListResponse)
def list_daily_reports(
    limit: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(get_db),
) -> DailyReportListResponse:
    """返回已发布日报摘要，按报告日期倒序排列。"""
    return DailyReportService(db).list_reports(limit=limit)


@router.get("/daily-reports/{report_date}", response_model=DailyReportDetail)
def get_daily_report(
    report_date: date,
    revision: int | None = Query(default=None, ge=1, description="不传时返回最新发布版本"),
    db: Session = Depends(get_db),
) -> DailyReportDetail:
    """返回指定交易日的最新发布版本或明确指定的历史修订。"""
    report = DailyReportService(db).get_report(report_date, revision)
    if report is None:
        raise HTTPException(status_code=404, detail=f"{report_date} 没有已发布日报")
    return report
