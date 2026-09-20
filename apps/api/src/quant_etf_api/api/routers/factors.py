"""因子层 API 路由，提供因子模板元数据查询/编辑和因子值现算查询接口。

因子值一律按模板与参数现算，不读取任何持久化因子值：
- 横截面：某个模板实例在指定交易日的全部活跃指数取值；
- 时间序列：某个模板实例在单指数上的历史取值；
- IC 与相关性：由现算结果派生。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db, get_factor_registry
from quant_etf_api.factors.catalog import FactorResolutionError, FactorTemplateRegistry
from quant_etf_api.factors.compute import FactorComputeService
from quant_etf_api.factors.templates import FactorParameterError
from quant_etf_api.infra.db.repositories.factor_definition import FactorDefinitionRepository
from quant_etf_api.infra.time import today_cn
from quant_etf_api.factors.evaluation import (
    MIN_CROSS_SECTION_N,
    analyze_ic,
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


def _parse_params(raw: str | None) -> dict[str, Any]:
    """解析模板参数查询串。

    Args:
        raw: params 查询参数，取值是 JSON 对象文本；None 或空串表示默认口径。

    Returns:
        参数字典。

    Raises:
        HTTPException: 取值不是合法 JSON 对象时返回 422。
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail=f"params 必须是合法 JSON：{exc}") from None
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="params 必须是 JSON 对象")
    return parsed


def _resolve_instance(
    registry: FactorTemplateRegistry,
    factor_id: str,
    params: dict[str, Any],
) -> Any:
    """把模板 ID 与参数解析为可执行实例。

    Args:
        registry: 因子模板注册表。
        factor_id: 因子模板 ID。
        params: 参数覆盖。

    Returns:
        FactorInstance。

    Raises:
        HTTPException: 模板不存在返回 404，参数不合法返回 422。
    """
    try:
        return registry.resolve_params(factor_id, params)
    except FactorResolutionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except FactorParameterError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _spec_response(row: Any) -> FactorSpecResponse:
    """把 factor_definition 行转换为模板元数据响应。

    Args:
        row: FactorDefinitionModel 行。

    Returns:
        FactorSpecResponse。
    """
    return FactorSpecResponse(
        factor_id=row.factor_id,
        name=row.name,
        category=row.category,
        version=row.version,
        description=row.description,
        required_data=row.required_data or [],
        is_active=row.is_active,
        value_shape=row.value_shape,
        usage=list(row.usage or []),
        parameter_schema=row.parameter_schema or {},
        default_params=row.default_params,
    )


@router.post("/factors/init")
def init_factor_definitions(
    db: Session = Depends(get_db),
    registry: FactorTemplateRegistry = Depends(get_factor_registry),
) -> dict[str, int]:
    """手动触发因子模板同步：将代码中的模板元数据同步到数据库。

    同步策略：
    - 代码中有、DB 中没有 → INSERT（新模板）
    - 代码和 DB 都有 → 仅更新代码管控字段（version/required_data/参数模式等）
    - DB 中有、代码中没有 → 设为 is_active=False

    Returns:
        同步统计：new / updated / deactivated。
    """
    svc = FactorAdminService(db, registry)
    try:
        return svc.sync_factor_definitions()
    except Exception:
        raise HTTPException(status_code=500, detail="因子模板同步失败") from None


@router.get("/factors/", response_model=list[FactorSpecResponse])
def list_factor_specs(
    db: Session = Depends(get_db),
) -> list[FactorSpecResponse]:
    """返回所有因子模板的元数据列表（含已禁用），从数据库读取。"""
    return [_spec_response(row) for row in FactorDefinitionRepository(db).find_all()]


@router.patch("/factors/{factor_id}", response_model=FactorSpecResponse)
def update_factor(
    factor_id: str,
    body: FactorUpdateRequest,
    db: Session = Depends(get_db),
) -> FactorSpecResponse:
    """编辑因子模板元数据（名称、描述、类别、启用状态）。

    Args:
        factor_id: 因子模板标识。
        body: 编辑请求体，仅传入需要修改的字段。

    Returns:
        更新后的因子模板元数据。
    """
    repo = FactorDefinitionRepository(db)
    row = repo.find_by_id(factor_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"因子模板 {factor_id} 不存在")

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
        logger.warning("更新因子模板失败: factor_id=%s", factor_id, exc_info=True)
        raise HTTPException(status_code=500, detail="更新失败") from None

    return _spec_response(row)


@router.get("/factors/{factor_id}/cross-section", response_model=CrossSectionResponse)
def factor_cross_section(
    factor_id: str,
    trade_date: date | None = Query(None, description="查询日期，不传时取最新有行情的交易日"),
    params: str | None = Query(None, description='模板参数 JSON，如 {"period": 20}'),
    db: Session = Depends(get_db),
    registry: FactorTemplateRegistry = Depends(get_factor_registry),
) -> CrossSectionResponse:
    """查询指定因子模板实例的横截面快照（含指数中文名）。

    不传 trade_date 时取最新有行情的交易日；参数按模板 parameter_schema 校验。
    """
    if trade_date is not None and trade_date > today_cn():
        raise HTTPException(status_code=422, detail="不能查询未来日期")
    instance = _resolve_instance(registry, factor_id, _parse_params(params))
    svc = FactorComputeService(db, registry)
    try:
        result_date, rows = svc.compute_cross_section(instance, trade_date=trade_date)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None

    return CrossSectionResponse(
        factor_id=factor_id,
        params=dict(instance.params),
        trade_date=str(result_date),
        rows=[
            CrossSectionRow(
                index_code=row.index_code,
                name_cn=row.name_cn,
                factor_value_numeric=row.factor_value_numeric,
                factor_value_text=row.factor_value_text,
            )
            for row in rows
        ],
    )


@router.get("/factors/{factor_id}/values", response_model=list[FactorRow])
def factor_time_series(
    factor_id: str,
    index_code: str = Query(..., description="指数代码，如 000300"),
    start_date: date = Query(..., description="开始日期（含）"),
    end_date: date = Query(..., description="结束日期（含）"),
    params: str | None = Query(None, description='模板参数 JSON，如 {"period": 20}'),
    db: Session = Depends(get_db),
    registry: FactorTemplateRegistry = Depends(get_factor_registry),
) -> list[FactorRow]:
    """查询因子模板实例在单指数上的历史时间序列。"""
    instance = _resolve_instance(registry, factor_id, _parse_params(params))
    return FactorComputeService(db, registry).compute_series(
        instance, index_code, start_date, end_date
    )


@router.get("/factors/{factor_id}/ic", response_model=ICResponse)
def factor_ic_analysis(
    factor_id: str,
    start_date: date = Query(..., description="IC 分析起始日期（含）"),
    end_date: date = Query(..., description="IC 分析截止日期（含）"),
    forward_days: int = Query(1, ge=1, le=20, description="前瞻天数，用于计算下期收益率"),
    min_cross_section_n: int = Query(
        MIN_CROSS_SECTION_N,
        ge=5,
        le=200,
        description="有效 Rank IC 所需的最小横截面指数数量",
    ),
    params: str | None = Query(None, description='模板参数 JSON，如 {"period": 20}'),
    db: Session = Depends(get_db),
    registry: FactorTemplateRegistry = Depends(get_factor_registry),
) -> ICResponse:
    """查询因子模板实例的 IC（Information Coefficient）分析。

    计算因子值与下期收益率的 Rank IC 时间序列及汇总统计；因子值由现算服务按
    当日 point-in-time 指数集合计算。横截面指数数量少于 ``min_cross_section_n``
    的交易日不产出有效 IC（n=3 时 Spearman 只能取 ±1/±0.5），这些日期会被排除
    并计入 ``summary.excluded_low_n_days``，未产出有效 IC 时
    ``summary.insufficient_reason`` 给出原因。

    判读提示：显著性看 ``summary.t_stat``（|t| > 2 才算显著），横截面规模看
    ``summary.cross_section_n_avg``；``forward_days > 1`` 时相邻观测的前瞻窗口
    重叠，应参考 ``effective_n`` 而非 ``count``。
    """
    instance = _resolve_instance(registry, factor_id, _parse_params(params))
    try:
        result = analyze_ic(
            db,
            instance,
            start_date,
            end_date,
            forward_days,
            min_cross_section_n,
        )
    except Exception:
        logger.warning("IC 分析失败: factor_id=%s", factor_id, exc_info=True)
        raise HTTPException(status_code=500, detail="IC 分析计算失败") from None

    summary = result["summary"]
    return ICResponse(
        factor_id=factor_id,
        params=dict(instance.params),
        summary=ICSummary(**summary),
        series=[
            {
                "trade_date": item["trade_date"],
                "ic": item["ic"],
                "cross_section_n": item["cross_section_n"],
            }
            for item in result["series"]
        ],
    )


@router.get("/factors/correlation", response_model=CorrelationResponse)
def factor_correlation(
    trade_date: date = Query(..., description="交易日"),
    factor_ids: list[str] | None = Query(
        None, description="模板 ID 列表，不传时计算全部已注册模板的默认参数口径"
    ),
    min_cross_section_n: int = Query(
        MIN_CROSS_SECTION_N,
        ge=5,
        le=200,
        description="计算相关系数所需的最小成对样本数",
    ),
    db: Session = Depends(get_db),
    registry: FactorTemplateRegistry = Depends(get_factor_registry),
) -> CorrelationResponse:
    """查询因子间截面 Rank 相关性矩阵。

    对指定交易日的活跃指数，按**成对交集**计算各因子值之间的 Spearman 相关系数
    （因子覆盖不一致时不再要求全局交集，避免交集塌缩甚至为空）。可用于判断因子
    冗余度，相关性高的因子可考虑正交化或二选一。每对实际使用的指数数量见
    ``pair_counts``；样本不足或相关未定义的格子为 ``null``（不伪造为 0）。
    """
    try:
        result = calc_factor_correlation_matrix(
            db, trade_date, factor_ids, min_cross_section_n, registry
        )
    except Exception:
        logger.warning("因子相关性计算失败: trade_date=%s", trade_date, exc_info=True)
        raise HTTPException(status_code=500, detail="相关性计算失败") from None

    return CorrelationResponse(
        factor_ids=result["factor_ids"],
        matrix=result["matrix"],
        pair_counts=result.get("pair_counts", []),
        index_count=result.get("index_count", 0),
        undetermined_pair_count=result.get("undetermined_pair_count", 0),
        trade_date=result.get("trade_date", str(trade_date)),
    )
