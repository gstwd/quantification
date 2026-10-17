"""量价 / 择时研报研究台只读端点（因子中心研究页数据源）。"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.signal_lab import (
    SignalLabCatalog,
    SignalLabCrossSectionResponse,
    SignalLabTimingResponse,
)
from quant_etf_api.services.signal_lab_service import (
    SignalLabError,
    SignalLabNotFound,
    SignalLabService,
)

router = APIRouter(prefix="/signal-lab", tags=["signal-lab"])


def _parse_params(raw: str | None) -> dict[str, Any]:
    """解析 JSON 形式的参数查询串。

    Raises:
        HTTPException: 非法 JSON 或非对象时返回 422。
    """
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="params 必须是 JSON 对象") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="params 必须是 JSON 对象")
    return data


def _split_codes(raw: str | None) -> list[str] | None:
    """解析逗号分隔的指数代码。"""
    if not raw:
        return None
    codes = [c.strip() for c in raw.split(",") if c.strip()]
    return codes or None


@router.get("/catalog", response_model=SignalLabCatalog)
def get_catalog(db: Session = Depends(get_db)) -> SignalLabCatalog:
    """返回研究台可研究的因子、信号、指数与研究期边界。"""
    return SignalLabService(db).catalog()


@router.get("/timing", response_model=SignalLabTimingResponse)
def get_timing(
    index_code: str = Query(..., min_length=1, description="指数代码"),
    signal: str = Query(..., min_length=1, description="择时信号键"),
    start: date = Query(..., description="起始日期"),
    end: date = Query(..., description="截止日期"),
    params: str | None = Query(default=None, description="JSON 形式的信号参数"),
    db: Session = Depends(get_db),
) -> SignalLabTimingResponse:
    """即时计算单指数择时信号、中间分量与评估指标（只读）。"""
    parsed = _parse_params(params)
    try:
        return SignalLabService(db).timing(index_code, signal, start, end, parsed)
    except SignalLabNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SignalLabError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/market", response_model=SignalLabTimingResponse)
def get_market_signal(
    signal: str = Query(default="gsisi", description="市场级信号键"),
    proxy_code: str = Query(default="000300", description="代理指数代码"),
    start: date = Query(..., description="起始日期"),
    end: date = Query(..., description="截止日期"),
    params: str | None = Query(default=None, description="JSON 形式的信号参数"),
    db: Session = Depends(get_db),
) -> SignalLabTimingResponse:
    """即时计算市场级信号（GSISI）并在代理指数上评估（只读）。"""
    parsed = _parse_params(params)
    try:
        return SignalLabService(db).market(signal, proxy_code, start, end, parsed)
    except SignalLabNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SignalLabError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/cross-section", response_model=SignalLabCrossSectionResponse)
def get_cross_section(
    factor: str = Query(..., min_length=1, description="截面因子键"),
    start: date = Query(..., description="起始日期"),
    end: date = Query(..., description="截止日期"),
    horizon: int = Query(default=5, ge=1, le=60, description="未来收益窗口（交易日）"),
    index_codes: str | None = Query(default=None, description="逗号分隔指数代码，默认全部活跃指数"),
    params: str | None = Query(default=None, description="JSON 形式的因子参数"),
    db: Session = Depends(get_db),
) -> SignalLabCrossSectionResponse:
    """即时计算截面因子的周频 Rank IC、三分组与相关性（只读）。"""
    parsed = _parse_params(params)
    try:
        return SignalLabService(db).cross_section(
            factor, start, end, horizon, _split_codes(index_codes), parsed
        )
    except SignalLabError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
