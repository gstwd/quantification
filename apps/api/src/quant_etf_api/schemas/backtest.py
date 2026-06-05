from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from quant_etf_api.schemas.types import UtcDatetime


class BacktestCreateRequest(BaseModel):
    """创建回测请求体。

    Attributes:
        strategy_id: 策略插件标识。
        start_date: 回测起始日期。
        end_date: 回测截止日期。
        universe_mode: 标的范围，all=全部活跃 ETF，subset=指定代码列表。
        etf_codes: 指定 ETF 代码列表（universe_mode=subset 时生效）。
        params: 策略参数透传。
        weighting: 信号评分模式的加权方式。
        backtest_mode: 回测模式，signal=信号评分模式（默认），allocation=资产配置模式。
    """

    strategy_id: str
    start_date: date
    end_date: date
    universe_mode: Literal["all", "subset"] = "all"
    etf_codes: list[str] = []
    index_codes: list[str] = []
    params: dict[str, Any] | None = None
    weighting: Literal["equal", "signal_weighted"] = "equal"
    backtest_mode: Literal["signal", "allocation"] = "signal"


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
    backtest_mode: str = "signal"
    metrics: BacktestMetrics | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error_message: str | None = None


class BacktestDetail(BacktestSummary):
    """回测详情，含配置信息。"""

    universe_filter: dict[str, Any]
    params: dict[str, Any] | None = None


class BacktestDailyResult(BaseModel):
    """回测单日组合绩效。

    Attributes:
        trade_date: 交易日。
        portfolio_return: 当日组合收益率（%）。
        cumulative_return: 累计收益率（%）。
        drawdown: 回撤（%）。
        high_signal_count: HIGH 信号数量（信号模式）。
        mid_signal_count: MID 信号数量（信号模式）。
        low_signal_count: LOW 信号数量（信号模式）。
        timing_regime: 择时状态（资产配置模式）。
        total_exposure: 总仓位比例（资产配置模式）。
        cash_ratio: 现金比例（资产配置模式）。
        positions: 持仓明细（资产配置模式），etf_code → 权重。
    """

    trade_date: date
    portfolio_return: float
    cumulative_return: float
    drawdown: float
    high_signal_count: int = 0
    mid_signal_count: int = 0
    low_signal_count: int = 0
    timing_regime: str | None = None
    total_exposure: float | None = None
    cash_ratio: float | None = None
    positions: dict[str, float] | None = None


class BacktestEtfResult(BaseModel):
    """回测单日单 ETF 信号与实际收益（three_factor_guard 专用）。"""

    trade_date: date
    etf_code: str
    signal_score: float
    signal_level: str
    in_portfolio: bool
    etf_return: float | None = None


class BacktestIndexResult(BaseModel):
    """回测单日单指数信号与实际收益。"""

    trade_date: date
    index_code: str
    signal_score: float
    signal_level: str
    in_portfolio: bool
    index_return: float | None = None
