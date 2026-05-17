from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from quant_etf_api.schemas.types import UtcDatetime


class BacktestCreateRequest(BaseModel):
    """创建回测请求体。"""

    strategy_id: str
    start_date: date
    end_date: date
    universe_mode: Literal["all", "subset"] = "all"
    etf_codes: list[str] = []
    params: dict | None = None
    weighting: Literal["equal", "signal_weighted"] = "equal"


class BacktestMetrics(BaseModel):
    """回测汇总绩效指标。"""

    cumulative_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    signal_accuracy_pct: float
    total_trading_days: int
    active_days: int


class BacktestSummary(BaseModel):
    """回测列表摘要，不含明细数据。"""

    backtest_id: str
    strategy_id: str
    start_date: date
    end_date: date
    status: str
    weighting: str
    metrics: BacktestMetrics | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error_message: str | None = None


class BacktestDetail(BacktestSummary):
    """回测详情，含配置信息。"""

    universe_filter: dict
    params: dict | None = None


class BacktestDailyResult(BaseModel):
    """回测单日组合绩效。"""

    trade_date: date
    portfolio_return: float
    cumulative_return: float
    drawdown: float
    high_signal_count: int
    mid_signal_count: int
    low_signal_count: int


class BacktestEtfResult(BaseModel):
    """回测单日单 ETF 信号与实际收益。"""

    trade_date: date
    etf_code: str
    signal_score: float
    signal_level: str
    in_portfolio: bool
    etf_return: float | None = None
