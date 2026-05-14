from fastapi import APIRouter, HTTPException

from quant_etf_api.schemas.etf import EtfDetail
from quant_etf_api.services.universe_service import UniverseService

router = APIRouter(tags=["etfs"])
service = UniverseService()


@router.get("/etfs", response_model=list[EtfDetail])
def list_etfs() -> list[EtfDetail]:
    return service.list_etfs()


@router.get("/etfs/{etf_code}", response_model=EtfDetail)
def get_etf(etf_code: str) -> EtfDetail:
    etf = service.get_etf(etf_code)
    if etf is None:
        raise HTTPException(status_code=404, detail="ETF not found")
    return etf
