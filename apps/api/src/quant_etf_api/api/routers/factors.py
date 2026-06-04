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
from quant_etf_api.schemas.factor import (
    CrossSectionResponse,
    FactorSpecResponse,
    FactorUpdateRequest,
)
from quant_etf_api.schemas.signal import FactorRow

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
    svc = FactorService(db, registry)
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
    """查询指定因子的横截面快照（含 ETF 中文名）。

    不传 trade_date 时自动选择最新有数据的日期，若该日无数据则按需计算。
    设置 force_recompute=True 时强制重新计算并覆盖已有数据。
    """
    # 验证因子存在
    repo = FactorDefinitionRepository(db)
    if repo.find_by_id(factor_id) is None:
        raise HTTPException(status_code=404, detail=f"因子 {factor_id} 不存在")

    svc = FactorService(db, registry)
    try:
        if trade_date is not None:
            # 指定日期：强制重算或检查是否需要补算
            if force_recompute:
                svc.compute_and_store(trade_date)
            else:
                missing_codes = repo.find_missing_dates_for_all_etfs(factor_id, trade_date)
                if missing_codes:
                    svc.compute_and_store(trade_date)
            rows = repo.find_cross_section(factor_id, trade_date)
            result_date = trade_date
        else:
            # 自动选择最新日期
            result_date, _ = svc.get_or_compute_cross_section(factor_id, force_recompute)
            rows = repo.find_cross_section(factor_id, result_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None

    return CrossSectionResponse(
        factor_id=factor_id,
        trade_date=str(result_date),
        rows=[
            {
                "etf_code": r[0],
                "name_cn": r[1],
                "factor_value_numeric": r[2],
                "factor_value_text": r[3],
            }
            for r in rows
        ],
    )


@router.get("/factors/{factor_id}/values", response_model=list[FactorRow])
def factor_time_series(
    factor_id: str,
    etf_code: str = Query(..., description="ETF 代码，如 510300"),
    start_date: date = Query(..., description="开始日期（含）"),
    end_date: date = Query(..., description="结束日期（含）"),
    force_recompute: bool = Query(False, description="是否强制重新计算并覆盖已有数据"),
    db: Session = Depends(get_db),
    registry: FactorRegistry = Depends(get_factor_registry),
) -> list[FactorRow]:
    """查询单因子在单 ETF 上的历史时间序列，自动补算缺失日期后返回。

    设置 force_recompute=True 时强制重新计算并覆盖已有数据。
    """
    repo = FactorDefinitionRepository(db)
    if repo.find_by_id(factor_id) is None:
        raise HTTPException(status_code=404, detail=f"因子 {factor_id} 不存在")

    svc = FactorService(db, registry)
    return svc.get_or_compute_time_series(
        factor_id=factor_id,
        etf_code=etf_code,
        start_date=start_date,
        end_date=end_date,
        force_recompute=force_recompute,
    )
