"""指数信号仓库：index_signal 表的查询与写入门禁。"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from quant_etf_api.infra.db.models.core import IndexSignalModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class IndexSignalRepository(BaseRepository):
    """IndexSignalModel 的查询与写入仓库。

    策略层（StrategyExecutionService）写入信号时统一走本仓库，
    避免业务代码直接拼接 ORM/insert 语句。
    """

    def find_by_strategy_date(self, strategy_id: str, trade_date: date) -> list[IndexSignalModel]:
        """查询指定策略在指定交易日的信号记录。

        Args:
            strategy_id: 策略 ID。
            trade_date: 交易日。

        Returns:
            信号行列表。
        """
        return (
            self._db.query(IndexSignalModel)
            .filter(
                IndexSignalModel.strategy_id == strategy_id,
                IndexSignalModel.trade_date == trade_date,
            )
            .order_by(IndexSignalModel.index_code)
            .all()
        )

    def delete_by_strategy_date(self, strategy_id: str, trade_date: date) -> None:
        """删除指定策略在指定交易日的旧信号（配置修改后不残留旧数据）。

        Args:
            strategy_id: 策略 ID。
            trade_date: 交易日。
        """
        self._db.execute(
            delete(IndexSignalModel).where(
                IndexSignalModel.strategy_id == strategy_id,
                IndexSignalModel.trade_date == trade_date,
            )
        )

    def bulk_insert(self, rows: list[dict[str, Any]]) -> None:
        """批量插入信号行（调用方负责事务提交）。

        Args:
            rows: 信号行字典列表。
        """
        if not rows:
            return
        self._db.execute(insert(IndexSignalModel).values(rows))
