from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.etf import EtfCreateRequest, EtfCreateResponse, EtfDetail
from quant_etf_api.services.universe_service import UniverseService

router = APIRouter(tags=["etfs"])


@router.get("/etfs", response_model=list[EtfDetail])
def list_etfs(db: Session = Depends(get_db)) -> list[EtfDetail]:
    return UniverseService(db).list_etfs()


@router.post("/etfs", response_model=EtfCreateResponse, status_code=201)
def create_etf(payload: EtfCreateRequest, db: Session = Depends(get_db)) -> EtfCreateResponse:
    try:
        etf = UniverseService(db).add_etf(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return EtfCreateResponse(etf=etf, message=f"ETF {payload.etf_code} 添加成功")


@router.get("/etfs/{etf_code}", response_model=EtfDetail)
def get_etf(etf_code: str, db: Session = Depends(get_db)) -> EtfDetail:
    etf = UniverseService(db).get_etf(etf_code)
    if etf is None:
        raise HTTPException(status_code=404, detail="ETF not found")
    return etf


@router.delete("/etfs/{etf_code}", status_code=204)
def delete_etf(etf_code: str, db: Session = Depends(get_db)) -> None:
    try:
        UniverseService(db).remove_etf(etf_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
