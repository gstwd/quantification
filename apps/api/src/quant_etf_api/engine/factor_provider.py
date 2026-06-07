"""因子供应器：桥接因子计算层与策略引擎层。

职责：
1. 从 StrategyConfig 中提取所有需要的因子 ID 列表。
2. 实时模式：从 index_factor_value 表加载预计算因子值。
3. 回测模式：利用预加载的 K 线数据，通过 FactorComputer 批量计算所有因子。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, TYPE_CHECKING

from sqlalchemy import and_
from sqlalchemy.orm import Session

from quant_etf_api.factors.base import FactorContext

if TYPE_CHECKING:
    from quant_etf_api.engine.config import StrategyConfig
    from quant_etf_api.factors.registry import FactorRegistry

logger = logging.getLogger(__name__)


class FactorProvider:
    """因子供应器：从预计算因子表或实时计算中加载引擎所需的因子值。

    Args:
        db: SQLAlchemy 同步 Session（实时模式必需，回测模式可选）。
        registry: 因子注册表（回测预计算模式必需）。
    """

    def __init__(
        self,
        db: Session | None = None,
        registry: "FactorRegistry | None" = None,
    ) -> None:
        """初始化因子供应器。

        Args:
            db: 数据库会话，实时模式用于查询预计算因子值。
            registry: 因子注册表，回测模式用于调用 FactorComputer。
        """
        self._db = db
        self._registry = registry

    @staticmethod
    def collect_required_factor_ids(config: "StrategyConfig") -> list[str]:
        """从策略配置中收集所有需要的因子 ID。

        遍历 timing.factors、score.factors、filters.rules，
        去重后返回完整的因子 ID 列表。

        Args:
            config: 策略配置。

        Returns:
            去重后的因子 ID 列表。
        """
        factor_ids: set[str] = set()

        # 评分因子
        factor_ids.update(config.score.factors.keys())

        # 择时因子
        if config.timing:
            factor_ids.update(config.timing.factors.keys())

        # 过滤规则引用的因子（含跨因子比较的 compare_to）
        if config.filters:
            for rule in config.filters.rules:
                factor_ids.add(rule.factor)
                if rule.compare_to:
                    factor_ids.add(rule.compare_to)

        return sorted(factor_ids)

    def load_asset_factors(
        self,
        config: "StrategyConfig",
        trade_date: date,
        index_codes: list[str],
    ) -> dict[tuple[str, str], float | None]:
        """从数据库加载预计算的资产因子值（实时模式）。

        查询 index_factor_value 表，返回每资产 × 因子的平铺字典。

        Args:
            config: 策略配置，用于推导需要的因子 ID 列表。
            trade_date: 交易日。
            index_codes: 指数代码列表。

        Returns:
            key=(index_code, factor_id), value=因子数值 的字典。
        """
        if self._db is None:
            logger.warning("FactorProvider 未注入 db，无法加载预计算因子值")
            return {}

        factor_ids = self.collect_required_factor_ids(config)
        if not factor_ids:
            return {}

        return self._query_factor_values(factor_ids, trade_date, index_codes)

    def load_market_factors(
        self,
        config: "StrategyConfig",
        trade_date: date,
    ) -> dict[str, float | None]:
        """加载市场级因子值（用于择时）。

        根据 config.timing.factors 中配置的因子 ID，
        从代表性指数（默认沪深300）加载因子值作为市场代理。

        Args:
            config: 策略配置，需包含 timing 配置。
            trade_date: 交易日。

        Returns:
            key=factor_id, value=因子数值 的字典。
        """
        if self._db is None or config.timing is None:
            return {}

        factor_ids = list(config.timing.factors.keys())
        if not factor_ids:
            return {}

        # 使用代表性指数作为市场代理
        rep_code = "000300"
        result: dict[str, float | None] = {}
        for factor_id in factor_ids:
            values = self._query_factor_values([factor_id], trade_date, [rep_code])
            result[factor_id] = values.get((rep_code, factor_id))

        return result

    def precompute_backtest_factors(
        self,
        config: "StrategyConfig",
        dates: list[date],
        index_codes: list[str],
        all_bars: dict[tuple[str, date], Any],
        all_valuation: dict[tuple[str, date], Any],
    ) -> dict[date, dict[tuple[str, str], float | None]]:
        """回测模式：一次性计算所有因子值，避免逐日查库。

        利用预加载的 K 线和估值数据构建 FactorContext，
        对每个交易日 × 指数调用 FactorComputer 批量计算。

        Args:
            config: 策略配置。
            dates: 回测交易日列表。
            index_codes: 回测指数代码列表。
            all_bars: 预加载的指数日线数据，key=(index_code, trade_date)。
            all_valuation: 预加载的估值数据，key=(index_code, trade_date)。

        Returns:
            三维映射：date → (index_code, factor_id) → factor_value。
        """
        if self._registry is None:
            logger.warning("FactorProvider 未注入 registry，回测因子预计算不可用")
            return {}

        factor_ids = self.collect_required_factor_ids(config)
        if not factor_ids:
            return {}

        computers = [c for c in self._registry.all() if c.spec.factor_id in factor_ids]
        if not computers:
            logger.warning("回测因子预计算：无匹配的因子计算器，factor_ids=%s", factor_ids)
            return {}

        result: dict[date, dict[tuple[str, str], float | None]] = {}

        for trade_date in dates:
            ctx = FactorContext(
                index_bars=all_bars,
                index_valuation={
                    (code, dt): val
                    for (code, dt), val in all_valuation.items()
                    if dt == trade_date
                },
            )
            day_factors: dict[tuple[str, str], float | None] = {}
            for code in index_codes:
                for computer in computers:
                    try:
                        fv = computer.compute(code, trade_date, ctx)
                        day_factors[(code, fv.factor_id)] = fv.numeric
                    except Exception:
                        logger.warning(
                            "回测因子计算失败: date=%s code=%s factor=%s",
                            trade_date,
                            code,
                            computer.spec.factor_id,
                            exc_info=True,
                        )
                        day_factors[(code, computer.spec.factor_id)] = None
            result[trade_date] = day_factors

        logger.info(
            "回测因子预计算完成: dates=%d index=%d factors=%d",
            len(dates),
            len(index_codes),
            len(computers),
        )
        return result

    # ==================================================================
    # 内部方法
    # ==================================================================

    def _query_factor_values(
        self,
        factor_ids: list[str],
        trade_date: date,
        index_codes: list[str],
    ) -> dict[tuple[str, str], float | None]:
        """查询 index_factor_value 表，返回平铺的因子值字典。

        Args:
            factor_ids: 需要查询的因子 ID 列表。
            trade_date: 交易日。
            index_codes: 指数代码列表。

        Returns:
            key=(index_code, factor_id), value=因子数值 的字典。
        """
        from quant_etf_api.infra.db.models.core import IndexFactorValueModel

        rows = (
            self._db.query(
                IndexFactorValueModel.index_code,
                IndexFactorValueModel.factor_id,
                IndexFactorValueModel.factor_value_numeric,
            )
            .filter(
                and_(
                    IndexFactorValueModel.factor_id.in_(factor_ids),
                    IndexFactorValueModel.trade_date == trade_date,
                    IndexFactorValueModel.index_code.in_(index_codes),
                    IndexFactorValueModel.strategy_id.is_(None),
                )
            )
            .all()
        )
        return {(r.index_code, r.factor_id): r.factor_value_numeric for r in rows}
