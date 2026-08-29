from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from quant_etf_api.schemas.types import UtcDatetime


class BacktestWarning(BaseModel):
    """回测/运行过程中需要透传给前端的结构化提示。

    Attributes:
        level: 级别，info=信息, warning=警告, error=错误。
        code: 稳定错误码（如 WARMUP / MISSING_FACTOR / DATA_GAP / PARTIAL_RESULT）。
        message: 人类可读的中文说明。
        trade_date: 关联的交易日，全局性警告为 None。
        index_code: 关联的指数代码，全局性警告为 None。
    """

    level: Literal["info", "warning", "error"]
    code: str
    message: str
    trade_date: date | None = None
    index_code: str | None = None


class BacktestCreateRequest(BaseModel):
    """创建回测请求体。

    Attributes:
        strategy_id: 策略唯一标识。
        start_date: 回测起始日期。
        end_date: 回测截止日期。
        universe_mode: 标的范围，all=全部指数，subset=指定指数代码列表。
        index_codes: 指定指数代码列表（universe_mode=subset 时生效）。
        params: 策略参数透传。
        enable_benchmark: 是否启用基准对比。
        benchmark_index_code: 基准指数代码，默认沪深300。
    """

    strategy_id: str
    start_date: date
    end_date: date
    universe_mode: Literal["all", "subset"] = "all"
    index_codes: list[str] = []
    params: dict[str, Any] | None = None
    enable_benchmark: bool = True
    benchmark_index_code: str = "000300"


class BacktestMetrics(BaseModel):
    """回测汇总绩效指标。"""

    cumulative_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    signal_accuracy_pct: float
    total_trading_days: int
    active_days: int
    # 专业指标（Phase 3 新增）
    annualized_return_pct: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    max_drawdown_days: int = 0
    profit_loss_ratio: float | None = None
    alpha: float | None = None
    beta: float | None = None
    information_ratio: float | None = None
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None
    # B10：数据缺口统计
    data_gap_days: int = 0


class BacktestSummary(BaseModel):
    """回测列表摘要，不含明细数据。"""

    backtest_id: str
    strategy_id: str
    start_date: date
    end_date: date
    status: str
    metrics: BacktestMetrics | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error_message: str | None = None
    progress: int = 0


class BacktestDetail(BacktestSummary):
    """回测详情，含配置信息。"""

    universe_filter: dict[str, Any]
    params: dict[str, Any] | None = None
    warnings: list[BacktestWarning] = Field(default_factory=list)
    config_snapshot: dict[str, Any] | None = None
    config_hash: str | None = None
    data_cutoff_date: date | None = None
    optimization_id: str | None = None


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
        positions: 持仓明细（资产配置模式），index_code → 权重。
        benchmark_return: 基准日收益率（%）。
        turnover: 当日换手率。
        missing_bar_count: 当日受数据缺口影响的持仓资产数（B10）。
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
    benchmark_return: float | None = None
    turnover: float | None = None
    missing_bar_count: int = 0


class BacktestIndexResult(BaseModel):
    """回测单日单指数信号与实际收益。

    口径与实时信号一致：signal_score 为综合得分，target_weight 为信号目标仓位权重。
    """

    trade_date: date
    index_code: str
    signal_score: float
    signal_level: str
    in_portfolio: bool
    index_return: float | None = None
    target_weight: float | None = None


# ── 策略对比回测 schemas ──────────────────────────────────────────────


class BacktestComparisonCreateRequest(BaseModel):
    """创建策略对比回测请求体。

    Attributes:
        strategy_a_id: 策略 A 唯一标识。
        strategy_b_id: 策略 B 唯一标识。
        start_date: 回测起始日期（两个策略共享）。
        end_date: 回测截止日期（两个策略共享）。
        a_index_codes: 策略 A 的指数代码列表（空列表=全部指数）。
            若策略 A 已配置标的范围则忽略此字段。
        b_index_codes: 策略 B 的指数代码列表（空列表=全部指数）。
            若策略 B 已配置标的范围则忽略此字段。
        enable_benchmark: 是否启用基准对比。
        benchmark_index_code: 基准指数代码，默认沪深300。
        name: 可选的对比名称。
    """

    strategy_a_id: str
    strategy_b_id: str
    start_date: date
    end_date: date
    a_index_codes: list[str] = []
    b_index_codes: list[str] = []
    enable_benchmark: bool = True
    benchmark_index_code: str = "000300"
    name: str | None = None


class ComparisonMetrics(BaseModel):
    """双策略对比维度的指标。

    包含策略 A/B 各自的核心指标和差值（A - B）。
    正差值表示 A 优于 B（回撤类指标中，负差值表示 A 回撤更小=更优）。
    """

    # ── 策略 A 指标 ──
    a_cumulative_return_pct: float
    b_cumulative_return_pct: float
    a_annualized_return_pct: float
    b_annualized_return_pct: float
    a_max_drawdown_pct: float
    b_max_drawdown_pct: float
    a_sharpe_ratio: float
    b_sharpe_ratio: float
    a_sortino_ratio: float
    b_sortino_ratio: float
    a_calmar_ratio: float
    b_calmar_ratio: float
    a_win_rate_pct: float
    b_win_rate_pct: float
    a_signal_accuracy_pct: float
    b_signal_accuracy_pct: float
    a_total_trading_days: int
    b_total_trading_days: int
    a_active_days: int
    b_active_days: int

    # ── 差值（A - B） ──
    cumulative_return_diff_pct: float
    annualized_return_diff_pct: float
    max_drawdown_diff_pct: float
    sharpe_diff: float
    sortino_diff: float
    calmar_diff: float
    win_rate_diff_pct: float
    signal_accuracy_diff_pct: float

    # ── 基准对比（若启用） ──
    a_benchmark_return_pct: float | None = None
    b_benchmark_return_pct: float | None = None
    a_excess_return_pct: float | None = None
    b_excess_return_pct: float | None = None
    a_alpha: float | None = None
    b_alpha: float | None = None
    a_beta: float | None = None
    b_beta: float | None = None
    a_information_ratio: float | None = None
    b_information_ratio: float | None = None


class BacktestComparisonSummary(BaseModel):
    """对比回测列表摘要。"""

    comparison_id: str
    name: str | None = None
    strategy_a_id: str
    strategy_b_id: str
    backtest_a_id: str
    backtest_b_id: str
    start_date: date
    end_date: date
    status: str
    comparison_metrics: ComparisonMetrics | None = None
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    finished_at: UtcDatetime | None = None
    error_message: str | None = None
    progress: int = 0


class BacktestComparisonDetail(BacktestComparisonSummary):
    """对比回测详情，含两个子回测的完整信息。"""

    backtest_a: BacktestDetail | None = None
    backtest_b: BacktestDetail | None = None
    params: dict[str, Any] | None = None


class ComparisonDailyPoint(BaseModel):
    """对比回测单日绩效摘要（仅含图表渲染所需字段，减少网络传输）。

    相比 BacktestDailyResult：
    - 保留：trade_date, portfolio_return, cumulative_return, drawdown
    - 移除：signal counts (high/mid/low), timing_regime, total_exposure,
      cash_ratio, positions (JSON dict 通常较大), benchmark_return, turnover
    """

    trade_date: date
    portfolio_return: float
    cumulative_return: float
    drawdown: float


class ComparisonDailyResponse(BaseModel):
    """对比回测每日收益响应，用于前端叠加图表渲染。

    Attributes:
        a_daily: 策略 A 的每日组合绩效摘要。
        b_daily: 策略 B 的每日组合绩效摘要。
    """

    a_daily: list[ComparisonDailyPoint]
    b_daily: list[ComparisonDailyPoint]
