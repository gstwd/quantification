"""仓库基类。"""

from __future__ import annotations

from sqlalchemy.orm import Session


class BaseRepository:
    """仓库基类，提供统一的 Session 注入。

    所有仓库方法均为只读查询，不管理事务（commit / rollback 由调用方负责）。
    """

    def __init__(self, db: Session) -> None:
        self._db = db
