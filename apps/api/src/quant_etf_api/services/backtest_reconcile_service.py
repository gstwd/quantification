"""回测独立对账服务：读取落库回测与原始行情，交给领域层独立复算后比对。

只读：不写库、不入队、不修改回测记录。复算规则见
:mod:`quant_etf_api.domain.portfolio.reconcile`（刻意不复用回测执行器的代码）。

差异有两类来源：回测主链路的实现缺陷，或回测完成之后行情被上游修订（对账读的是
当前库里的行情）。出现差异时，可以用同一配置快照重跑回测来区分：重跑后对账一致，
说明是行情修订；仍不一致，说明是实现缺陷。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from quant_etf_api.domain.portfolio.reconcile import (
    reconcile_daily,
    reconcile_metrics,
)
from quant_etf_api.infra.db.repositories.backtest import BacktestRepository
from quant_etf_api.infra.db.repositories.index_daily_bar import IndexDailyBarRepository

logger = logging.getLogger(__name__)

# 只支持当前记账口径；存量 constant_mix_v0 回测的口径不同，不能按 drift_v1 规格核对
_SUPPORTED_ACCOUNTING = "drift_v1"
# 回望窗口：为首个交易日就缺行情的资产找前值（前向填充的种子）
_SEED_LOOKBACK_DAYS = 120
# 回测 params 缺执行模型时的系统级解释（见 AGENTS.md「研究与优化统一按 t_plus_1_close」）
_LEGACY_EXECUTION_MODEL = "t_plus_1_open"
# 2026-11-05 修复前的回测把零权重持仓算作持仓日，只会在这两个指标上与逐日结果不符
_ACTIVE_DAY_FIELDS = frozenset({"metrics.active_days", "metrics.win_rate_pct"})


class BacktestReconcileService:
    """回测独立对账服务。"""

    def __init__(
        self,
        db: Session,
        backtest_repo: BacktestRepository | None = None,
        index_bar_repo: IndexDailyBarRepository | None = None,
    ) -> None:
        """初始化服务。

        Args:
            db: SQLAlchemy 同步 Session。
            backtest_repo: 回测仓库，未提供时自动创建。
            index_bar_repo: 指数日线仓库，未提供时自动创建。
        """
        self._backtest_repo = backtest_repo or BacktestRepository(db)
        self._index_bar_repo = index_bar_repo or IndexDailyBarRepository(db)

    def verify(self, backtest_id: str) -> dict[str, Any]:
        """对一次落库回测做独立对账。

        Args:
            backtest_id: 回测 ID。

        Returns:
            对账报告字典（``ok``、差异明细、各字段最大差、未核对项等）。

        Raises:
            LookupError: 回测不存在。
            ValueError: 回测未成功完成、是组合策略回测、记账口径不受支持或没有逐日结果。
        """
        row = self._backtest_repo.find_by_id(backtest_id)
        if row is None:
            raise LookupError(f"回测 {backtest_id} 不存在")
        if row.status != "success":
            raise ValueError(f"只能对账已成功完成的回测（{backtest_id} 状态为 {row.status}）")
        params: dict[str, Any] = dict(row.params or {})
        if params.get("_composite_model"):
            raise ValueError("组合策略回测按成员净值合成，不在逐日持仓对账的范围内")
        accounting = params.get("_accounting_model") or "constant_mix_v0"
        if accounting != _SUPPORTED_ACCOUNTING:
            raise ValueError(
                f"只支持 {_SUPPORTED_ACCOUNTING} 记账口径的回测（{backtest_id} 为 {accounting}）"
            )
        rows = self._backtest_repo.find_daily_results(backtest_id)
        if not rows:
            raise ValueError(f"回测 {backtest_id} 没有逐日结果")

        execution_model = str(params.get("_execution_model") or _LEGACY_EXECUTION_MODEL)
        benchmark_mix = _benchmark_mix(params)
        held = {code for r in rows for code in (r.executed_positions or {})}
        codes = sorted(held | {code for code, _w in benchmark_mix} | _universe_codes(row))

        last_day = rows[-1].trade_date
        excluded = {date.fromisoformat(d) for d in params.get("_non_trading_bar_dates") or []}
        later = [
            day
            for day in self._index_bar_repo.find_trading_dates(
                last_day + timedelta(days=1), row.end_date, codes
            )
            if day not in excluded
        ]
        skipped: list[str] = []
        daily_rows = list(rows)
        if later:
            trading_dates = [r.trade_date for r in rows] + [later[0]]
        else:
            # 找不到最后一行的收益窗口终点（行情已被删除等）：最后一行只核对汇总指标
            daily_rows = daily_rows[:-1]
            trading_dates = [r.trade_date for r in rows]
            skipped.append(f"最后一行 {last_day}：库中找不到下一交易日的行情，未核对逐日收益")

        bars = self._index_bar_repo.find_all_date_range(
            rows[0].trade_date - timedelta(days=_SEED_LOOKBACK_DAYS),
            trading_dates[-1],
            index_codes=codes,
        )
        report = reconcile_daily(
            daily_rows,
            trading_dates,
            bars,
            execution_model=execution_model,
            benchmark_mix=benchmark_mix,
        )
        reconcile_metrics(
            rows,
            dict(row.metrics or {}),
            report,
            annual_risk_free_rate_pct=float(params.get("_annual_risk_free_rate_pct") or 0.0),
        )
        report.skipped.extend(skipped)
        payload = {
            "backtest_id": backtest_id,
            "strategy_id": row.strategy_id,
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(),
            "benchmark_mix": [[code, weight] for code, weight in benchmark_mix],
            **report.to_dict(),
        }
        issue_fields = {issue.field for issue in report.issues}
        if not report.ok and issue_fields <= _ACTIVE_DAY_FIELDS:
            payload["hint"] = (
                "逐日记账一致，只有持仓日统计不符：这是 2026-11-05 已修复的零权重持仓缺陷"
                "（空仓日被计入持仓日，见决策记录），落库的 active_days / win_rate_pct 偏差，"
                "重跑该回测即可修正"
            )
        elif not report.ok:
            payload["hint"] = (
                "差异可能来自回测主链路的实现缺陷，也可能来自回测之后行情被上游修订；"
                "用同一配置快照重跑回测后再对账：一致则为行情修订，仍不一致则为实现缺陷"
            )
        logger.info(
            "[reconcile] backtest_id=%s ok=%s 核对=%s 差异=%s",
            backtest_id,
            report.ok,
            report.checked_days,
            report.issue_count,
        )
        return payload


def _benchmark_mix(params: dict[str, Any]) -> list[tuple[str, float]]:
    """回测使用的基准构成；未启用基准时为空（不核对基准）。

    Args:
        params: 回测 params。

    Returns:
        ``[(指数代码, 权重)]``。
    """
    if not params.get("_enable_benchmark", True):
        return []
    mix = [
        (str(item["index_code"]), float(item["weight"]))
        for item in params.get("_benchmark_mix") or []
    ]
    return mix or [(str(params.get("_benchmark_index_code") or "000300"), 1.0)]


def _universe_codes(row: Any) -> set[str]:
    """回测标的范围里显式列出的指数代码（用于定位最后一个交易日）。"""
    universe = row.universe_filter or {}
    return {str(code) for code in universe.get("index_codes") or []}
