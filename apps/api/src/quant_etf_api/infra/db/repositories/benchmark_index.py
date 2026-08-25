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

    def find_all(self) -> list[BenchmarkIndexModel]:
        """查询全部指数（含已停用），按代码升序。"""
        return self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()

    def find_by_code(self, index_code: str) -> BenchmarkIndexModel | None:
        """按主键查询单条指数记录。

        Args:
            index_code: 指数代码。

        Returns:
            指数行，不存在时返回 None。
        """
        return self._db.get(BenchmarkIndexModel, index_code)

    def find_active_by_codes(self, index_codes: list[str]) -> list[BenchmarkIndexModel]:
        """按代码列表查询活跃指数。

        Args:
            index_codes: 指数代码列表。

        Returns:
            活跃指数行列表，保持传入代码的顺序。
        """
        active = {r.index_code: r for r in self.find_active()}
        return [active[c] for c in index_codes if c in active]
