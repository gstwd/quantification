"""日频市场指标仓库：market_indicator 的幂等写入与按指标读取。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from quant_etf_api.domain.market_data.indicators import MarketIndicatorPoint
from quant_etf_api.infra.db.models.core import MarketIndicatorModel
from quant_etf_api.infra.db.repositories.base import BaseRepository
from quant_etf_api.infra.time import utcnow_aware

# 单条 INSERT 的行数上限（PostgreSQL 绑定参数总数 65535）
_CHUNK_SIZE = 500


class MarketIndicatorRepository(BaseRepository):
    """market_indicator 表仓库（事务由调用方管理）。"""

    def upsert(self, points: Iterable[MarketIndicatorPoint], *, overwrite: bool) -> int:
        """分块幂等写入。

        Args:
            points: 观测点。
            overwrite: True 时覆盖已有值（修复 / 重建）；False 时跳过已存在的 (指标, 交易日)。

        Returns:
            提交写入的行数。
        """
        rows = [
            {
                "indicator_code": p.indicator_code,
                "trade_date": p.trade_date,
                "value": float(p.value),
                "source": p.source,
                "ingested_at": utcnow_aware(),
            }
            for p in points
        ]
        for start in range(0, len(rows), _CHUNK_SIZE):
            statement = pg_insert(MarketIndicatorModel).values(rows[start : start + _CHUNK_SIZE])
            if overwrite:
                statement = statement.on_conflict_do_update(
                    constraint="uq_market_indicator",
                    set_={
                        "value": statement.excluded.value,
                        "source": statement.excluded.source,
                        "ingested_at": statement.excluded.ingested_at,
                    },
                )
            else:
                statement = statement.on_conflict_do_nothing(constraint="uq_market_indicator")
            self._db.execute(statement)
        return len(rows)

    def latest_dates(self, codes: Sequence[str]) -> dict[str, date]:
        """各指标已入库的最新交易日。

        Args:
            codes: 指标代码。

        Returns:
            指标代码 → 最新交易日（无数据的指标不出现）。
        """
        rows = (
            self._db.query(
                MarketIndicatorModel.indicator_code, sa.func.max(MarketIndicatorModel.trade_date)
            )
            .filter(MarketIndicatorModel.indicator_code.in_(list(codes)))
            .group_by(MarketIndicatorModel.indicator_code)
            .all()
        )
        return {str(code): latest for code, latest in rows if latest is not None}

    def find_all_as_map(self, codes: Sequence[str] | None = None) -> dict[str, dict[str, float]]:
        """加载指标历史，返回 indicator_code → {交易日 ISO: 值}（与宏观指标映射同一契约）。

        Args:
            codes: 只加载这些指标；None 表示全部。

        Returns:
            映射字典。
        """
        query = self._db.query(
            MarketIndicatorModel.indicator_code,
            MarketIndicatorModel.trade_date,
            MarketIndicatorModel.value,
        )
        if codes is not None:
            query = query.filter(MarketIndicatorModel.indicator_code.in_(list(codes)))
        result: dict[str, dict[str, float]] = {}
        for code, trade_date, value in query.all():
            result.setdefault(str(code), {})[trade_date.isoformat()] = float(value)
        return result

    def find_latest(self, code: str, limit: int) -> list[tuple[date, float]]:
        """读取单个指标最近 limit 条观测（按交易日升序返回）。

        Args:
            code: 指标代码。
            limit: 条数上限。

        Returns:
            (交易日, 值) 列表。
        """
        rows = (
            self._db.query(MarketIndicatorModel.trade_date, MarketIndicatorModel.value)
            .filter(MarketIndicatorModel.indicator_code == code)
            .order_by(MarketIndicatorModel.trade_date.desc())
            .limit(limit)
            .all()
        )
        return [(d, float(v)) for d, v in reversed(rows)]

    def find_series(self, code: str, start: date, end: date) -> list[tuple[date, float]]:
        """读取单个指标在区间内的序列（升序）。

        Args:
            code: 指标代码。
            start: 起始日（含）。
            end: 截止日（含）。

        Returns:
            (交易日, 值) 列表。
        """
        rows = (
            self._db.query(MarketIndicatorModel.trade_date, MarketIndicatorModel.value)
            .filter(
                MarketIndicatorModel.indicator_code == code,
                MarketIndicatorModel.trade_date >= start,
                MarketIndicatorModel.trade_date <= end,
            )
            .order_by(MarketIndicatorModel.trade_date.asc())
            .all()
        )
        return [(d, float(v)) for d, v in rows]
