"""宏观指标数据仓库。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from quant_etf_api.infra.db.models.core import MacroIndicatorModel
from quant_etf_api.infra.db.repositories.base import BaseRepository


class MacroIndicatorRepository(BaseRepository):
    """MacroIndicatorModel 的只读查询仓库。"""

    def find_all_as_map(self) -> dict[str, dict[str, float]]:
        """加载全部宏观指标，返回 indicator_code → {period: value} 的映射。

        Returns:
            key=indicator_code, value={period_date: value} 的字典
            （period_date 为空时回退到 period 字符串作为 key）。
        """
        rows = self._db.query(MacroIndicatorModel).all()
        result: dict[str, dict[str, float]] = {}
        for row in rows:
            code = row.indicator_code
            if code not in result:
                result[code] = {}
            # key 统一使用 period_date（为空时回退到 period 字符串），
            # 与 FactorContext 的 {period_date: value} 契约保持一致
            result[code][str(row.period_date or row.period)] = row.value
        return result

    def find_by_code_limit(self, indicator_code: str, limit: int) -> list[MacroIndicatorModel]:
        """按指标代码查询最近 N 条记录（按 period 倒序）。"""
        return (
            self._db.query(MacroIndicatorModel)
            .filter(MacroIndicatorModel.indicator_code == indicator_code)
            .order_by(MacroIndicatorModel.period.desc())
            .limit(limit)
            .all()
        )

    def find_latest_ingested_at(self) -> datetime | None:
        """查询宏观指标最近一次入库时间。"""
        return self._db.query(func.max(MacroIndicatorModel.ingested_at)).scalar()

    def find_by_codes(self, indicator_codes: list[str]) -> list[MacroIndicatorModel]:
        """按指标代码列表查询全部记录。"""
        return (
            self._db.query(MacroIndicatorModel)
            .filter(MacroIndicatorModel.indicator_code.in_(indicator_codes))
            .all()
        )
