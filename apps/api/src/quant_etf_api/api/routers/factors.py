"""因子层 API 路由，提供因子注册表查询和因子值查询/计算接口。"""

from __future__ import annotations

import logging
import threading
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db, get_factor_registry
from quant_etf_api.factors.registry import FactorRegistry
from quant_etf_api.factors.service import FactorService
from quant_etf_api.schemas.factor import FactorSpecResponse
from quant_etf_api.schemas.signal import FactorRow

router = APIRouter(tags=["factors"])
logger = logging.getLogger(__name__)


class ComputeRequest(BaseModel):
    """POST /factors/compute 请求体。

    Attributes:
        trade_date: 要计算的交易日。
    """

    trade_date: date


@router.get("/factors/", response_model=list[FactorSpecResponse])
def list_factor_specs(
    registry: FactorRegistry = Depends(get_factor_registry),
) -> list[FactorSpecResponse]:
    """返回注册表中所有因子的元数据描述符列表。"""
    return [
        FactorSpecResponse(
            factor_id=spec.factor_id,
            name=spec.name,
            category=spec.category,
            version=spec.version,
            description=spec.description,
            required_data=spec.required_data,
        )
        for spec in registry.specs()
    ]


@router.get("/factors/{factor_id}/values", response_model=list[FactorRow])
def factor_time_series(
    factor_id: str,
    etf_code: str = Query(..., description="ETF 代码，如 510300"),
    start_date: date = Query(..., description="开始日期（含）"),
    end_date: date = Query(..., description="结束日期（含）"),
    db: Session = Depends(get_db),
    registry: FactorRegistry = Depends(get_factor_registry),
) -> list[FactorRow]:
    """查询单因子在单 ETF 上的历史时间序列（仅独立因子，strategy_id IS NULL）。"""
    return FactorService(db, registry).factor_history(
        factor_id=factor_id,
        etf_code=etf_code,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/factors/{factor_id}/cross-section", response_model=list[FactorRow])
def factor_cross_section(
    factor_id: str,
    trade_date: date = Query(..., description="查询日期"),
    db: Session = Depends(get_db),
    registry: FactorRegistry = Depends(get_factor_registry),
) -> list[FactorRow]:
    """查询单因子在指定交易日的全 ETF 横截面快照（仅独立因子）。"""
    return FactorService(db, registry).factor_cross_section(
        factor_id=factor_id,
        trade_date=trade_date,
    )


@router.post("/factors/compute", status_code=202)
def trigger_factor_compute(
    body: ComputeRequest,
    registry: FactorRegistry = Depends(get_factor_registry),
) -> JSONResponse:
    """在后台线程中触发指定交易日的因子计算（非阻塞，立即返回 202）。

    参考 main._trigger_startup_fill() 的后台线程模式，
    后台线程持有独立 Session，不与请求 Session 共享，避免连接污染。
    """
    trade_date = body.trade_date

    def _bg() -> None:
        from quant_etf_api.infra.db.base import SessionLocal
        from quant_etf_api.services.run_service import RunService

        db = SessionLocal()
        run_svc = RunService(db)
        run = run_svc.create_run("factor_computation", None, trade_date)
        try:
            result = FactorService(db, registry).compute_and_store(trade_date)
            run_svc.mark_success(run.run_id)
            logger.info(
                "后台因子计算完成: run_id=%s trade_date=%s result=%s",
                run.run_id,
                trade_date,
                result,
            )
        except Exception as exc:
            run_svc.mark_failed(run.run_id, str(exc))
            logger.exception("后台因子计算失败: trade_date=%s", trade_date)
        finally:
            db.close()

    threading.Thread(target=_bg, daemon=True, name=f"factor-compute-{trade_date}").start()
    return JSONResponse(
        status_code=202,
        content={"message": f"因子计算已在后台启动，交易日：{trade_date}"},
    )
