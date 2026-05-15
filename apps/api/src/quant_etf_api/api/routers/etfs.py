from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.etf import EtfDetail
from quant_etf_api.services.universe_service import UniverseService

router = APIRouter(tags=["etfs"])


@router.get("/etfs", response_model=list[EtfDetail])
def list_etfs(db: Session = Depends(get_db)) -> list[EtfDetail]:
    return UniverseService(db).list_etfs()


@router.get("/etfs/{etf_code}", response_model=EtfDetail)
def get_etf(etf_code: str, db: Session = Depends(get_db)) -> EtfDetail:
    etf = UniverseService(db).get_etf(etf_code)
    if etf is None:
        raise HTTPException(status_code=404, detail="ETF not found")
    return etf
