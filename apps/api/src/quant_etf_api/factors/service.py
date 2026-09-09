"""因子计算编排与持久化（基于指数数据）。

负责以下职责：
1. 批量加载 90 天回望的指数上下文数据
2. 对全量指数 × 全量已启用因子调用 compute()
3. 使用 PostgreSQL partial index ON CONFLICT upsert 写入 index_factor_value
4. 提供横截面和时间序列查询，支持按需自动计算缺失数据

元数据同步（sync_factor_definitions）已迁移至 services/factor_admin_service.py，
因子值写入统一走 IndexFactorValueRepository 写入门禁。
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from quant_etf_api.factors.base import FactorContext
from quant_etf_api.factors.base import factor_params_hash
from quant_etf_api.factors.registry import max_lookback_days
from quant_etf_api.infra.db.models.core import (
    BenchmarkIndexModel,
    IndexFactorValueModel,
)
from quant_etf_api.infra.db.repositories.factor_definition import FactorDefinitionRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.index_factor_value import IndexFactorValueRepository
from quant_etf_api.infra.db.repositories.index_valuation import IndexValuationRepository
from quant_etf_api.infra.db.repositories.macro_indicator import MacroIndicatorRepository
from quant_etf_api.schemas.factor import CrossSectionRow
from quant_etf_api.schemas.signal import FactorRow

if TYPE_CHECKING:
    from quant_etf_api.factors.registry import FactorRegistry

logger = logging.getLogger(__name__)

# 指数扩散的 160 日均线、150 日快线与 25 日慢线需要 333 个连续交易日（含目标日）。
_PANEL_FACTOR_WINDOW_DAYS = 333
_BACKGROUND_ONLY_FACTOR_IDS = {"index_diffusion_ratio", "rrg_industry_match_score"}


def _get_max_lookback_days(registry: "FactorRegistry") -> int:
    """从注册表中获取所有因子所需的最大回望自然日数。

    统一委托 factors.registry.max_lookback_days，保证实时与回测口径一致。

    Args:
        registry: 因子注册表。

    Returns:
        最大回望自然日数。
    """
    return max_lookback_days(registry)


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

    def compute_and_store(self, trade_date: date) -> dict[str, Any]:
        """计算指定交易日全量指数 × 全量已启用因子并写入 DB。

        执行流程：
        1. 查询 benchmark_index 中所有指数
        2. _load_context：批量加载回望窗口内的指数数据（含多日估值，供 erp_percentile 等使用）
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
        active_ids = {d.factor_id for d in self._repo.find_active()}
        computers = [c for c in self._registry.all() if c.spec.factor_id in active_ids]

        if not computers:
            logger.warning("compute_and_store: 无已启用的因子，跳过计算")
            return {"index_count": len(indexes), "factor_count": 0, "upsert_count": 0, "errors": 0}

        include_composite_panels = any(
            computer.spec.factor_id in _BACKGROUND_ONLY_FACTOR_IDS for computer in computers
        )
        panel_dates = (
            self._panel_calculation_dates(trade_date) if include_composite_panels else [trade_date]
        )
        ctx = self._load_context(
            trade_date,
            index_codes,
            panel_dates=panel_dates,
            include_composite_panels=include_composite_panels,
        )

        builtin_rows: list[dict] = []
        param_rows: list[dict] = []
        errors = 0
        missing_count = 0
        start = time.perf_counter()

        for idx in indexes:
            for computer in computers:
                try:
                    fv = computer.compute(idx.index_code, trade_date, ctx)
                    if fv.numeric is None:
                        missing_count += 1
                    logger.debug(
                        "[factor] %s %s %s value=%s payload=%s",
                        trade_date,
                        idx.index_code,
                        fv.factor_id,
                        fv.numeric,
                        fv.payload,
                    )
                    params = dict(computer.spec.default_params or {})
                    params_hash = factor_params_hash(params) if params else ""
                    row_payload = {
                        "trade_date": trade_date,
                        "index_code": idx.index_code,
                        "factor_id": fv.factor_id,
                        "factor_value_numeric": fv.numeric,
                        "factor_value_text": fv.text,
                        "factor_payload": fv.payload or None,
                        "strategy_id": None,
                        "params_hash": params_hash,
                        "params": params or None,
                    }
                    if params_hash:
                        param_rows.append(row_payload)
                    else:
                        builtin_rows.append(row_payload)
                except Exception:
                    errors += 1
                    logger.warning(
                        "因子计算失败: index=%s factor=%s",
                        idx.index_code,
                        computer.spec.factor_id,
                        exc_info=True,
                    )

        upsert_count = self._index_repo.bulk_upsert_builtin(builtin_rows)
        upsert_count += self._index_repo.bulk_upsert_params(param_rows)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        panel_metrics = ctx.panels.get("panel_metrics") or {}
        logger.info(
            "[factor] 因子计算完成: trade_date=%s index=%d factor=%d upsert=%d missing=%d errors=%d "
            "面板=%s 耗时=%sms",
            trade_date,
            len(indexes),
            len(computers),
            upsert_count,
            missing_count,
            errors,
            panel_metrics,
            elapsed_ms,
        )

        return {
            "index_count": len(indexes),
            "factor_count": len(computers),
            "upsert_count": upsert_count,
            "errors": errors,
            "panel_metrics": panel_metrics,
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
        params_hash = self._default_params_hash(factor_id)
        latest = self._index_repo.find_latest_date(factor_id, params_hash=params_hash)

        if factor_id in _BACKGROUND_ONLY_FACTOR_IDS:
            if force_recompute or latest is None:
                self._enqueue_latest_factor_computation()
            if latest is None:
                raise ValueError("复合因子尚未由后台任务计算，请稍后重试")
        elif latest is None or force_recompute:
            bar_latest = self._index_repo.find_latest_bar_date()
            if bar_latest is None:
                raise ValueError("无任何指数行情数据，无法计算因子")
            self.compute_and_store(bar_latest)
            latest = bar_latest

        rows = self._index_repo.find_cross_section(factor_id, latest, params_hash=params_hash)
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
        if factor_id in _BACKGROUND_ONLY_FACTOR_IDS:
            if force_recompute:
                self._enqueue_latest_factor_computation()
            rows = self._index_repo.find_factor_values(
                factor_id,
                index_code,
                start_date,
                end_date,
                params_hash=self._default_params_hash(factor_id),
            )
            return [_row_to_factor_row(row) for row in rows]

        if force_recompute:
            dates_to_compute = self._index_repo.find_all_bar_dates(index_code, start_date, end_date)
        else:
            dates_to_compute = self._index_repo.find_missing_dates(
                factor_id, index_code, start_date, end_date
            )

        for d in dates_to_compute:
            self.compute_and_store(d)

        rows = self._index_repo.find_factor_values(
            factor_id,
            index_code,
            start_date,
            end_date,
            params_hash=self._default_params_hash(factor_id),
        )
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
            rows = self._index_repo.find_factor_values(
                factor_id,
                index_code,
                start_date,
                end_date,
                params_hash=self._default_params_hash(factor_id),
            )
            return [_row_to_factor_row(r) for r in rows]
        except Exception:
            logger.warning("factor_history 查询失败", exc_info=True)
            return []

    # ==================================================================
    # 内部方法
    # ==================================================================

    def _load_context(
        self,
        trade_date: date,
        index_codes: list[str],
        panel_dates: list[date] | None = None,
        include_composite_panels: bool = True,
    ) -> FactorContext:
        """批量加载回望数据，构建 FactorContext。

        回望窗口由注册表中所有因子的 lookback_days 最大值动态决定。

        Args:
            trade_date: 目标交易日。
            index_codes: 指数代码列表。
            panel_dates: 复合因子的连续交易日计算轴；仅后台计算路径传入。
            include_composite_panels: 是否装配成分股、行业归属等高成本复合因子面板。

        Returns:
            填充了 index_bars / index_valuation / macro_indicators 的 FactorContext。
        """
        lookback_days = _get_max_lookback_days(self._registry)
        lookback_start = trade_date - timedelta(days=lookback_days)

        # 加载指数日线
        index_bar_rows = IndexDailyBarRepository(self._db).find_by_date_range(
            lookback_start, trade_date, index_codes
        )

        # 加载指数估值数据（全回望窗口，供 erp_percentile 等派生因子访问历史分布）
        valuation_rows = (
            IndexValuationRepository(self._db).find_by_date_range(
                lookback_start, trade_date, index_codes
            )
            if index_codes
            else []
        )

        # 加载宏观指标数据（LPR 等），仅保留 period_date <= trade_date 的时点记录，
        # 避免补算历史日期时使用未来才公布的宏观数据（前视偏差）
        macro_rows = MacroIndicatorRepository(self._db).find_by_codes(
            ["lpr1y", "lpr5y", "cpi", "pmi"]
        )
        macro_indicators: dict[str, dict[str, float]] = {}
        for row in macro_rows:
            if row.period_date is not None and row.period_date > trade_date:
                continue
            code = row.indicator_code
            if code not in macro_indicators:
                macro_indicators[code] = {}
            # key 统一使用 period_date（为空时回退到 period 字符串），
            # 与 FactorContext 的 {period_date: value} 契约保持一致
            macro_indicators[code][str(row.period_date or row.period)] = row.value

        ctx = FactorContext(
            index_bars={(r.index_code, r.trade_date): r for r in index_bar_rows},
            index_valuation={(r.index_code, r.trade_date): r for r in valuation_rows},
            macro_indicators=macro_indicators,
        )
        # 复合因子数据面板：注册表出现消费 index_membership / stock_closes /
        # industry_selection 等面板的因子时，由装配服务按当日注入
        panel_factor_ids = {
            spec.factor_id
            for spec in self._registry.specs()
            if spec.required_data
            and any(
                name
                in {
                    "index_membership",
                    "stock_closes",
                    "industry_selection",
                    "index_industry_exposure",
                }
                for name in spec.required_data
            )
        }
        if include_composite_panels and panel_factor_ids and index_codes:
            from quant_etf_api.services.index_factor_panel_service import (
                IndexFactorPanelService,
            )

            ctx.panels = IndexFactorPanelService(self._db).build_panels(
                index_codes=index_codes,
                dates=panel_dates or [trade_date],
                lookback_natural_days=lookback_days,
            )
        return ctx

    def _default_params_hash(self, factor_id: str) -> str:
        """返回注册因子默认参数的指纹，供展示查询排除历史参数口径。

        Args:
            factor_id: 因子标识。

        Returns:
            默认参数指纹；非参数化或未注册因子返回空字符串。
        """
        computer = self._registry.get(factor_id)
        params = dict(computer.spec.default_params or {}) if computer is not None else {}
        return factor_params_hash(params) if params else ""

    def _panel_calculation_dates(self, trade_date: date) -> list[date]:
        """获取复合因子目标日前所需的连续指数交易日计算轴。

        交易日以已落库的指数日线为准，避免请求线程调用外部交易日历；数据不足时
        返回可得日期，扩散计算器会按严格窗口规则产出 NULL。

        Args:
            trade_date: 本次后台任务的目标交易日。

        Returns:
            升序交易日列表，最多包含目标日及此前 333 个交易日。
        """
        dates = IndexDailyBarRepository(self._db).find_all_trading_dates(
            trade_date - timedelta(days=800), trade_date
        )
        return dates[-_PANEL_FACTOR_WINDOW_DAYS:]

    def _enqueue_latest_factor_computation(self) -> None:
        """将最新日全量因子计算入队，避免复合因子在请求线程加载大面板。"""
        latest = self._index_repo.find_latest_bar_date()
        if latest is None:
            return
        from quant_etf_api.infra.job_queue.queue import get_job_queue

        get_job_queue().enqueue(
            "factor_computation",
            {"trade_date": latest.isoformat()},
            job_key=f"factor_computation:{latest.isoformat()}",
        )


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
