from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.strategies.models import StrategyContextData
from quant_etf_api.plugins.registry import StrategyRegistry, build_default_registry
from quant_etf_api.schemas.strategy import AllocationResponse, StrategyDetail

logger = logging.getLogger(__name__)


class StrategyService:
    """策略服务，提供策略列表、详情和决策管线调用。"""

    def __init__(self, registry: StrategyRegistry | None = None, db: Session | None = None) -> None:
        """初始化策略服务。

        Args:
            registry: 策略注册表，默认使用内置插件。
            db: SQLAlchemy Session，仅 run_allocation 需要。
        """
        self.registry = registry or build_default_registry()
        self._db = db

    def list_strategies(self) -> list[StrategyDetail]:
        """返回所有已注册策略的摘要列表。"""
        return [StrategyDetail(**item) for item in self.registry.as_summaries()]

    def get_strategy(self, strategy_id: str) -> StrategyDetail | None:
        """按 ID 获取策略详情。"""
        plugin = self.registry.get(strategy_id)
        if plugin is None:
            return None
        return StrategyDetail(
            strategy_id=plugin.strategy_id,
            display_name=plugin.display_name,
            version=plugin.version,
            frequency=plugin.frequency,
            asset_scope=plugin.asset_scope,
            description=plugin.description,
            parameter_schema=plugin.parameter_schema(),
            required_inputs=plugin.required_inputs(),
            factors=plugin.factor_definitions(),
            signal_definition=plugin.signal_definition(),
        )

    def run_allocation(
        self,
        strategy_id: str,
        params: dict[str, Any] | None = None,
    ) -> AllocationResponse | None:
        """运行资产配置决策管线。

        调用插件的 assess_market_timing → rank_assets → allocate_positions，
        返回完整的决策结果。

        Args:
            strategy_id: 策略标识。
            params: 策略参数。

        Returns:
            AllocationResponse，插件不支持决策管线时返回 None。
        """
        plugin = self.registry.get(strategy_id)
        if plugin is None or not hasattr(plugin, "assess_market_timing"):
            return None

        if self._db is None:
            logger.warning("run_allocation: 未提供数据库 Session")
            return None

        # 加载数据并构建上下文
        context, universe = self._build_allocation_context()

        # 运行决策管线
        timing = plugin.assess_market_timing(date.today(), context, params)
        rankings = plugin.rank_assets(date.today(), universe, context, params)
        plan = plugin.allocate_positions(timing, rankings, params)

        return AllocationResponse(
            timing=asdict(timing),
            rankings=[asdict(r) for r in (rankings or [])],
            plan=asdict(plan),
        )

    def _build_allocation_context(
        self,
    ) -> tuple[StrategyContextData, list[dict[str, Any]]]:
        """为决策管线构建上下文和 ETF 宇宙。

        优先从 index_factor_value 表读取已计算的因子值（volume_ratio_20d、
        return_5d），仅在因子值缺失时从原始 K 线回退计算。
        """
        from quant_etf_api.domain.common.bar_metrics import calc_5d_return, calc_volume_ratio_20d
        from quant_etf_api.infra.db.models.core import (
            EtfDailyBarModel,
            EtfUniverseModel,
            IndexFactorValueModel,
            IndexValuationModel,
        )

        # 获取活跃 ETF
        etfs = (
            self._db.query(EtfUniverseModel)
            .filter(EtfUniverseModel.is_active.is_(True))
            .all()
        )
        etf_codes = [e.etf_code for e in etfs]
        universe = [
            {"etf_code": e.etf_code, "name_cn": e.name_cn, "category": e.category}
            for e in etfs
        ]

        from datetime import timedelta

        today = date.today()
        lookback = today - timedelta(days=90)

        # 加载最近 90 天行情（回退计算用）
        bars = (
            self._db.query(EtfDailyBarModel)
            .filter(
                EtfDailyBarModel.trade_date >= lookback,
                EtfDailyBarModel.trade_date <= today,
                EtfDailyBarModel.etf_code.in_(etf_codes),
            )
            .all()
        )
        all_bars = {(r.etf_code, r.trade_date): r for r in bars}

        # 优先从 index_factor_value 读取已计算的因子值
        precomputed: dict[str, dict[str, float]] = {}
        for factor_id in ("volume_ratio_20d", "return_5d"):
            rows = (
                self._db.query(
                    IndexFactorValueModel.index_code,
                    IndexFactorValueModel.factor_value_numeric,
                )
                .filter(
                    IndexFactorValueModel.factor_id == factor_id,
                    IndexFactorValueModel.trade_date == today,
                    IndexFactorValueModel.strategy_id.is_(None),
                )
                .all()
            )
            for code, value in rows:
                precomputed.setdefault(code, {})[factor_id] = value

        # 加载指数估值
        val_rows = (
            self._db.query(IndexValuationModel)
            .filter(IndexValuationModel.trade_date >= lookback)
            .all()
        )
        index_valuation: dict[str, dict[str, Any]] = {}
        for r in val_rows:
            index_valuation[r.index_code] = {
                "pe_percentile": r.pe_percentile,
                "pb_percentile": r.pb_percentile,
                "pe": r.pe,
                "pb": r.pb,
            }

        # 构建 ETF-指数映射
        asset_index_map = {e.etf_code: e.tracking_index_code for e in etfs if e.tracking_index_code}

        # 构建 asset_bars 上下文（优先用因子值，缺失时回退计算）
        asset_bars: dict[str, dict[str, Any]] = {}
        for code in etf_codes:
            bar = all_bars.get((code, today))
            if bar and bar.close_price:
                factors = precomputed.get(code, {})
                asset_bars[code] = {
                    "close_price": bar.close_price,
                    "volume_ratio_20d": factors.get(
                        "volume_ratio_20d", calc_volume_ratio_20d(code, today, all_bars)
                    ),
                    "return_5d": factors.get(
                        "return_5d", calc_5d_return(code, today, all_bars)
                    ),
                    "change_pct": bar.change_pct,
                }

        context = StrategyContextData(
            extra={
                "asset_bars": asset_bars,
                "index_valuation": index_valuation,
                "asset_index_map": asset_index_map,
            }
        )
        return context, universe
