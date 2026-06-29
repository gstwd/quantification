"""关键词→标签映射管理 API。

提供 keyword_tag_config 表的 CRUD 操作，支持前端动态管理分类规则。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from quant_etf_api.api.deps import get_db
from quant_etf_api.infra.db.repositories.keyword_tag_config import (
    KeywordTagConfigRepository,
)
from quant_etf_api.schemas.ai_factor import (
    KeywordTagConfigCreate,
    KeywordTagConfigResponse,
    KeywordTagConfigUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/keyword-tags", tags=["关键词标签"])


def _model_to_response(model: Any) -> KeywordTagConfigResponse:
    """将 ORM 模型转换为 API 响应。"""
    return KeywordTagConfigResponse(
        id=model.id,
        keyword=model.keyword,
        tag=model.tag,
        is_active=model.is_active,
        priority=model.priority,
        created_at=model.created_at.isoformat() if model.created_at else None,
        updated_at=model.updated_at.isoformat() if model.updated_at else None,
    )


@router.get("/", response_model=list[KeywordTagConfigResponse])
def list_keyword_tags(
    offset: int = Query(default=0, ge=0, description="偏移量"),
    limit: int = Query(default=100, ge=1, le=500, description="每页数量"),
    active_only: bool = Query(default=False, description="仅返回活跃的映射"),
    db: Session = Depends(get_db),
) -> list[KeywordTagConfigResponse]:
    """查询关键词标签映射列表。"""
    repo = KeywordTagConfigRepository(db)
    rows = repo.find_all(offset=offset, limit=limit, active_only=active_only)
    return [_model_to_response(r) for r in rows]


@router.get("/count")
def count_keyword_tags(
    active_only: bool = Query(default=False, description="仅统计活跃的映射"),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """统计关键词标签映射总数。"""
    repo = KeywordTagConfigRepository(db)
    total = repo.count_all(active_only=active_only)
    return {"total": total}


@router.get("/{config_id}", response_model=KeywordTagConfigResponse)
def get_keyword_tag(
    config_id: int,
    db: Session = Depends(get_db),
) -> KeywordTagConfigResponse:
    """查询单条关键词标签映射。"""
    repo = KeywordTagConfigRepository(db)
    model = repo.find_by_id(config_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"关键词标签 ID={config_id} 不存在")
    return _model_to_response(model)


@router.post("/", response_model=KeywordTagConfigResponse, status_code=201)
def create_keyword_tag(
    body: KeywordTagConfigCreate,
    db: Session = Depends(get_db),
) -> KeywordTagConfigResponse:
    """新增关键词标签映射。"""
    repo = KeywordTagConfigRepository(db)
    try:
        model = repo.create(body.model_dump())
        return _model_to_response(model)
    except Exception as e:
        logger.exception("创建关键词标签失败")
        raise HTTPException(status_code=409, detail=f"创建失败: {e}") from e


@router.put("/{config_id}", response_model=KeywordTagConfigResponse)
def update_keyword_tag(
    config_id: int,
    body: KeywordTagConfigUpdate,
    db: Session = Depends(get_db),
) -> KeywordTagConfigResponse:
    """更新关键词标签映射（仅更新提供的字段）。"""
    repo = KeywordTagConfigRepository(db)
    # 仅传递非 None 的字段
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="至少需要提供一个更新字段")
    try:
        model = repo.update(config_id, updates)
        if model is None:
            raise HTTPException(status_code=404, detail=f"关键词标签 ID={config_id} 不存在")
        return _model_to_response(model)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("更新关键词标签失败")
        raise HTTPException(status_code=409, detail=f"更新失败: {e}") from e


@router.delete("/{config_id}")
def delete_keyword_tag(
    config_id: int,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """删除关键词标签映射（软删除，设置 is_active=False）。"""
    repo = KeywordTagConfigRepository(db)
    ok = repo.delete(config_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"关键词标签 ID={config_id} 不存在")
    return {"status": "deleted", "id": str(config_id)}


@router.post("/batch-import")
def batch_import_keyword_tags(
    body: dict[str, str],
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """批量导入关键词标签映射。

    body 格式: {"关键词1": "标签1", "关键词2": "标签2", ...}
    ON CONFLICT DO UPDATE: 已存在的 keyword 会更新其 tag。
    """
    if not body:
        raise HTTPException(status_code=400, detail="请求体为空，需要 keyword:tag 映射")
    repo = KeywordTagConfigRepository(db)
    try:
        result = repo.batch_import(body)
        return result
    except Exception as e:
        logger.exception("批量导入关键词标签失败")
        raise HTTPException(status_code=500, detail=f"批量导入失败: {e}") from e
