from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.schemas.market_data import IndexCreateRequest, IndexCreateResponse
from quant_etf_api.services.index_service import IndexService

router = APIRouter(tags=["indexes"])


@router.post("/indexes", response_model=IndexCreateResponse, status_code=201)
def create_index(
    payload: IndexCreateRequest,
    db: Session = Depends(get_db),
) -> IndexCreateResponse:
    """添加基准指数，自动从 AkShare 获取中文名称。

    获取失败时可通过 name_cn 字段手动指定名称。
    """
    try:
        index = IndexService(db).add_index(payload.index_code, payload.name_cn)
        return IndexCreateResponse(index=index, message=f"指数 {payload.index_code} 添加成功")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/indexes/{index_code}", status_code=204)
def delete_index(
    index_code: str,
    db: Session = Depends(get_db),
) -> None:
    """删除基准指数。"""
    try:
        IndexService(db).remove_index(index_code)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
