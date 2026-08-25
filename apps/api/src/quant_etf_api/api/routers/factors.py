"""因子层 API 路由，提供因子元数据查询/编辑和因子值查询接口。"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db, get_factor_registry
from quant_etf_api.factors.registry import FactorRegistry
from quant_etf_api.factors.service import FactorService
from quant_etf_api.infra.db.repositories.factor_definition import FactorDefinitionRepository
from quant_etf_api.factors.evaluation import (
    calc_factor_correlation_matrix,
)
from quant_etf_api.schemas.factor import (
    CorrelationResponse,
    CrossSectionResponse,
    CrossSectionRow,
    FactorSpecResponse,
    FactorUpdateRequest,
    ICResponse,
    ICSummary,
)
from quant_etf_api.schemas.signal import FactorRow
from quant_etf_api.services.factor_admin_service import FactorAdminService

router = APIRouter(tags=["factors"])
logger = logging.getLogger(__name__)


@router.post("/factors/init")
def init_factor_definitions(
    db: Session = Depends(get_db),
    registry: FactorRegistry = Depends(get_factor_registry),
) -> dict[str, int]:
    """手动触发因子定义同步：将代码中的因子元数据同步到数据库。

    同步策略：
    - 代码中有、DB 中没有 → INSERT（新因子）
    - 代码和 DB 都有 → 仅更新 version、required_data
    - DB 中有、代码中没有 → 设为 is_active=False

    Returns:
        同步统计：new / updated / deactivated。
    """
    svc = FactorAdminService(db, registry)
    try:
        return svc.sync_factor_definitions()
    except Exception:
        raise HTTPException(status_code=500, detail="因子定义同步失败") from None


@router.get("/factors/", response_model=list[FactorSpecResponse])
def list_factor_specs(
    db: Session = Depends(get_db),
) -> list[FactorSpecResponse]:
    """返回所有因子的元数据描述符列表（含已禁用），从数据库读取。"""
    repo = FactorDefinitionRepository(db)
    rows = repo.find_all()
    return [
        FactorSpecResponse(
            factor_id=r.factor_id,
            name=r.name,
            category=r.category,
            version=r.version,
            description=r.description,
            required_data=r.required_data or [],
            is_active=r.is_active,
        )
        for r in rows
    ]


@router.patch("/factors/{factor_id}", response_model=FactorSpecResponse)
def update_factor(
    factor_id: str,
    body: FactorUpdateRequest,
    db: Session = Depends(get_db),
) -> FactorSpecResponse:
    """编辑因子元数据（名称、描述、类别、启用状态）。

    Args:
        factor_id: 因子标识。
        body: 编辑请求体，仅传入需要修改的字段。

    Returns:
        更新后的因子元数据。
    """
    repo = FactorDefinitionRepository(db)
    row = repo.find_by_id(factor_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"因子 {factor_id} 不存在")

    if body.name is not None:
        row.name = body.name
    if body.description is not None:
        row.description = body.description
    if body.category is not None:
        row.category = body.category
    if body.is_active is not None:
        row.is_active = body.is_active

    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("更新因子定义失败: factor_id=%s", factor_id, exc_info=True)
        raise HTTPException(status_code=500, detail="更新失败") from None

    return FactorSpecResponse(
        factor_id=row.factor_id,
        name=row.name,
        category=row.category,
        version=row.version,
        description=row.description,
        required_data=row.required_data or [],
        is_active=row.is_active,
    )


@router.get("/factors/{factor_id}/cross-section", response_model=CrossSectionResponse)
def factor_cross_section(
    factor_id: str,
    trade_date: date | None = Query(None, description="查询日期，不传时自动选择最新有数据的日期"),
    force_recompute: bool = Query(False, description="是否强制重新计算并覆盖已有数据"),
    db: Session = Depends(get_db),
    registry: FactorRegistry = Depends(get_factor_registry),
) -> CrossSectionResponse:
    """查询指定因子的横截面快照（含指数中文名）。

    不传 trade_date 时自动选择最新有数据的日期，若该日无数据则按需计算。
    设置 force_recompute=True 时强制重新计算并覆盖已有数据。
    """
    repo = FactorDefinitionRepository(db)
    if repo.find_by_id(factor_id) is None:
        raise HTTPException(status_code=404, detail=f"因子 {factor_id} 不存在")

    svc = FactorService(db, registry)
    try:
        result_date, rows = svc.get_or_compute_cross_section(factor_id, force_recompute)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None

    return CrossSectionResponse(
        factor_id=factor_id,
        trade_date=str(result_date),
        rows=[
            CrossSectionRow(
                index_code=r.index_code,
                name_cn=r.name_cn,
                factor_value_numeric=r.factor_value_numeric,
                factor_value_text=r.factor_value_text,
            )
            for r in rows
        ],
    )


@router.get("/factors/{factor_id}/values", response_model=list[FactorRow])
def factor_time_series(
    factor_id: str,
    index_code: str = Query(..., description="指数代码，如 000300"),
    start_date: date = Query(..., description="开始日期（含）"),
    end_date: date = Query(..., description="结束日期（含）"),
    force_recompute: bool = Query(False, description="是否强制重新计算并覆盖已有数据"),
    db: Session = Depends(get_db),
    registry: FactorRegistry = Depends(get_factor_registry),
) -> list[FactorRow]:
    """查询单因子在单指数上的历史时间序列，自动补算缺失日期后返回。

    设置 force_recompute=True 时强制重新计算并覆盖已有数据。
    """
    repo = FactorDefinitionRepository(db)
    if repo.find_by_id(factor_id) is None:
        raise HTTPException(status_code=404, detail=f"因子 {factor_id} 不存在")

    svc = FactorService(db, registry)
    return svc.get_or_compute_time_series(
        factor_id=factor_id,
        index_code=index_code,
        start_date=start_date,
        end_date=end_date,
        force_recompute=force_recompute,
    )


@router.get("/factors/{factor_id}/ic", response_model=ICResponse)
def factor_ic_analysis(
    factor_id: str,
    start_date: date = Query(..., description="IC 分析起始日期（含）"),
    end_date: date = Query(..., description="IC 分析截止日期（含）"),
    forward_days: int = Query(1, ge=1, le=20, description="前瞻天数，用于计算下期收益率"),
    db: Session = Depends(get_db),
) -> ICResponse:
    """查询因子的 IC（Information Coefficient）分析。

    计算因子值与下期收益率的 Rank IC 时间序列及汇总统计。
    IC 均值 > 0 表示因子有正向预测力，IC_IR > 0.5 表示因子较稳定。
    """
    repo = FactorDefinitionRepository(db)
    if repo.find_by_id(factor_id) is None:
        raise HTTPException(status_code=404, detail=f"因子 {factor_id} 不存在")

    try:
        from quant_etf_api.factors.evaluation import calc_ic_series

        series_data = calc_ic_series(db, factor_id, start_date, end_date, forward_days)
        # 从 IC 序列直接计算汇总统计，避免 calc_ic_summary 内部重复调用 calc_ic_series
        if series_data:
            ic_values = [s["ic"] for s in series_data]
            n = len(ic_values)
            mean = sum(ic_values) / n
            variance = sum((x - mean) ** 2 for x in ic_values) / (n - 1) if n > 1 else 0.0
            std = variance**0.5
            ic_ir = round(mean / std, 4) if std > 0 else None
            positive_count = sum(1 for x in ic_values if x > 0)
            summary = ICSummary(
                ic_mean=round(mean, 4),
                ic_std=round(std, 4),
                ic_ir=ic_ir,
                ic_positive_ratio=round(positive_count / n, 4),
                count=n,
            )
        else:
            summary = ICSummary(
                ic_mean=None, ic_std=None, ic_ir=None, ic_positive_ratio=None, count=0
            )
    except Exception:
        logger.warning("IC 分析失败: factor_id=%s", factor_id, exc_info=True)
        raise HTTPException(status_code=500, detail="IC 分析计算失败") from None

    return ICResponse(
        factor_id=factor_id,
        summary=summary,
        series=[{"trade_date": s["trade_date"], "ic": s["ic"]} for s in series_data],
    )


@router.get("/factors/correlation", response_model=CorrelationResponse)
def factor_correlation(
    trade_date: date = Query(..., description="交易日"),
    factor_ids: list[str] | None = Query(None, description="因子列表，不传时计算所有有数据的因子"),
    db: Session = Depends(get_db),
) -> CorrelationResponse:
    """查询因子间截面 Rank 相关性矩阵。

    对指定交易日的所有 ETF，计算各因子值之间的 Spearman 秩相关系数。
    可用于判断因子冗余度，相关性高的因子可考虑正交化或二选一。
    """
    try:
        result = calc_factor_correlation_matrix(db, trade_date, factor_ids)
    except Exception:
        logger.warning("因子相关性计算失败: trade_date=%s", trade_date, exc_info=True)
        raise HTTPException(status_code=500, detail="相关性计算失败") from None

    return CorrelationResponse(
        factor_ids=result["factor_ids"],
        matrix=result["matrix"],
        etf_count=result.get("etf_count", 0),
        trade_date=result.get("trade_date", str(trade_date)),
    )
