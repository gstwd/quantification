"""基准指数数据仓库。

提供 BenchmarkIndexModel 的查询操作。
"""

from __future__ import annotations

from quant_etf_api.infra.db.models.core import BenchmarkIndexModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class BenchmarkIndexRepository(BaseRepository):
    """基准指数仓库。"""

    def find_active(self) -> list[BenchmarkIndexModel]:
        """查询所有活跃指数。

        Returns:
            is_active=True 的 BenchmarkIndexModel 列表。
        """
        return (
            self._db.query(BenchmarkIndexModel)
            .filter(BenchmarkIndexModel.is_active == True)  # noqa: E712
            .order_by(BenchmarkIndexModel.index_code)
            .all()
        )
