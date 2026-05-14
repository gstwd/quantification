from fastapi import APIRouter

from quant_etf_api.services.system_service import SystemService

router = APIRouter(tags=["system"])
service = SystemService()


@router.get("/system/status")
def system_status() -> dict:
    return service.status()
