"""个股元数据仓库（stock_universe 查询与写入门禁）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from quant_etf_api.infra.db.models.stock import StockUniverseModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class StockUniverseRepository(BaseRepository):
    """stock_universe 查询与批量 upsert 仓库。"""

    def find_by_code(self, stock_code: str) -> StockUniverseModel | None:
        """按股票代码查询个股元数据行。"""
        return (
            self._db.query(StockUniverseModel)
            .filter(StockUniverseModel.stock_code == stock_code)
            .first()
        )

    def find_all(
        self,
        codes: list[str] | None = None,
        only_active: bool = False,
    ) -> list[StockUniverseModel]:
        """查询个股元数据；codes 非空时按给定列表过滤。"""
        query = self._db.query(StockUniverseModel)
        if codes:
            query = query.filter(StockUniverseModel.stock_code.in_(codes))
        if only_active:
            query = query.filter(StockUniverseModel.is_active.is_(True))
        return query.order_by(StockUniverseModel.stock_code.asc()).all()

    def find_codes(self, only_active: bool = False) -> list[str]:
        """返回个股代码列表（可选仅活跃）。"""
        query = self._db.query(StockUniverseModel.stock_code)
        if only_active:
            query = query.filter(StockUniverseModel.is_active.is_(True))
        rows = query.order_by(StockUniverseModel.stock_code.asc()).all()
        return [r[0] for r in rows]

    def search(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        keyword: str | None = None,
        industry_code: str | None = None,
        status: str | None = None,
    ) -> tuple[list[StockUniverseModel], int]:
        """分页检索个股元数据（支持代码/名称关键字与行业、状态过滤）。"""
        query = self._db.query(StockUniverseModel)
        if keyword:
            pattern = f"%{keyword.strip()}%"
            query = query.filter(
                or_(
                    StockUniverseModel.stock_code.ilike(pattern),
                    StockUniverseModel.name_cn.ilike(pattern),
                )
            )
        if industry_code:
            query = query.filter(StockUniverseModel.industry_code == industry_code)
        if status == "active":
            query = query.filter(StockUniverseModel.is_active.is_(True))
        elif status == "delisted":
            query = query.filter(StockUniverseModel.is_active.is_(False))

        total = query.with_entities(func.count(StockUniverseModel.stock_code)).scalar() or 0
        rows = query.order_by(StockUniverseModel.stock_code.asc()).offset(offset).limit(limit).all()
        return rows, total

    def bulk_upsert(
        self,
        rows: list[dict[str, Any]],
        update_cols: set[str],
    ) -> int:
        """批量幂等写入个股元数据；冲突时仅更新指定列。"""
        if not rows:
            return 0
        stmt = pg_insert(StockUniverseModel).values(rows)
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=[StockUniverseModel.stock_code],
                set_={
                    col: getattr(stmt.excluded, col) for col in update_cols if col != "stock_code"
                },
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=[StockUniverseModel.stock_code])
        self._db.execute(stmt)
        return len(rows)

    def latest_data_date(self) -> date | None:
        """查询个股质量快照中最新的 data_end_date（用于总览展示）。"""
        return self._db.query(func.max(StockUniverseModel.data_end_date)).scalar()
