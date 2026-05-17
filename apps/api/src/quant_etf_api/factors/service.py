"""FactorService：因子计算编排与持久化。

负责以下职责：
1. 将 FactorRegistry 中的 FactorSpec 同步到 factor_definition 表（幂等）
2. 批量加载 90 天回望的上下文数据
3. 对全量活跃 ETF × 全量注册因子调用 compute()
4. 使用 PostgreSQL partial index ON CONFLICT upsert 写入 etf_factor_value
5. 提供时间序列和横截面查询接口
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.infra.db.models.core import (
    EtfDailyBarModel,
    EtfDailyShareModel,
    EtfFactorValueModel,
    EtfUniverseModel,
    FactorDefinitionModel,
)
from quant_etf_api.schemas.signal import FactorRow

if TYPE_CHECKING:
    from quant_etf_api.factors.registry import FactorRegistry

logger = logging.getLogger(__name__)

# 回望自然日数，覆盖 60 个交易日（约 84 个自然日）再加安全余量
_LOOKBACK_DAYS = 90


class FactorService:
    """因子计算与持久化服务。

    Args:
        db: SQLAlchemy 同步 Session。
        registry: 已注册全部内置因子的 FactorRegistry。
    """

    def __init__(self, db: Session, registry: "FactorRegistry") -> None:
        self._db = db
        self._registry = registry

    # ==================================================================
    # 公开接口
    # ==================================================================

    def compute_and_store(self, trade_date: date) -> dict:
        """计算指定交易日全量 ETF × 全量因子并写入 DB。

        执行流程：
        1. _ensure_factor_definitions：同步因子定义到 DB（幂等）
        2. 查询活跃 ETF 列表
        3. _load_context：批量加载 90 天回望数据
        4. 对每个 ETF × 每个因子调用 compute()
        5. upsert（partial index ON CONFLICT DO UPDATE）写入 etf_factor_value

        Args:
            trade_date: 要计算的交易日。

        Returns:
            汇总统计字典，包含 etf_count / factor_count / upsert_count / errors。
        """
        self._ensure_factor_definitions()

        etfs = (
            self._db.query(EtfUniverseModel)
            .filter(EtfUniverseModel.is_active.is_(True))
            .order_by(EtfUniverseModel.etf_code)
            .all()
        )
        if not etfs:
            logger.warning("compute_and_store: 无活跃 ETF，跳过因子计算")
            return {"etf_count": 0, "factor_count": 0, "upsert_count": 0, "errors": 0}

        etf_codes = [e.etf_code for e in etfs]
        ctx = self._load_context(trade_date, etf_codes)
        computers = self._registry.all()

        rows_to_upsert: list[dict] = []
        errors = 0

        for etf in etfs:
            for computer in computers:
                try:
                    fv = computer.compute(etf.etf_code, trade_date, ctx)
                    rows_to_upsert.append(
                        {
                            "trade_date": trade_date,
                            "etf_code": etf.etf_code,
                            "factor_id": fv.factor_id,
                            "factor_value_numeric": fv.numeric,
                            "factor_value_text": fv.text,
                            "factor_payload": fv.payload or None,
                            "strategy_id": None,  # 独立因子，strategy_id 为 NULL
                        }
                    )
                except Exception:
                    errors += 1
                    logger.warning(
                        "因子计算失败: etf=%s factor=%s",
                        etf.etf_code,
                        computer.spec.factor_id,
                        exc_info=True,
                    )

        upsert_count = self._bulk_upsert(rows_to_upsert)
        logger.info(
            "因子计算完成: trade_date=%s etf=%d factor=%d upsert=%d errors=%d",
            trade_date,
            len(etfs),
            len(computers),
            upsert_count,
            errors,
        )
        return {
            "etf_count": len(etfs),
            "factor_count": len(computers),
            "upsert_count": upsert_count,
            "errors": errors,
        }

    def factor_history(
        self,
        factor_id: str,
        etf_code: str,
        start_date: date,
        end_date: date,
    ) -> list[FactorRow]:
        """查询单因子在单 ETF 上的时间序列。

        仅返回独立因子值（strategy_id IS NULL）。

        Args:
            factor_id: 因子标识。
            etf_code: ETF 代码。
            start_date: 开始日期（含）。
            end_date: 结束日期（含）。

        Returns:
            按 trade_date 升序排列的 FactorRow 列表。
        """
        try:
            rows = (
                self._db.query(EtfFactorValueModel)
                .filter(
                    and_(
                        EtfFactorValueModel.factor_id == factor_id,
                        EtfFactorValueModel.etf_code == etf_code,
                        EtfFactorValueModel.trade_date >= start_date,
                        EtfFactorValueModel.trade_date <= end_date,
                        EtfFactorValueModel.strategy_id.is_(None),
                    )
                )
                .order_by(EtfFactorValueModel.trade_date.asc())
                .all()
            )
            return [_row_to_factor_row(r) for r in rows]
        except Exception:
            logger.warning("factor_history 查询失败", exc_info=True)
            return []

    def factor_cross_section(
        self,
        factor_id: str,
        trade_date: date,
    ) -> list[FactorRow]:
        """查询单因子在某交易日的全 ETF 横截面快照。

        仅返回独立因子值（strategy_id IS NULL）。

        Args:
            factor_id: 因子标识。
            trade_date: 查询日期。

        Returns:
            按 etf_code 升序排列的 FactorRow 列表。
        """
        try:
            rows = (
                self._db.query(EtfFactorValueModel)
                .filter(
                    and_(
                        EtfFactorValueModel.factor_id == factor_id,
                        EtfFactorValueModel.trade_date == trade_date,
                        EtfFactorValueModel.strategy_id.is_(None),
                    )
                )
                .order_by(EtfFactorValueModel.etf_code.asc())
                .all()
            )
            return [_row_to_factor_row(r) for r in rows]
        except Exception:
            logger.warning("factor_cross_section 查询失败", exc_info=True)
            return []

    # ==================================================================
    # 内部方法
    # ==================================================================

    def _ensure_factor_definitions(self) -> None:
        """将注册表中的 FactorSpec 同步到 factor_definition 表（幂等）。

        策略：先查询 DB 中已有的 factor_id 集合，再插入缺失条目，跳过已存在的。
        不更新已存在记录（版本升级通过新 migration 处理）。
        """
        specs = self._registry.specs()
        if not specs:
            return

        existing_ids: set[str] = {
            row[0] for row in self._db.query(FactorDefinitionModel.factor_id).all()
        }
        new_specs = [s for s in specs if s.factor_id not in existing_ids]
        if not new_specs:
            return

        for spec in new_specs:
            self._db.add(
                FactorDefinitionModel(
                    factor_id=spec.factor_id,
                    name=spec.name,
                    category=spec.category,
                    version=spec.version,
                    description=spec.description,
                    owner_plugin=None,  # 独立因子，无 owner_plugin
                )
            )
        try:
            self._db.commit()
            logger.info("同步因子定义完成: 新增 %d 条", len(new_specs))
        except Exception:
            self._db.rollback()
            logger.warning("同步因子定义失败", exc_info=True)

    def _load_context(self, trade_date: date, etf_codes: list[str]) -> FactorContext:
        """批量加载 90 天回望的所有数据，构建 FactorContext。

        覆盖 Return60dComputer 所需的 61 个收盘价（约 84 个自然日）。
        etf_shares 加载 trade_date 当日快照数据（shares_delta_pct 为当日值）。

        Args:
            trade_date: 目标交易日。
            etf_codes: 活跃 ETF 代码列表。

        Returns:
            填充了 etf_bars / etf_shares 的 FactorContext（index_bars 留空）。
        """
        lookback_start = trade_date - timedelta(days=_LOOKBACK_DAYS)

        bar_rows = (
            self._db.query(EtfDailyBarModel)
            .filter(
                and_(
                    EtfDailyBarModel.trade_date >= lookback_start,
                    EtfDailyBarModel.trade_date <= trade_date,
                    EtfDailyBarModel.etf_code.in_(etf_codes),
                )
            )
            .all()
        )

        # 仅加载当日份额快照，shares_delta_pct 是当日计算的差值
        share_rows = (
            self._db.query(EtfDailyShareModel)
            .filter(
                and_(
                    EtfDailyShareModel.trade_date == trade_date,
                    EtfDailyShareModel.etf_code.in_(etf_codes),
                )
            )
            .all()
        )

        return FactorContext(
            etf_bars={(r.etf_code, r.trade_date): r for r in bar_rows},
            etf_shares={(r.etf_code, r.trade_date): r for r in share_rows},
        )

    def _bulk_upsert(self, rows: list[dict]) -> int:
        """批量 upsert etf_factor_value，使用 partial unique index 处理 NULL strategy_id。

        ON CONFLICT 目标：partial index uq_etf_factor_value_builtin
        （trade_date, etf_code, factor_id WHERE strategy_id IS NULL）。
        冲突时更新 factor_value_numeric / factor_value_text / factor_payload。

        index_where 与 migration 中的 postgresql_where=sa.text("strategy_id IS NULL")
        语义完全一致，PostgreSQL 能正确识别为同一 partial index。

        Args:
            rows: 待写入的字典列表。

        Returns:
            实际写入（insert + update）的记录数，异常时返回 0。
        """
        if not rows:
            return 0

        stmt = insert(EtfFactorValueModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_date", "etf_code", "factor_id"],
            index_where=EtfFactorValueModel.strategy_id.is_(None),
            set_={
                "factor_value_numeric": stmt.excluded.factor_value_numeric,
                "factor_value_text": stmt.excluded.factor_value_text,
                "factor_payload": stmt.excluded.factor_payload,
            },
        )
        try:
            result = self._db.execute(stmt)
            self._db.commit()
            return result.rowcount
        except Exception:
            self._db.rollback()
            logger.error("_bulk_upsert 失败，已回滚", exc_info=True)
            return 0


def _row_to_factor_row(row: EtfFactorValueModel) -> FactorRow:
    """将 ORM 行转换为 FactorRow schema。"""
    return FactorRow(
        trade_date=row.trade_date,
        etf_code=row.etf_code,
        factor_id=row.factor_id,
        factor_value_numeric=row.factor_value_numeric,
        factor_value_text=row.factor_value_text,
        factor_payload=row.factor_payload or {},
        strategy_id=row.strategy_id,
    )
