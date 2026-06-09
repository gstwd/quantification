"""因子计算编排与持久化（基于指数数据）。

负责以下职责：
1. 将 FactorRegistry 中的 FactorSpec 与 factor_definition 表双向同步（幂等）
2. 批量加载 90 天回望的指数上下文数据
3. 对全量指数 × 全量已启用因子调用 compute()
4. 使用 PostgreSQL partial index ON CONFLICT upsert 写入 index_factor_value
5. 提供横截面和时间序列查询，支持按需自动计算缺失数据
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, TYPE_CHECKING

from sqlalchemy import and_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    FactorDefinitionModel,
    IndexDailyBarModel,
    IndexFactorValueModel,
    IndexValuationModel,
    MacroIndicatorModel,
)
from quant_etf_api.infra.db.repositories.factor_definition import FactorDefinitionRepository
from quant_etf_api.infra.db.repositories.index_factor_value import IndexFactorValueRepository
from quant_etf_api.schemas.factor import CrossSectionRow
from quant_etf_api.schemas.signal import FactorRow

if TYPE_CHECKING:
    from quant_etf_api.factors.registry import FactorRegistry

logger = logging.getLogger(__name__)

# 派生百分位因子配置：(基础因子 ID, 派生因子 ID, 回望天数)
_PERCENTILE_DERIVATIONS: list[tuple[str, str, int]] = [
    ("erp", "erp_percentile", 730),
]

# 默认回望自然日数（当注册表中无因子时使用）
_DEFAULT_LOOKBACK_DAYS = 90


def _get_max_lookback_days(registry: "FactorRegistry") -> int:
    """从注册表中获取所有因子所需的最大回望自然日数。

    Args:
        registry: 因子注册表。

    Returns:
        最大回望自然日数。
    """
    try:
        max_days = max(
            (c.spec.lookback_days for c in registry.all()),
            default=_DEFAULT_LOOKBACK_DAYS,
        )
        return max_days
    except Exception:
        return _DEFAULT_LOOKBACK_DAYS


class FactorService:
    """因子计算与持久化服务（基于指数数据）。

    Args:
        db: SQLAlchemy 同步 Session。
        registry: 已注册全部内置因子的 FactorRegistry。
    """

    def __init__(self, db: Session, registry: "FactorRegistry") -> None:
        """初始化因子服务。

        Args:
            db: SQLAlchemy 同步 Session。
            registry: 因子注册表。
        """
        self._db = db
        self._registry = registry
        self._repo = FactorDefinitionRepository(db)
        self._index_repo = IndexFactorValueRepository(db)

    # ==================================================================
    # 公开接口
    # ==================================================================

    def sync_factor_definitions(self) -> dict[str, int]:
        """将注册表中的 FactorSpec 同步到 factor_definition 表（幂等）。

        同步策略：
        - 代码中有、DB 中没有 → INSERT（新因子）
        - 代码和 DB 都有 → 仅更新 version、required_data（代码管控字段）
        - DB 中有、代码中没有 → 设为 is_active=False（保留历史数据关联）

        Returns:
            同步统计字典：new / updated / deactivated。
        """
        specs = {s.factor_id: s for s in self._registry.specs()}
        existing = {d.factor_id: d for d in self._repo.find_all()}

        new_count = 0
        update_count = 0
        deactivate_count = 0

        for factor_id, spec in specs.items():
            if factor_id not in existing:
                self._db.add(
                    FactorDefinitionModel(
                        factor_id=spec.factor_id,
                        name=spec.name,
                        category=spec.category,
                        version=spec.version,
                        description=spec.description,
                        required_data=spec.required_data,
                        owner_plugin=None,
                        is_active=True,
                    )
                )
                new_count += 1
            else:
                row = existing[factor_id]
                changed = False
                if row.version != spec.version:
                    row.version = spec.version
                    changed = True
                if row.required_data != spec.required_data:
                    row.required_data = spec.required_data
                    changed = True
                if changed:
                    update_count += 1

        for factor_id, row in existing.items():
            if factor_id not in specs and row.is_active:
                row.is_active = False
                deactivate_count += 1

        if new_count or update_count or deactivate_count:
            try:
                self._db.commit()
                logger.info(
                    "因子定义同步完成: 新增=%d 更新=%d 停用=%d",
                    new_count,
                    update_count,
                    deactivate_count,
                )
            except Exception:
                self._db.rollback()
                logger.warning("因子定义同步失败", exc_info=True)
                raise

        return {"new": new_count, "updated": update_count, "deactivated": deactivate_count}

    def compute_and_store(self, trade_date: date) -> dict[str, Any]:
        """计算指定交易日全量指数 × 全量已启用因子并写入 DB。

        执行流程：
        1. 查询 benchmark_index 中所有指数
        2. _load_context：批量加载 90 天回望的指数数据
        3. 对每个指数 × 每个已启用因子调用 compute()
        4. upsert（partial index ON CONFLICT DO UPDATE）写入 index_factor_value

        Args:
            trade_date: 要计算的交易日。

        Returns:
            汇总统计字典，包含 index_count / factor_count / upsert_count / errors。
        """
        indexes = self._db.query(BenchmarkIndexModel).order_by(BenchmarkIndexModel.index_code).all()
        if not indexes:
            logger.warning("compute_and_store: 无指数，跳过因子计算")
            return {"index_count": 0, "factor_count": 0, "upsert_count": 0, "errors": 0}

        index_codes = [idx.index_code for idx in indexes]
        ctx = self._load_context(trade_date, index_codes)

        active_ids = {d.factor_id for d in self._repo.find_active()}
        computers = [c for c in self._registry.all() if c.spec.factor_id in active_ids]

        if not computers:
            logger.warning("compute_and_store: 无已启用的因子，跳过计算")
            return {"index_count": len(indexes), "factor_count": 0, "upsert_count": 0, "errors": 0}

        rows_to_upsert: list[dict] = []
        errors = 0

        for idx in indexes:
            for computer in computers:
                try:
                    fv = computer.compute(idx.index_code, trade_date, ctx)
                    rows_to_upsert.append(
                        {
                            "trade_date": trade_date,
                            "index_code": idx.index_code,
                            "factor_id": fv.factor_id,
                            "factor_value_numeric": fv.numeric,
                            "factor_value_text": fv.text,
                            "factor_payload": fv.payload or None,
                            "strategy_id": None,
                        }
                    )
                except Exception:
                    errors += 1
                    logger.warning(
                        "因子计算失败: index=%s factor=%s",
                        idx.index_code,
                        computer.spec.factor_id,
                        exc_info=True,
                    )

        upsert_count = self._bulk_upsert(rows_to_upsert)
        logger.info(
            "因子计算完成: trade_date=%s index=%d factor=%d upsert=%d errors=%d",
            trade_date,
            len(indexes),
            len(computers),
            upsert_count,
            errors,
        )

        # 派生百分位因子后处理
        derived_count = self._compute_percentile_derived(trade_date, index_codes)

        return {
            "index_count": len(indexes),
            "factor_count": len(computers) + derived_count,
            "upsert_count": upsert_count + derived_count,
            "errors": errors,
        }

    def get_or_compute_cross_section(
        self, factor_id: str, force_recompute: bool = False
    ) -> tuple[date, list[CrossSectionRow]]:
        """获取指定因子的横截面数据，自动选择最新日期并按需计算。

        Args:
            factor_id: 因子标识。
            force_recompute: 是否强制重新计算，覆盖已有数据。

        Returns:
            (trade_date, cross_section_rows) 元组。

        Raises:
            ValueError: 无任何行情数据时抛出。
        """
        latest = self._index_repo.find_latest_date(factor_id)

        if latest is None or force_recompute:
            bar_latest = self._index_repo.find_latest_bar_date()
            if bar_latest is None:
                raise ValueError("无任何指数行情数据，无法计算因子")
            self.compute_and_store(bar_latest)
            latest = bar_latest

        rows = self._index_repo.find_cross_section(factor_id, latest)
        return latest, [
            CrossSectionRow(
                index_code=r[0],
                name_cn=r[1],
                factor_value_numeric=r[2],
                factor_value_text=r[3],
            )
            for r in rows
        ]

    def get_or_compute_time_series(
        self,
        factor_id: str,
        index_code: str,
        start_date: date,
        end_date: date,
        force_recompute: bool = False,
    ) -> list[FactorRow]:
        """获取因子时间序列，自动补算缺失日期后返回。

        Args:
            factor_id: 因子标识。
            index_code: 指数代码。
            start_date: 开始日期（含）。
            end_date: 截止日期（含）。
            force_recompute: 是否强制重新计算，覆盖已有数据。

        Returns:
            按 trade_date 升序排列的 FactorRow 列表。
        """
        if force_recompute:
            dates_to_compute = self._index_repo.find_all_bar_dates(index_code, start_date, end_date)
        else:
            dates_to_compute = self._index_repo.find_missing_dates(
                factor_id, index_code, start_date, end_date
            )

        for d in dates_to_compute:
            self.compute_and_store(d)

        rows = self._index_repo.find_factor_values(factor_id, index_code, start_date, end_date)
        return [_row_to_factor_row(r) for r in rows]

    def factor_history(
        self,
        factor_id: str,
        index_code: str,
        start_date: date,
        end_date: date,
    ) -> list[FactorRow]:
        """查询单因子在单指数上的时间序列。

        仅返回独立因子值（strategy_id IS NULL）。

        Args:
            factor_id: 因子标识。
            index_code: 指数代码。
            start_date: 开始日期（含）。
            end_date: 截止日期（含）。

        Returns:
            按 trade_date 升序排列的 FactorRow 列表。
        """
        try:
            rows = self._index_repo.find_factor_values(factor_id, index_code, start_date, end_date)
            return [_row_to_factor_row(r) for r in rows]
        except Exception:
            logger.warning("factor_history 查询失败", exc_info=True)
            return []

    # ==================================================================
    # 内部方法
    # ==================================================================

    def _load_context(self, trade_date: date, index_codes: list[str]) -> FactorContext:
        """批量加载回望数据，构建 FactorContext。

        回望窗口由注册表中所有因子的 lookback_days 最大值动态决定。

        Args:
            trade_date: 目标交易日。
            index_codes: 指数代码列表。

        Returns:
            填充了 index_bars / index_valuation / macro_indicators 的 FactorContext。
        """
        lookback_days = _get_max_lookback_days(self._registry)
        lookback_start = trade_date - timedelta(days=lookback_days)

        # 加载指数日线
        index_bar_rows = (
            self._db.query(IndexDailyBarModel)
            .filter(
                and_(
                    IndexDailyBarModel.trade_date >= lookback_start,
                    IndexDailyBarModel.trade_date <= trade_date,
                    IndexDailyBarModel.index_code.in_(index_codes),
                )
            )
            .all()
        )

        # 加载指数估值数据
        valuation_rows = (
            (
                self._db.query(IndexValuationModel)
                .filter(
                    and_(
                        IndexValuationModel.trade_date == trade_date,
                        IndexValuationModel.index_code.in_(index_codes),
                    )
                )
                .all()
            )
            if index_codes
            else []
        )

        # 加载宏观指标数据（LPR 等），取每个指标代码的全部历史记录
        macro_rows = (
            self._db.query(MacroIndicatorModel)
            .filter(MacroIndicatorModel.indicator_code.in_(["lpr1y", "lpr5y", "cpi", "pmi"]))
            .all()
        )
        macro_indicators: dict[str, dict[str, float]] = {}
        for row in macro_rows:
            code = row.indicator_code
            if code not in macro_indicators:
                macro_indicators[code] = {}
            macro_indicators[code][row.period] = row.value

        return FactorContext(
            index_bars={(r.index_code, r.trade_date): r for r in index_bar_rows},
            index_valuation={(r.index_code, r.trade_date): r for r in valuation_rows},
            macro_indicators=macro_indicators,
        )

    def _compute_percentile_derived(self, trade_date: date, index_codes: list[str]) -> int:
        """计算派生百分位因子（如 erp_percentile）。

        从 index_factor_value 表读取基础因子的历史值，
        计算当前值在历史分布中的百分位，写入派生因子。

        Args:
            trade_date: 目标交易日。
            index_codes: 指数代码列表。

        Returns:
            写入的派生因子记录数。
        """
        total_written = 0
        for base_factor_id, derived_factor_id, lookback_days in _PERCENTILE_DERIVATIONS:
            # 检查派生因子是否已启用
            derived_def = self._repo.find_by_id(derived_factor_id)
            if derived_def is not None and not derived_def.is_active:
                continue

            lookback_start = trade_date - timedelta(days=lookback_days)
            for index_code in index_codes:
                try:
                    # 查询历史基础因子值
                    history_rows = (
                        self._db.query(
                            IndexFactorValueModel.trade_date,
                            IndexFactorValueModel.factor_value_numeric,
                        )
                        .filter(
                            and_(
                                IndexFactorValueModel.factor_id == base_factor_id,
                                IndexFactorValueModel.index_code == index_code,
                                IndexFactorValueModel.trade_date >= lookback_start,
                                IndexFactorValueModel.trade_date <= trade_date,
                                IndexFactorValueModel.strategy_id.is_(None),
                                IndexFactorValueModel.factor_value_numeric.isnot(None),
                            )
                        )
                        .order_by(IndexFactorValueModel.trade_date)
                        .all()
                    )

                    if len(history_rows) < 10:
                        continue

                    # 获取当前值
                    current_val = None
                    for row in history_rows:
                        if row.trade_date == trade_date:
                            current_val = row.factor_value_numeric
                            break
                    if current_val is None:
                        continue

                    # 计算百分位：有多少比例的值 <= 当前值
                    all_values = [r.factor_value_numeric for r in history_rows]
                    count_below = sum(1 for v in all_values if v <= current_val)
                    percentile = round(count_below / len(all_values) * 100, 2)

                    # upsert 派生因子值
                    self._bulk_upsert(
                        [
                            {
                                "trade_date": trade_date,
                                "index_code": index_code,
                                "factor_id": derived_factor_id,
                                "factor_value_numeric": percentile,
                                "factor_value_text": None,
                                "factor_payload": {
                                    "base_factor": base_factor_id,
                                    "current_value": round(current_val, 4),
                                    "history_count": len(all_values),
                                    "lookback_days": lookback_days,
                                },
                                "strategy_id": None,
                            }
                        ]
                    )
                    total_written += 1

                except Exception:
                    logger.warning(
                        "百分位派生因子计算失败: index=%s base=%s derived=%s",
                        index_code,
                        base_factor_id,
                        derived_factor_id,
                        exc_info=True,
                    )

        if total_written:
            logger.info("百分位派生因子写入: count=%d", total_written)
        return total_written

    def _bulk_upsert(self, rows: list[dict[str, Any]]) -> int:
        """批量 upsert index_factor_value，使用 partial unique index 处理 NULL strategy_id。

        Args:
            rows: 待写入的字典列表。

        Returns:
            实际写入（insert + update）的记录数，异常时返回 0。
        """
        if not rows:
            return 0

        stmt = insert(IndexFactorValueModel).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["trade_date", "index_code", "factor_id"],
            index_where=IndexFactorValueModel.strategy_id.is_(None),
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


def _row_to_factor_row(row: IndexFactorValueModel) -> FactorRow:
    """将 ORM 行转换为 FactorRow schema。"""
    payload = row.factor_payload
    if isinstance(payload, str):
        import json

        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    return FactorRow(
        trade_date=row.trade_date,
        index_code=row.index_code,
        factor_id=row.factor_id,
        factor_value_numeric=row.factor_value_numeric,
        factor_value_text=row.factor_value_text,
        factor_payload=payload or {},
        strategy_id=row.strategy_id,
    )
