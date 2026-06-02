"""通用分页响应模型。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """通用分页响应。

    Attributes:
        items: 当前页数据列表。
        total: 符合条件的总记录数。
        offset: 当前偏移量。
        limit: 每页最大条数。
    """

    items: list[T]
    total: int
    offset: int
    limit: int
