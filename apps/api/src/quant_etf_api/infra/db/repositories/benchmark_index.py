"""基准指数数据仓库。

提供 BenchmarkIndexModel 的查询操作。
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa

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

    def find_for_period(self, start_date: date) -> list[BenchmarkIndexModel]:
        """查询"回测区间起点仍在存续"的指数（point-in-time 标的池）。

        point-in-time 口径（消除幸存者偏差）：
        - ``is_active=True``：当前活跃指数；
        - ``delisting_date IS NULL``：退市日未知（历史停用行未登记日期），
          一律纳入，交由逐日行情缺口判定实际可交易区间；
        - ``delisting_date >= start_date``：退市日晚于区间起点的指数在回测
          窗口内仍然存续，必须纳入——旧实现只取 is_active=True，把这些指数
          整段剔除，等价于"只回测活到今天的资产"（幸存者偏差）。

        Args:
            start_date: 回测区间起始日期（含）。

        Returns:
            符合条件的 BenchmarkIndexModel 列表，按 index_code 升序。
        """
        return (
            self._db.query(BenchmarkIndexModel)
            .filter(
                sa.or_(
                    BenchmarkIndexModel.is_active.is_(True),
                    BenchmarkIndexModel.delisting_date.is_(None),
                    BenchmarkIndexModel.delisting_date >= start_date,
                )
            )
            .order_by(BenchmarkIndexModel.index_code)
            .all()
        )

    def count_active(self) -> int:
        """统计活跃指数数量。

        Returns:
            活跃指数总数。
        """
        return (
            self._db.query(BenchmarkIndexModel)
            .filter(BenchmarkIndexModel.is_active == True)  # noqa: E712
            .count()
        )

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
