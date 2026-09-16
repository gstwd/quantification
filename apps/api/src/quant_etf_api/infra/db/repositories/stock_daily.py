"""Tushare 个股每日指标与资金流向仓库。"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from quant_etf_api.infra.db.models.stock import (
    StockDailyBasicModel,
    StockMoneyflowModel,
)
from quant_etf_api.infra.db.repositories.base import BaseRepository

_PG_INSERT_CHUNK_SIZE = 500


class _StockDailyRepository(BaseRepository):
    """个股日频明细仓库公共实现（每日指标 / 资金流向）。"""

    _model: type[Any]
    _constraint: str
    _update_columns: set[str]

    def latest_date(self) -> date | None:
        """查询当前表最新交易日期。"""
        return self._db.query(func.max(self._model.trade_date)).scalar()

    def count_trade_date(self, trade_date: date) -> int:
        """统计指定交易日已写入的行数。"""
        return int(
            self._db.query(func.count()).filter(self._model.trade_date == trade_date).scalar() or 0
        )

    def trading_dates(self, start: date, end: date) -> list[date]:
        """查询区间内已写入的交易日（去重升序）。"""
        rows = (
            self._db.query(self._model.trade_date)
            .filter(
                self._model.trade_date >= start,
                self._model.trade_date <= end,
            )
            .distinct()
            .order_by(self._model.trade_date.asc())
            .all()
        )
        return [r[0] for r in rows]

    def delete_by_code(self, stock_code: str) -> int:
        """删除单只股票的全部行（供全量重拉使用，调用方负责事务）。"""
        result = (
            self._db.query(self._model)
            .filter(self._model.stock_code == stock_code)
            .delete(synchronize_session=False)
        )
        return int(result)

    def bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """批量幂等写入，仅在业务字段变化时更新。"""
        if not rows:
            return 0

        def _build(chunk: list[dict[str, Any]]) -> Any:
            """构造单块 ON CONFLICT DO UPDATE 语句。"""
            stmt = pg_insert(self._model).values(chunk)
            business_columns = self._update_columns - {"ingested_at"}
            return stmt.on_conflict_do_update(
                constraint=self._constraint,
                set_={column: getattr(stmt.excluded, column) for column in self._update_columns},
                # ingested_at 每次摄取都会变化，不能参与判定；否则重复修复会
                # 为所有冲突行生成新版本，造成大表和索引持续膨胀。
                where=or_(
                    *[
                        getattr(self._model, column).is_distinct_from(
                            getattr(stmt.excluded, column)
                        )
                        for column in sorted(business_columns)
                    ]
                ),
            )

        for start in range(0, len(rows), _PG_INSERT_CHUNK_SIZE):
            self._db.execute(_build(rows[start : start + _PG_INSERT_CHUNK_SIZE]))
        return len(rows)


class StockDailyBasicRepository(_StockDailyRepository):
    """stock_daily_basic 写入门禁。"""

    _model = StockDailyBasicModel
    _constraint = "uq_stock_daily_basic"
    _update_columns = {
        "close",
        "turnover_rate",
        "turnover_rate_f",
        "volume_ratio",
        "pe",
        "pe_ttm",
        "pb",
        "ps",
        "ps_ttm",
        "dv_ratio",
        "dv_ttm",
        "total_share",
        "float_share",
        "free_share",
        "total_mv",
        "circ_mv",
        "limit_status",
        "source",
        "ingested_at",
    }


class StockMoneyflowRepository(_StockDailyRepository):
    """stock_moneyflow 写入门禁。"""

    _model = StockMoneyflowModel
    _constraint = "uq_stock_moneyflow"
    _update_columns = {
        "buy_sm_vol",
        "buy_sm_amount",
        "sell_sm_vol",
        "sell_sm_amount",
        "buy_md_vol",
        "buy_md_amount",
        "sell_md_vol",
        "sell_md_amount",
        "buy_lg_vol",
        "buy_lg_amount",
        "sell_lg_vol",
        "sell_lg_amount",
        "buy_elg_vol",
        "buy_elg_amount",
        "sell_elg_vol",
        "sell_elg_amount",
        "net_mf_vol",
        "net_mf_amount",
        "source",
        "ingested_at",
    }
