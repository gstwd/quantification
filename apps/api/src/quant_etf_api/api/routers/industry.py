"""申万行业轮动子系统只读研究端点（调试页数据源）。"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
import pandas as pd
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.domain.industry.constants import (
    DIFFUSION_LAB_MAX_RANGE_DAYS,
    RRG_LAB_MAX_RANGE_DAYS,
    normalize_sw_code,
)
from quant_etf_api.domain.industry.correlation import compute_industry_index_correlations
from quant_etf_api.domain.industry.selection import IndustrySelectionConfig
from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.builtins.index_panel_factors import IndexDiffusionRatioComputer
from quant_etf_api.infra.db.repositories.industry import (
    IndustryDailyBarRepository,
    IndustryUniverseRepository,
)
from quant_etf_api.infra.db.repositories.benchmark_index import BenchmarkIndexRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.schemas.market_data import DailyBar
from quant_etf_api.schemas.industry import (
    IndustryDiffusionMeta,
    IndustryDiffusionPoint,
    IndustryDiffusionResponse,
    IndexDiffusionDebugResponse,
    IndustryIndexSummary,
    IndustryIndexCorrelationItem,
    IndustryIndexCorrelationResponse,
    IndustryQualityDetail,
    IndustryRRGMeta,
    IndustryRRGPoint,
    IndustryRRGResponse,
    IndustryRotationResponse,
    IndustrySummaryItem,
)
from quant_etf_api.services.industry_factor_service import IndustryFactorService
from quant_etf_api.services.industry_data_service import IndustryDataService
from quant_etf_api.services.industry_rotation_service import IndustryRotationService
from quant_etf_api.services.index_factor_panel_service import IndexFactorPanelService

router = APIRouter(prefix="/industry", tags=["industry"])
logger = logging.getLogger("quant_etf_api.api.industry")

# 慢计算告警阈值：超过该秒数的 RRG/扩散请求输出 warning，便于与前端超时对应
_COMPUTE_SLOW_SECONDS = 15.0


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


def _validate_max_days(start: date, end: date, max_days: int, factor_label: str) -> None:
    """限制调试研究单次查询的自然日跨度，防止响应体与计算窗口失控。"""
    span_days = (end - start).days + 1
    if span_days > max_days:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{factor_label}单次查询最多支持 {max_days} 自然日"
                f"（当前 {span_days} 天），请缩短日期区间后重试"
            ),
        )


def _log_compute_start(
    *,
    factor_label: str,
    start: date,
    end: date,
    codes: list[str] | None,
    params: dict[str, Any],
) -> float:
    """打印计算开始日志并返回计时起点。"""
    started = time.perf_counter()
    logger.info(
        "%s 即时计算开始 start=%s end=%s range_days=%d industry_count=%s params=%s",
        factor_label,
        start.isoformat(),
        end.isoformat(),
        (end - start).days + 1,
        "全部" if not codes else len(codes),
        params,
    )
    return started


def _log_compute_done(
    *,
    factor_label: str,
    started: float,
    rows: int,
    issue_count: int,
) -> None:
    """打印计算完成与耗时；超过慢阈值时用 warning 提示。"""
    elapsed = time.perf_counter() - started
    log_fn = logger.warning if elapsed >= _COMPUTE_SLOW_SECONDS else logger.info
    log_fn(
        "%s 即时计算完成 rows=%d meta_issues=%d elapsed_ms=%.0f%s",
        factor_label,
        rows,
        issue_count,
        elapsed * 1000,
        "（超过 15s，前端可能触发超时，建议缩短区间）"
        if elapsed >= _COMPUTE_SLOW_SECONDS
        else "",
    )


def _issue_items(raw_issues: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """把服务层问题字典清洗为可序列化结构。"""
    if not raw_issues:
        return []
    return [
        {
            "level": str(item.get("level", "warn")),
            "code": str(item.get("code", "")),
            "message": str(item.get("message", "")),
            "industry_codes": list(item.get("industry_codes", [])),
            "count": item.get("count"),
            "sample_dates": [str(d) for d in item.get("sample_dates", [])],
        }
        for item in raw_issues
    ]


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


@router.get("/correlation", response_model=IndustryIndexCorrelationResponse)
def get_industry_index_correlation(
    index_code: str = Query(..., min_length=1, description="待比较的活跃指数代码"),
    start: date = Query(..., description="起始日期"),
    end: date = Query(..., description="截止日期"),
    industry_codes: str | None = Query(default=None, description="逗号分隔行业代码"),
    db: Session = Depends(get_db),
) -> IndustryIndexCorrelationResponse:
    """计算指定指数与行业的严格日收益 Pearson 相关度。

    只读取已入库的指数与行业日线。缺少收盘价不会前填，且会使该资产紧随的
    收益也失效，避免将跨停牌或数据断档的价格变化混入相关度样本。
    """
    _validate_range(start, end)
    _validate_max_days(start, end, RRG_LAB_MAX_RANGE_DAYS, "行业相关度")
    index = BenchmarkIndexRepository(db).find_by_code(index_code)
    if index is None or not index.is_active:
        raise HTTPException(status_code=404, detail=f"活跃指数 {index_code} 不存在")

    universe_repo = IndustryUniverseRepository(db)
    requested_codes = _split_codes(industry_codes)
    codes = universe_repo.find_active_codes(requested_codes)
    if not codes:
        raise HTTPException(status_code=422, detail="未选择有效行业")
    index_bars = IndexDailyBarRepository(db).find_by_code_date_range(index_code, start, end)
    industry_bars = IndustryDailyBarRepository(db).find_range(start, end, codes)
    index_close = pd.Series(
        {row.trade_date: row.close_price for row in index_bars if row.close_price is not None},
        dtype=float,
    )
    industry_close = pd.DataFrame(
        [
            {
                "trade_date": row.trade_date,
                "industry_code": row.industry_code,
                "close_price": row.close_price,
            }
            for row in industry_bars
            if row.close_price is not None
        ]
    )
    if industry_close.empty:
        close_panel = pd.DataFrame(columns=codes, dtype=float)
    else:
        close_panel = industry_close.pivot(
            index="trade_date", columns="industry_code", values="close_price"
        ).reindex(columns=codes)
    correlations = compute_industry_index_correlations(index_close, close_panel)
    names = {row.industry_code: row.name_cn for row in universe_repo.find_by_codes(codes)}
    items = [
        IndustryIndexCorrelationItem(
            industry_code=code,
            name_cn=names.get(code, ""),
            correlation=_num(correlations.loc[code, "correlation"]),
            sample_count=int(correlations.loc[code, "sample_count"]),
        )
        for code in codes
    ]
    items.sort(
        key=lambda item: (
            item.correlation is None,
            -(item.correlation if item.correlation is not None else 0.0),
        )
    )
    return IndustryIndexCorrelationResponse(
        index_code=index_code,
        start=start,
        end=end,
        index_close_days=len(index_close),
        items=items,
    )


@router.get("/index-diffusion", response_model=IndexDiffusionDebugResponse)
def get_index_diffusion_debug(
    index_code: str = Query(..., min_length=1, description="待计算的活跃指数代码"),
    trade_date: date = Query(..., description="目标交易日"),
    db: Session = Depends(get_db),
) -> IndexDiffusionDebugResponse:
    """即时计算指定指数在一个交易日的严格成分扩散结果。

    此端点仅供研究调试页使用：读取已入库的指数交易日、成分事件与个股收盘，
    不写入 ``index_factor_value``，也不参与实时策略计算。计算轴截取目标日前
    最多截取目标日前 240 个已入库交易日；当数据不足或 20 日平滑窗口存在空值时因子值返回 NULL。

    Args:
        index_code: 活跃指数代码。
        trade_date: 目标交易日。
        db: SQLAlchemy 同步 Session。

    Returns:
        原始上涨占比、平滑因子值和有效/缺失样本诊断。

    Raises:
        HTTPException: 指数不存在或目标日不是该指数交易日时抛出。
    """
    index = BenchmarkIndexRepository(db).find_by_code(index_code)
    if index is None or not index.is_active:
        raise HTTPException(status_code=404, detail=f"活跃指数 {index_code} 不存在")

    date_repo = IndexDailyBarRepository(db)
    available_dates = [
        row.trade_date
        for row in date_repo.find_by_code_date_range(
            index_code,
            trade_date - timedelta(days=800),
            trade_date,
        )
    ]
    if trade_date not in available_dates:
        raise HTTPException(
            status_code=422,
            detail=f"{trade_date.isoformat()} 不是指数 {index_code} 的已入库交易日",
        )
    calculation_dates = available_dates[-240:]

    started = _log_compute_start(
        factor_label="指数单日扩散",
        start=calculation_dates[0],
        end=trade_date,
        codes=None,
        params={"index_code": index_code, "target_date": trade_date.isoformat()},
    )
    panels = IndexFactorPanelService(db).build_panels(
        index_codes=[index_code],
        dates=calculation_dates,
        lookback_natural_days=820,
        include_industry_panels=False,
    )
    value = IndexDiffusionRatioComputer().compute(
        index_code,
        trade_date,
        FactorContext(index_bars={}, panels=panels),
    )
    payload = value.payload or {}
    metrics = panels.get("panel_metrics") or {}
    _log_compute_done(
        factor_label="指数单日扩散",
        started=started,
        rows=1,
        issue_count=0,
    )
    return IndexDiffusionDebugResponse(
        index_code=index_code,
        trade_date=trade_date,
        factor_value=value.numeric,
        raw_ratio=_num(payload.get("raw_ratio")),
        member_count=int(payload.get("member_count", 0)),
        valid_sample_count=int(payload.get("valid_sample_count", 0)),
        missing_sample_count=int(payload.get("missing_sample_count", 0)),
        rising_sample_count=int(payload.get("rising_sample_count", 0)),
        valid_days=int(payload.get("valid_days", 0)),
        window_complete=bool(payload.get("window_complete", False)),
        lookback=int(payload.get("lookback", 220)),
        smooth_window=int(payload.get("smooth_window", 20)),
        calculation_version=str(payload.get("calculation_version", "")),
        calculation_date_count=int(metrics.get("calculation_date_count", 0)),
        stock_count=int(metrics.get("stock_count", 0)),
        stock_close_point_count=int(metrics.get("stock_close_point_count", 0)),
    )


def _rrg_warmup_days(lookback_ratio: int, lookback_mom: int, smooth_window: int) -> int:
    """RRG 输出首个有效值需要的 warm-up 交易日数（研报口径）。"""
    return lookback_ratio + lookback_mom + 2 * (smooth_window - 1)


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
    """即时计算 RRG 序列（warm-up 由服务端自动前移，问题显式返回）。"""
    _validate_range(start, end)
    _validate_max_days(start, end, RRG_LAB_MAX_RANGE_DAYS, "RRG")
    service = IndustryFactorService(db)
    codes = _split_codes(industry_codes)
    started = _log_compute_start(
        factor_label="RRG",
        start=start,
        end=end,
        codes=codes,
        params={
            "lookback_ratio": lookback_ratio,
            "lookback_mom": lookback_mom,
            "smooth_window": smooth_window,
        },
    )
    try:
        panels = service.build_panels(
            start=start,
            end=end,
            industry_codes=codes,
            lookback_ratio=lookback_ratio,
            lookback_mom=lookback_mom,
            smooth_window=smooth_window,
            need_diffusion=False,
            collect_coverage=True,
        )
    except ValueError as e:
        logger.warning(
            "RRG 即时计算失败 start=%s end=%s elapsed_ms=%.0f error=%s",
            start.isoformat(),
            end.isoformat(),
            (time.perf_counter() - started) * 1000,
            e,
        )
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
            r = _num(ratio.loc[day, code])
            m = _num(momentum.loc[day, code])
            q = _num(quadrant.loc[day, code])
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
    market_dates = list(panels.get("market_dates", []))
    output_dates = [d for d in market_dates if start <= d <= end]
    coverage: list[dict[str, Any]] = []
    for item in panels.get("rrg_coverage", []):
        enriched = dict(item)
        enriched["name_cn"] = names.get(item["industry_code"], "")
        coverage.append(enriched)
    valid_dates = [
        item["valid_from"] for item in coverage if item.get("valid_from") is not None
    ]
    valid_until_dates = [
        item["valid_until"] for item in coverage if item.get("valid_until") is not None
    ]
    effective_start = min(valid_dates) if valid_dates else None
    effective_end = max(valid_until_dates) if valid_until_dates else None
    meta = IndustryRRGMeta(
        requested_start=start,
        requested_end=end,
        effective_start=effective_start,
        effective_end=effective_end,
        market_trading_days=len(output_dates),
        warmup_required=_rrg_warmup_days(lookback_ratio, lookback_mom, smooth_window),
        max_range_days=RRG_LAB_MAX_RANGE_DAYS,
        issues=_issue_items(panels.get("data_issues", {}).get("rrg", [])),
        coverage=coverage,
        notes=list(panels.get("notes", {}).get("rrg", [])),
    )
    _log_compute_done(
        factor_label="RRG",
        started=started,
        rows=len(points),
        issue_count=len(meta.issues),
    )
    return IndustryRRGResponse(
        points=points,
        warmup_days=_rrg_warmup_days(lookback_ratio, lookback_mom, smooth_window),
        meta=meta,
    )


@router.get("/diffusion", response_model=IndustryDiffusionResponse)
def get_diffusion_series(
    start: date = Query(..., description="起始日期"),
    end: date = Query(..., description="截止日期"),
    industry_codes: str | None = Query(default=None, description="逗号分隔行业代码"),
    diffusion_lookback: int = Query(default=220, ge=1),
    smooth_window: int = Query(default=20, ge=1),
    with_coverage: bool = Query(
        default=False,
        description="True=逐点返回每日有效样本/成员数/覆盖度（增大响应体）",
    ),
    db: Session = Depends(get_db),
) -> IndustryDiffusionResponse:
    """即时计算数量占比扩散序列（按行业分批加载，含覆盖元信息）。"""
    _validate_range(start, end)
    _validate_max_days(start, end, DIFFUSION_LAB_MAX_RANGE_DAYS, "扩散")
    service = IndustryFactorService(db)
    codes = _split_codes(industry_codes)
    started = _log_compute_start(
        factor_label="扩散",
        start=start,
        end=end,
        codes=codes,
        params={
            "diffusion_lookback": diffusion_lookback,
            "smooth_window": smooth_window,
            "with_coverage": with_coverage,
        },
    )
    try:
        panels = service.build_panels(
            start=start,
            end=end,
            industry_codes=codes,
            smooth_window=smooth_window,
            diffusion_lookback=diffusion_lookback,
            need_rrg=False,
            collect_coverage=True,
        )
    except ValueError as e:
        logger.warning(
            "扩散即时计算失败 start=%s end=%s elapsed_ms=%.0f error=%s",
            start.isoformat(),
            end.isoformat(),
            (time.perf_counter() - started) * 1000,
            e,
        )
        raise HTTPException(status_code=422, detail=str(e))
    names = service.industry_names()
    diffusion = panels["diffusion"]
    daily = panels.get("diffusion_daily", {"valid": None, "member": None})
    valid_frame = daily.get("valid") if with_coverage else None
    member_frame = daily.get("member") if with_coverage else None
    points: list[IndustryDiffusionPoint] = []
    codes = panels["industry_codes"]
    for day in diffusion.index:
        for code in codes:
            if code not in diffusion.columns:
                continue
            value = _num(diffusion.loc[day, code])
            valid_count: int | None = None
            member_count: int | None = None
            coverage: float | None = None
            if valid_frame is not None and code in valid_frame.columns:
                valid_value = valid_frame.loc[day, code]
                member_value = member_frame.loc[day, code] if member_frame is not None else None
                valid_count = None if _num(valid_value) is None else int(valid_value)
                member_count = None if _num(member_value) is None else int(member_value)
                if valid_count is not None and member_count:
                    coverage = round(valid_count / member_count, 4)
            points.append(
                IndustryDiffusionPoint(
                    trade_date=day.date().isoformat(),
                    industry_code=code,
                    name_cn=names.get(code, ""),
                    value=None if value is None else round(value, 6),
                    valid_count=valid_count,
                    member_count=member_count,
                    coverage=coverage,
                )
            )
    market_dates = list(panels.get("market_dates", []))
    output_dates = [d for d in market_dates if start <= d <= end]
    coverage_items: list[dict[str, Any]] = []
    for item in panels.get("diffusion_coverage", []):
        enriched = dict(item)
        enriched["name_cn"] = names.get(item["industry_code"], "")
        coverage_items.append(enriched)
    valid_dates = [
        item["valid_from"] for item in coverage_items if item.get("valid_from") is not None
    ]
    valid_until_dates = [
        item["valid_until"]
        for item in coverage_items
        if item.get("valid_until") is not None
    ]
    effective_start = min(valid_dates) if valid_dates else None
    effective_end = max(valid_until_dates) if valid_until_dates else None
    meta = IndustryDiffusionMeta(
        requested_start=start,
        requested_end=end,
        effective_start=effective_start,
        effective_end=effective_end,
        market_trading_days=len(output_dates),
        output_days=len(output_dates),
        max_range_days=DIFFUSION_LAB_MAX_RANGE_DAYS,
        issues=_issue_items(panels.get("data_issues", {}).get("diffusion", [])),
        coverage=coverage_items,
        rules=list(panels.get("notes", {}).get("diffusion", [])),
    )
    _log_compute_done(
        factor_label="扩散",
        started=started,
        rows=len(points),
        issue_count=len(meta.issues),
    )
    return IndustryDiffusionResponse(points=points, meta=meta)


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
    """按研报信号规则输出轮动选择（默认月末决策，仅供研究预览）。"""
    _validate_range(start, end)
    _validate_max_days(start, end, RRG_LAB_MAX_RANGE_DAYS, "轮动选择")
    try:
        quadrants = [int(p) for p in keep_quadrants.split(",") if p.strip()]
        rotation = IndustrySelectionConfig(
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
