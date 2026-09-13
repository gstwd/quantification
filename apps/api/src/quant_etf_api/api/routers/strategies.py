"""策略路由：策略列表、详情、配置管理和资产配置决策管线。"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.lifecycle import (
    HealthSnapshotSchema,
    LifecycleDetail,
    LifecycleOnlineRequest,
    LifecycleRefreshRequest,
    LifecycleStatusRequest,
    LifecycleSummary,
)
from quant_etf_api.schemas.strategy import (
    AllocationResponse,
    StarredSummaryResponse,
    StrategyConfigCreate,
    StrategyConfigUpdate,
    StrategyDetail,
    StrategySummary,
    StrategyValidationResult,
)
from quant_etf_api.services.strategy_lifecycle_service import StrategyLifecycleService
from quant_etf_api.services.strategy_service import StrategyService

router = APIRouter(tags=["strategies"])


@router.get("/strategies", response_model=list[StrategySummary])
def list_strategies(db: Session = Depends(get_db)) -> list[StrategySummary]:
    """返回所有已启用策略的摘要列表。"""
    return StrategyService(db).list_strategies()


@router.get("/strategies/starred/summary", response_model=StarredSummaryResponse)
def get_starred_summary(
    trade_date: date | None = Query(None, description="指定交易日（YYYY-MM-DD），不传则使用今天"),
    db: Session = Depends(get_db),
) -> StarredSummaryResponse:
    """获取所有星标策略的当日执行摘要。

    返回各星标策略的择时信号、资产排名、仓位分配及调仓日判断结果。
    """
    return StrategyService(db).get_starred_summary(trade_date=trade_date)


# 注意：生命周期列表路由必须定义在 /strategies/{strategy_id} 之前，
# 否则 /strategies/lifecycle 会被 {strategy_id} 捕获并返回 404。
@router.get("/strategies/lifecycle", response_model=list[LifecycleSummary])
def list_lifecycles(db: Session = Depends(get_db)) -> list[LifecycleSummary]:
    """返回全部上线策略的生命周期摘要（人工标记上线后的监控列表）。"""
    return StrategyLifecycleService(db).list_lifecycles()


@router.get("/strategies/{strategy_id}", response_model=StrategyDetail)
def get_strategy(strategy_id: str, db: Session = Depends(get_db)) -> StrategyDetail:
    """按 ID 获取策略详情。"""
    strategy = StrategyService(db).get_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return strategy


@router.post("/strategies", response_model=StrategyDetail, status_code=201)
def create_strategy(req: StrategyConfigCreate, db: Session = Depends(get_db)) -> StrategyDetail:
    """创建策略配置。"""
    try:
        return StrategyService(db).create_config(req)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.put("/strategies/{strategy_id}", response_model=StrategyDetail)
def update_strategy(
    strategy_id: str, req: StrategyConfigUpdate, db: Session = Depends(get_db)
) -> StrategyDetail:
    """更新策略配置。"""
    try:
        result = StrategyService(db).update_config(strategy_id, req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="策略不存在")
    return result


@router.delete("/strategies/{strategy_id}", status_code=204)
def delete_strategy(strategy_id: str, db: Session = Depends(get_db)) -> None:
    """删除策略配置。"""
    if not StrategyService(db).delete_config(strategy_id):
        raise HTTPException(status_code=404, detail="策略不存在")


@router.post("/strategies/validate", response_model=StrategyValidationResult)
def validate_strategy_config(
    config_json: dict, db: Session = Depends(get_db)
) -> StrategyValidationResult:
    """校验策略配置 JSON 是否合法（不持久化）。"""
    return StrategyService(db).validate_config(config_json)


@router.get("/strategies/{strategy_id}/allocation", response_model=AllocationResponse)
def run_allocation(
    strategy_id: str,
    trade_date: date | None = Query(
        None, description="指定交易日（YYYY-MM-DD），不传则使用最新数据"
    ),
    db: Session = Depends(get_db),
) -> AllocationResponse:
    """运行资产配置决策管线，返回择时、排名、仓位分配结果。

    支持通过 trade_date 参数查看历史某一天的决策结果。
    """
    try:
        result = StrategyService(db).run_allocation(strategy_id, trade_date=trade_date)
    except ValueError as e:
        # 配置引用未知/停用因子等校验失败 → 快速失败，返回 422 而非静默错误结果
        raise HTTPException(status_code=422, detail=str(e))
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"策略 {strategy_id} 不存在或不支持资产配置决策管线",
        )
    return result


@router.post("/strategies/{strategy_id}/star", status_code=204)
def star_strategy(strategy_id: str, db: Session = Depends(get_db)) -> None:
    """星标关注策略。"""
    if not StrategyService(db).star_strategy(strategy_id, True):
        raise HTTPException(status_code=404, detail="策略不存在")


@router.post("/strategies/{strategy_id}/unstar", status_code=204)
def unstar_strategy(strategy_id: str, db: Session = Depends(get_db)) -> None:
    """取消星标关注。"""
    if not StrategyService(db).star_strategy(strategy_id, False):
        raise HTTPException(status_code=404, detail="策略不存在")


# ── 策略生命周期（上线后监控与诊断）────────────────────────────────────


@router.get("/strategies/{strategy_id}/lifecycle", response_model=LifecycleDetail)
def get_lifecycle(strategy_id: str, db: Session = Depends(get_db)) -> LifecycleDetail:
    """返回单策略的生命周期详情（含冻结快照与最近的健康体检记录）。"""
    detail = StrategyLifecycleService(db).get_lifecycle(strategy_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="该策略尚未标记上线")
    return detail


@router.post("/strategies/{strategy_id}/lifecycle/online", response_model=LifecycleDetail)
def online_strategy(
    strategy_id: str,
    req: LifecycleOnlineRequest,
    db: Session = Depends(get_db),
) -> LifecycleDetail:
    """人工标记策略上线：冻结配置快照并生成研究期分布作为监控参照系。

    该操作会复用已存在的研究期回测；若无可用回测则同步执行一次
    （2016-01-01 ~ 2025-12-31），因此首次调用耗时较长。
    """
    try:
        return StrategyLifecycleService(db).online(strategy_id, req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/strategies/{strategy_id}/lifecycle/status", response_model=LifecycleDetail)
def update_lifecycle_status(
    strategy_id: str,
    req: LifecycleStatusRequest,
    db: Session = Depends(get_db),
) -> LifecycleDetail:
    """人工变更生命周期状态（运行中 / 暂停观察 / 退役），系统不会自动切换。"""
    try:
        return StrategyLifecycleService(db).update_status(strategy_id, req)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/strategies/{strategy_id}/lifecycle/refresh",
    response_model=HealthSnapshotSchema,
)
def refresh_lifecycle(
    strategy_id: str,
    req: LifecycleRefreshRequest,
    db: Session = Depends(get_db),
) -> HealthSnapshotSchema:
    """人工触发一次健康体检：同步跑上线后回测并与研究期分布比对。

    同步执行，调用方需容忍一次验证期回测的耗时；结果会追加一条健康快照。
    """
    try:
        return StrategyLifecycleService(db).refresh(strategy_id, req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
