"""ETF 池仓库。"""

from __future__ import annotations

from quant_etf_api.infra.db.models.core import EtfUniverseModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class EtfUniverseRepository(BaseRepository):
    """EtfUniverseModel 的只读查询仓库。"""

    def find_all_active(self) -> list[EtfUniverseModel]:
        """获取所有活跃 ETF，按代码排序。"""
        return (
            self._db.query(EtfUniverseModel)
            .filter(EtfUniverseModel.is_active.is_(True))
            .order_by(EtfUniverseModel.etf_code)
            .all()
        )

    def find_active_paginated(
        self, offset: int = 0, limit: int = 50
    ) -> tuple[list[EtfUniverseModel], int]:
        """分页查询活跃 ETF，按代码排序。

        Args:
            offset: 偏移量。
            limit: 每页最大条数。

        Returns:
            (items, total) 元组。
        """
        base_q = self._db.query(EtfUniverseModel).filter(EtfUniverseModel.is_active.is_(True))
        total = base_q.count()
        rows = base_q.order_by(EtfUniverseModel.etf_code).offset(offset).limit(limit).all()
        return rows, total

    def find_by_code(self, etf_code: str) -> EtfUniverseModel | None:
        """按主键查询单条 ETF 记录。"""
        return self._db.get(EtfUniverseModel, etf_code)

    def find_active_codes(self) -> list[str]:
        """获取所有活跃 ETF 的代码列表。"""
        rows = (
            self._db.query(EtfUniverseModel.etf_code)
            .filter(EtfUniverseModel.is_active.is_(True))
            .all()
        )
        return [r.etf_code for r in rows]

    def find_by_codes(self, codes: list[str]) -> list[EtfUniverseModel]:
        """按代码列表批量查询。"""
        return self._db.query(EtfUniverseModel).filter(EtfUniverseModel.etf_code.in_(codes)).all()

    def count_active(self) -> int:
        """活跃 ETF 计数。"""
        return self._db.query(EtfUniverseModel).filter(EtfUniverseModel.is_active.is_(True)).count()
