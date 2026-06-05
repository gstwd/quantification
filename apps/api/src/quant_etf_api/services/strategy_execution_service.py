"""策略实时执行服务，为单日策略运行构建 DB 上下文并调用插件。

使用模式与 BacktestService 一致：从 DB 加载行情数据，
构建 StrategyContextData，调用插件 run_for_universe，
将信号和因子值写入 etf_signal 和 etf_factor_value 表。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.infra.db.models.core import (
    EtfFactorValueModel,
    EtfSignalModel,
)
from quant_etf_api.infra.db.repositories.etf_daily_bar import EtfDailyBarRepository
from quant_etf_api.infra.db.repositories.etf_universe import EtfUniverseRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository
from quant_etf_api.infra.db.repositories.research_run import ResearchRunRepository
from quant_etf_api.plugins.base import StrategyContextData, StrategyPlugin
from quant_etf_api.domain.common.bar_metrics import (
    calc_5d_return,
    calc_volume_ratio_20d,
)

logger = logging.getLogger(__name__)


class StrategyExecutionService:
    """策略实时执行服务。

    为指定策略的单日运行构建数据库上下文，
    调用策略插件计算信号/因子值，并将结果持久化。
    """

    def __init__(
        self,
        db: Session,
        bar_repo: EtfDailyBarRepository | None = None,
        index_bar_repo: IndexDailyBarRepository | None = None,
        universe_repo: EtfUniverseRepository | None = None,
        run_repo: ResearchRunRepository | None = None,
    ) -> None:
        self._db = db
        self._bar_repo = bar_repo or EtfDailyBarRepository(db)
        self._index_bar_repo = index_bar_repo or IndexDailyBarRepository(db)
        self._universe_repo = universe_repo or EtfUniverseRepository(db)
        self._run_repo = run_repo or ResearchRunRepository(db)

    def execute(
        self,
        plugin: StrategyPlugin,
        trade_date: date,
        run_id: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """执行单日策略信号计算，写入 etf_signal 和 etf_factor_value。

        Args:
            plugin: 已注册的策略插件实例
            trade_date: 运行对应的交易日
            run_id: 研究运行 ID
            params: 策略参数覆盖
        """
        etfs = self._universe_repo.find_all_active()
        if not etfs:
            logger.warning("execute: 无活跃 ETF，跳过策略运行")
            return

        etf_codes = [e.etf_code for e in etfs]
        universe = [{"etf_code": e.etf_code, "name_cn": e.name_cn} for e in etfs]

        # 批量加载所需数据
        all_bars = self._load_all_bars(trade_date, etf_codes)
        all_index_bars = self._load_all_index_bars(trade_date)

        # 构建策略上下文
        context = self._build_live_context(
            trade_date, etf_codes, all_bars, all_index_bars
        )

        # 调用插件计算信号
        try:
            results = plugin.run_for_universe(trade_date, universe, context, params)
        except Exception:
            logger.exception("插件 %s 执行失败", plugin.strategy_id)
            self._mark_run_failed(run_id, f"插件 {plugin.strategy_id} 执行异常")
            return

        # 写入信号和因子值
        signal_count = 0
        factor_count = 0
        for r in results:
            try:
                self._db.add(
                    EtfSignalModel(
                        trade_date=r.trade_date,
                        etf_code=r.etf_code,
                        strategy_id=r.strategy_id,
                        signal_score=r.signal_score,
                        signal_level=r.signal_level,
                        signal_label=r.signal_label,
                        signal_payload=r.payload,
                        run_id=run_id,
                    )
                )
                signal_count += 1
            except Exception:
                self._db.rollback()
                logger.warning("写入信号失败: %s %s", r.etf_code, r.strategy_id)

            for fv in r.factor_values:
                try:
                    self._db.add(
                        EtfFactorValueModel(
                            trade_date=r.trade_date,
                            etf_code=r.etf_code,
                            factor_id=fv["factor_id"],
                            factor_value_numeric=fv.get("value"),
                            factor_value_text=fv.get("text"),
                            factor_payload=fv.get("payload"),
                            strategy_id=r.strategy_id,
                        )
                    )
                    factor_count += 1
                except Exception:
                    self._db.rollback()
                    logger.warning("写入因子值失败: %s %s %s", r.etf_code, fv["factor_id"])

        self._db.commit()

        # 更新运行状态
        self._run_repo.mark_success(
            run_id,
            metrics={
                "etf_count": len(etfs),
                "signal_count": signal_count,
                "factor_count": factor_count,
            },
        )
        logger.info(
            "策略执行完成: %s signals=%d factors=%d", plugin.strategy_id, signal_count, factor_count
        )

    def _mark_run_failed(self, run_id: str, message: str) -> None:
        try:
            self._run_repo.mark_failed(run_id, message)
        except Exception:
            logger.warning("更新失败状态时出错", exc_info=True)

    # ── 数据加载 ──────────────────────────────────────────────────────────────

    def _load_all_bars(self, trade_date: date, etf_codes: list[str]) -> dict[tuple[str, date], Any]:
        """批量加载交易日及前 25 日的 ETF 日线。"""
        lookback_start = trade_date - timedelta(days=35)
        return self._bar_repo.find_by_codes_date_range(etf_codes, lookback_start, trade_date)

    def _load_all_index_bars(self, trade_date: date) -> dict[tuple[str, date], Any]:
        """批量加载交易日及前 10 日的指数日线。"""
        lookback_start = trade_date - timedelta(days=15)
        return self._index_bar_repo.find_all_date_range(lookback_start, trade_date)

    # ── 上下文构建 ────────────────────────────────────────────────────────────

    def _build_live_context(
        self,
        trade_date: date,
        etf_codes: list[str],
        all_bars: dict[tuple[str, date], Any],
        all_index_bars: dict[tuple[str, date], Any],
    ) -> StrategyContextData:
        """从 DB 数据构建单日策略上下文。"""
        # 基准指数涨跌幅和 5 日收益
        benchmark_changes: dict[str, float] = {}
        index_5d_return: dict[str, float] = {}
        for index_code in ("000300", "000016", "000905"):
            bar = all_index_bars.get((index_code, trade_date))
            if bar is not None and bar.change_pct is not None:
                benchmark_changes[index_code] = bar.change_pct
            index_5d_return[index_code] = calc_5d_return(
                index_code, trade_date, all_index_bars
            )

        # 资产 K 线衍生指标
        asset_bars: dict[str, dict[str, Any]] = {}
        for code in etf_codes:
            bar = all_bars.get((code, trade_date))
            if bar is None:
                continue
            volume_ratio_20d = calc_volume_ratio_20d(code, trade_date, all_bars)
            etf_5d_return = calc_5d_return(code, trade_date, all_bars)
            asset_bars[code] = {
                "volume_ratio_20d": volume_ratio_20d,
                "change_pct": bar.change_pct or 0.0,
                "etf_5d_return": etf_5d_return,
                "close_price": bar.close_price,
            }

        return StrategyContextData(
            benchmark_changes=benchmark_changes,
            extra={"asset_bars": asset_bars, "index_5d_return": index_5d_return},
        )
