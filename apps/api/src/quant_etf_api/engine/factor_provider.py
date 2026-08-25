"""因子供应器：桥接因子计算层与策略引擎层。

职责：
1. 从 StrategyConfig 中提取所有需要的因子 ID 列表。
2. 实时模式：从 index_factor_value 表加载预计算因子值。
3. 回测模式：利用预加载的 K 线数据，通过 FactorComputer 批量计算所有因子。
4. 三态缺失语义：load_asset_factor_records 保留 FactorValue 的
   missing_reason（因子未计算/数据不足/因子不存在），引擎层仍使用
   扁平浮点字典以保持性能，服务层可基于记录做精确的补算决策。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, TYPE_CHECKING

from sqlalchemy import and_
from sqlalchemy.orm import Session

from quant_etf_api.factors.base import (
    BatchFactorComputer,
    FactorContext,
    FactorValue,
    MissingReason,
)

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
        排名模块的动量/估值子因子（rank.momentum_factor / rank.valuation_factor），
        以及 regime_rules 中所有 regime 的 score/filters 配置，
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

        # 排名模块引用的子因子（动量/估值子排名直接读取 asset_factors）
        factor_ids.add(config.rank.momentum_factor)
        factor_ids.add(config.rank.valuation_factor)

        # regime 条件化配置中引用的因子
        for regime_rule in config.regime_rules.values():
            if regime_rule.score:
                factor_ids.update(regime_rule.score.factors.keys())
            if regime_rule.filters:
                for rule in regime_rule.filters.rules:
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

    def load_asset_factor_records(
        self,
        config: "StrategyConfig",
        trade_date: date,
        index_codes: list[str],
    ) -> dict[tuple[str, str], FactorValue]:
        """加载资产因子记录（保留缺失语义），供服务层诊断与补算决策。

        与 load_asset_factors 的区别：返回值保留 FactorValue 的
        missing_reason / payload / text，可区分"因子未计算 / 数据不足 / 因子不存在"。
        引擎执行路径仍使用 load_asset_factors 的扁平字典以保持性能。

        Args:
            config: 策略配置，用于推导需要的因子 ID 列表。
            trade_date: 交易日。
            index_codes: 指数代码列表。

        Returns:
            key=(index_code, factor_id), value=带缺失语义的 FactorValue。
        """
        if self._db is None:
            return {}

        factor_ids = self.collect_required_factor_ids(config)
        if not factor_ids:
            return {}

        rows = self._query_factor_rows(factor_ids, trade_date, index_codes)
        records: dict[tuple[str, str], FactorValue] = {}
        for r in rows:
            records[(r.index_code, r.factor_id)] = FactorValue(
                factor_id=r.factor_id,
                numeric=r.factor_value_numeric,
                text=r.factor_value_text,
                payload=r.factor_payload or {},
                missing_reason=(
                    MissingReason.INSUFFICIENT_DATA if r.factor_value_numeric is None else None
                ),
            )
        return records

    def classify_missing(
        self,
        factor_id: str,
        index_codes: list[str],
        records: dict[tuple[str, str], FactorValue],
    ) -> MissingReason | None:
        """按三态语义分类单个因子的缺失原因。

        Args:
            factor_id: 因子标识。
            index_codes: 指数代码列表。
            records: load_asset_factor_records 的返回值。

        Returns:
            缺失原因；因子对全部指数均可用时返回 None。
        """
        if self._registry is not None and self._registry.get(factor_id) is None:
            return MissingReason.FACTOR_UNKNOWN

        present = [p for p in ((c, factor_id) for c in index_codes) if p in records]
        if not present:
            return MissingReason.NOT_COMPUTED
        if any(records[p].numeric is None for p in present):
            return MissingReason.INSUFFICIENT_DATA
        return None

    def load_market_factors(
        self,
        config: "StrategyConfig",
        trade_date: date,
    ) -> dict[str, float | None]:
        """加载市场级因子值（用于择时）。

        根据 config.timing.factors 中配置的因子 ID，
        从择时代理指数（config.timing.proxy_index_codes）加载因子值。

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

        proxy_codes = config.timing.proxy_index_codes
        result: dict[str, float | None] = {}
        for factor_id in factor_ids:
            for rep_code in proxy_codes:
                values = self._query_factor_values([factor_id], trade_date, [rep_code])
                val = values.get((rep_code, factor_id))
                if val is not None:
                    result[factor_id] = val
                    break
            else:
                result[factor_id] = None

        return result

    def precompute_backtest_factors(
        self,
        config: "StrategyConfig",
        dates: list[date],
        index_codes: list[str],
        all_bars: dict[tuple[str, date], Any],
        all_valuation: dict[tuple[str, date], Any],
        all_macro: dict[str, dict[str, float]] | None = None,
        all_sentiment: dict[tuple[str, date], Any] | None = None,
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
            all_macro: 预加载的宏观指标数据，key=indicator_code, value={period: value}。
            all_sentiment: 预加载的 AI 情绪聚合数据，key=(asset_tag, date)。

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

        # 将 computers 分为批量和逐点两组
        batch_computers = [c for c in computers if isinstance(c, BatchFactorComputer)]
        point_computers = [c for c in computers if not isinstance(c, BatchFactorComputer)]

        # 初始化结果字典（按日期）
        result: dict[date, dict[tuple[str, str], float | None]] = {d: {} for d in dates}

        # 批量因子：每个 (指数, 因子) 组合只构建一次收盘价序列，计算所有日期
        # 使用全量 bar 数据的上下文（无需按日期切片，批量接口自行处理历史范围）
        if batch_computers:
            batch_ctx = FactorContext(
                index_bars=all_bars,
                index_valuation=all_valuation or {},
                macro_indicators=all_macro or {},
                ai_sentiment=all_sentiment or {},
            )
            for code in index_codes:
                for computer in batch_computers:
                    try:
                        batch_results = computer.compute_batch(code, dates, batch_ctx)
                        for trade_date, fv in batch_results.items():
                            result[trade_date][(code, fv.factor_id)] = fv.numeric
                    except Exception:
                        logger.warning(
                            "回测批量因子计算失败: code=%s factor=%s",
                            code,
                            computer.spec.factor_id,
                            exc_info=True,
                        )
                        for trade_date in dates:
                            result[trade_date][(code, computer.spec.factor_id)] = None

        # 逐点因子（不支持批量接口的计算器）：保留原有逐日循环
        if point_computers:
            for trade_date in dates:
                ctx = FactorContext(
                    index_bars=all_bars,
                    index_valuation={
                        (code, dt): val
                        for (code, dt), val in (all_valuation or {}).items()
                        if dt <= trade_date
                    },
                    macro_indicators=all_macro or {},
                    ai_sentiment={
                        (tag, dt): val
                        for (tag, dt), val in (all_sentiment or {}).items()
                        if dt <= trade_date
                    },
                )
                for code in index_codes:
                    for computer in point_computers:
                        try:
                            fv = computer.compute(code, trade_date, ctx)
                            result[trade_date][(code, fv.factor_id)] = fv.numeric
                        except Exception:
                            logger.warning(
                                "回测因子计算失败: date=%s code=%s factor=%s",
                                trade_date,
                                code,
                                computer.spec.factor_id,
                                exc_info=True,
                            )
                            result[trade_date][(code, computer.spec.factor_id)] = None

        logger.info(
            "回测因子预计算完成: dates=%d index=%d batch_factors=%d point_factors=%d",
            len(dates),
            len(index_codes),
            len(batch_computers),
            len(point_computers),
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

    def _query_factor_rows(
        self,
        factor_ids: list[str],
        trade_date: date,
        index_codes: list[str],
    ) -> list[Any]:
        """查询 index_factor_value 原始行（仅独立因子值，strategy_id IS NULL）。

        Args:
            factor_ids: 因子 ID 列表。
            trade_date: 交易日。
            index_codes: 指数代码列表。

        Returns:
            IndexFactorValueModel 行列表。
        """
        from quant_etf_api.infra.db.models.core import IndexFactorValueModel

        return (
            self._db.query(IndexFactorValueModel)
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
