"""申万行业轮动子系统只读研究端点（调试页数据源）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.domain.industry.constants import normalize_sw_code
from quant_etf_api.engine.config import RotationConfig
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryUniverseRepository,
)
from quant_etf_api.schemas.market_data import DailyBar
from quant_etf_api.schemas.industry import (
    IndustryDiffusionResponse,
    IndustryIndexSummary,
    IndustryQualityDetail,
    IndustryRotationResponse,
    IndustryRRGPoint,
    IndustryRRGResponse,
    IndustrySummaryItem,
)
from quant_etf_api.services.industry_factor_service import IndustryFactorService
from quant_etf_api.services.industry_data_service import IndustryDataService
from quant_etf_api.services.industry_rotation_service import IndustryRotationService

router = APIRouter(prefix="/industry", tags=["industry"])


def _num(value: Any) -> float | None:
    """把 pandas/None/NaN 统一清洗为 float|None。"""
    if value is None:
        return None
    try:
        if value != value:
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def _split_codes(raw: str | None) -> list[str] | None:
    """把逗号分隔参数解析为行业代码列表。"""
    if not raw:
        return None
    return [normalize_sw_code(part.strip()) for part in raw.split(",") if part.strip()]


def _validate_range(start: date, end: date) -> None:
    """校验日期区间合法性。"""
    if start > end:
        raise HTTPException(status_code=422, detail="start 不能晚于 end")


@router.get("/indexes", response_model=list[IndustryIndexSummary])
def list_industry_indexes(
    db: Session = Depends(get_db),
) -> list[IndustryIndexSummary]:
    """列出申万一级行业目录。"""
    repo = IndustryUniverseRepository(db)
    return [
        IndustryIndexSummary(
            industry_code=row.industry_code,
            name_cn=row.name_cn,
            is_benchmark_excluded=row.is_benchmark_excluded,
        )
        for row in repo.find_all_active()
    ]


@router.get("/summary", response_model=list[IndustrySummaryItem])
def industry_summary(db: Session = Depends(get_db)) -> list[IndustrySummaryItem]:
    """行业数据管理列表（目录 + 成分数 + 质量快照 + 最新行情）。"""
    return IndustryDataService(db).list_summary()


@router.get("/indexes/{industry_code}/bars", response_model=list[DailyBar])
def industry_daily_bars(
    industry_code: str,
    start_date: date | None = Query(default=None, description="起始日期"),
    end_date: date | None = Query(default=None, description="结束日期"),
    limit: int = Query(default=250, ge=1, le=2000, description="返回最近 N 行"),
    db: Session = Depends(get_db),
) -> list[DailyBar]:
    """查询单个申万一级行业指数日线（K 线数据源）。"""
    code = normalize_sw_code(industry_code)
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date 不能晚于 end_date")
    repo = IndustryDailyBarRepository(db)
    if start_date is not None and end_date is not None:
        rows = repo.find_range(start_date, end_date, [code])
    else:
        rows = repo.find_by_code(code)
    rows = rows[-limit:] if limit and len(rows) > limit else rows
    return [
        DailyBar(
            trade_date=row.trade_date,
            code=row.industry_code,
            open_price=row.open_price,
            high_price=row.high_price,
            low_price=row.low_price,
            close_price=row.close_price,
            prev_close_price=row.prev_close_price,
            change_pct=row.change_pct,
            volume=row.volume,
            turnover=row.turnover,
            source=row.source,
            ingested_at=row.ingested_at,
        )
        for row in rows
    ]


@router.get("/indexes/{industry_code}/quality", response_model=IndustryQualityDetail)
def industry_quality_detail(
    industry_code: str,
    db: Session = Depends(get_db),
) -> IndustryQualityDetail:
    """行业详情页数据质量（快照 + OHLC/change_pct 字段完整性）。"""
    code = normalize_sw_code(industry_code)
    try:
        return IndustryDataService(db).industry_quality_detail(code)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/rrg", response_model=IndustryRRGResponse)
def get_rrg_series(
    start: date = Query(..., description="起始日期"),
    end: date = Query(..., description="截止日期"),
    industry_codes: str | None = Query(default=None, description="逗号分隔行业代码"),
    lookback_ratio: int = Query(default=220, ge=1),
    lookback_mom: int = Query(default=60, ge=1),
    smooth_window: int = Query(default=20, ge=1),
    db: Session = Depends(get_db),
) -> IndustryRRGResponse:
    """即时计算 RRG 序列（warm-up 由服务端自动前移）。"""
    _validate_range(start, end)
    service = IndustryFactorService(db)
    try:
        panels = service.build_panels(
            start=start,
            end=end,
            industry_codes=_split_codes(industry_codes),
            lookback_ratio=lookback_ratio,
            lookback_mom=lookback_mom,
            smooth_window=smooth_window,
            need_diffusion=False,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    names = service.industry_names()
    ratio = panels["rs_ratio"]
    momentum = panels["rs_momentum"]
    quadrant = panels["quadrant"]
    points: list[IndustryRRGPoint] = []
    codes = panels["industry_codes"]
    for day in ratio.index:
        for code in codes:
            if code not in ratio.columns or code not in momentum.columns:
                continue
            r = _num(ratio.loc[day, code]) if code in ratio.columns else None
            m = _num(momentum.loc[day, code]) if code in momentum.columns else None
            q = _num(quadrant.loc[day, code]) if code in quadrant.columns else None
            points.append(
                IndustryRRGPoint(
                    trade_date=day.date().isoformat(),
                    industry_code=code,
                    name_cn=names.get(code, ""),
                    rs_ratio=None if r is None else round(r, 4),
                    rs_momentum=None if m is None else round(m, 4),
                    quadrant=None if q is None else int(q),
                )
            )
    return IndustryRRGResponse(
        points=points,
        warmup_days=lookback_ratio + lookback_mom + 2 * (smooth_window - 1),
    )


@router.get("/diffusion", response_model=IndustryDiffusionResponse)
def get_diffusion_series(
    start: date = Query(..., description="起始日期"),
    end: date = Query(..., description="截止日期"),
    industry_codes: str | None = Query(default=None, description="逗号分隔行业代码"),
    diffusion_lookback: int = Query(default=220, ge=1),
    smooth_window: int = Query(default=20, ge=1),
    db: Session = Depends(get_db),
) -> IndustryDiffusionResponse:
    """即时计算数量占比扩散序列。"""
    _validate_range(start, end)
    service = IndustryFactorService(db)
    try:
        panels = service.build_panels(
            start=start,
            end=end,
            industry_codes=_split_codes(industry_codes),
            smooth_window=smooth_window,
            diffusion_lookback=diffusion_lookback,
            need_rrg=False,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    names = service.industry_names()
    diffusion = panels["diffusion"]
    points = []
    codes = panels["industry_codes"]
    for day in diffusion.index:
        for code in codes:
            if code not in diffusion.columns:
                continue
            value = _num(diffusion.loc[day, code])
            points.append(
                {
                    "trade_date": day.date().isoformat(),
                    "industry_code": code,
                    "name_cn": names.get(code, ""),
                    "value": None if value is None else round(value, 6),
                }
            )
    return IndustryDiffusionResponse(points=points)


@router.get("/rotation", response_model=IndustryRotationResponse)
def get_rotation_selections(
    start: date = Query(..., description="起始日期"),
    end: date = Query(..., description="截止日期"),
    signal: str = Query(default="diffusion_rrg"),
    top_n: int = Query(default=6, ge=1),
    keep_quadrants: str = Query(default="1,2", description="逗号分隔象限"),
    industry_codes: str | None = Query(default=None, description="逗号分隔行业代码"),
    lookback_ratio: int = Query(default=220, ge=1),
    lookback_mom: int = Query(default=60, ge=1),
    smooth_window: int = Query(default=20, ge=1),
    diffusion_lookback: int = Query(default=220, ge=1),
    monthly: bool = Query(default=True, description="True=月末决策，False=逐日决策"),
    db: Session = Depends(get_db),
) -> IndustryRotationResponse:
    """按研报信号规则输出轮动选择（默认月末决策）。"""
    _validate_range(start, end)
    try:
        quadrants = [int(p) for p in keep_quadrants.split(",") if p.strip()]
        rotation = RotationConfig(
            signal=signal,
            top_n=top_n,
            keep_quadrants=quadrants,
            lookback_ratio=lookback_ratio,
            lookback_mom=lookback_mom,
            smooth_window=smooth_window,
            diffusion_lookback=diffusion_lookback,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        result = IndustryRotationService(db).analyze_selections(
            start=start,
            end=end,
            rotation=rotation,
            industry_codes=_split_codes(industry_codes),
            monthly=monthly,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    selections = [
        {
            "trade_date": item["trade_date"],
            "selected_codes": item["selected_codes"],
            "weights": item["weights"],
        }
        for item in result["selections"]
    ]
    return IndustryRotationResponse(selections=selections)
