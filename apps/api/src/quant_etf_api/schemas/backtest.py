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
        execution_model: 执行模型，t_plus_1_open 或 t_plus_1_close。
        data_quality_mode: 数据缺口提示口径，warn（汇总提示）或 strict（逐指数提示）。
            两种口径使用同一"当日可执行候选池"，选股与收益结果完全一致，
            该字段只影响 warnings 的详细程度。
        purpose: 回测用途，research=研究期研究（不得越过研究期末端），
            validation=验证期验收，monitor=上线后监控。
        purpose_reason: 用途说明与触发来源（如优化会话 ID、生命周期刷新），
            用于验证期数据的留痕审计。
        cost_bps: 净口径指标使用的单边交易成本（基点），留空时取系统默认值。
        基准统一按所选指数的买入持有收益计算，不再区分模式。
    """

    strategy_id: str
    start_date: date
    end_date: date
    universe_mode: Literal["all", "subset"] = "all"
    index_codes: list[str] = []
    params: dict[str, Any] | None = None
    enable_benchmark: bool = True
    benchmark_index_code: str = "000300"
    execution_model: Literal["t_plus_1_open", "t_plus_1_close"] = "t_plus_1_open"
    data_quality_mode: Literal["warn", "strict"] = "warn"
    purpose: Literal["research", "validation", "monitor"] = "research"
    purpose_reason: str | None = None
    cost_bps: float | None = None


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
    # 净口径指标（按单边换手率折算成本），默认成本 10bp，明细见 stability 块
    net_annualized_return_pct: float | None = None
    net_sharpe_ratio: float | None = None
    net_excess_return_pct: float | None = None
    annualized_turnover: float | None = None


class BacktestStability(BaseModel):
    """回测稳健性指标：描述结果对历史细节的依赖程度。

    在回测读取路径由 ``domain.research.stability`` 从已落库的逐日结果现算，
    因此存量回测无需重跑即可获得；指标口径指纹一并返回，避免不同成本假设、
    执行模型或数据质量口径的指标被相互比较。

    Attributes:
        cost_bps: 成本折算使用的单边成本（基点）。
        execution_model: 该回测使用的执行模型。
        data_quality_mode: 数据缺口提示口径。
        benchmark_index_code: 基准指数代码，未启用基准时为 None。
        calendar_source: 调仓日历来源（upstream/database/not_required），
            None 表示该回测早于 C1 口径指纹引入（无法判定）。
        turnover_model: 换手口径（delta_w_v2=计入清仓/建仓腿；
            legacy_v1=历史口径，未计入）。
        cost_ladder: 多档成本并列的净口径指标（C3）。
        candidate_pool: 逐日有效候选池时间线（C6），存量回测为 None。
        annualized_turnover: 年化单边换手率（倍）。
        cost_drag_pct_per_year: 成本拖累（百分点/年）。
        net_cumulative_return_pct: 扣成本后累计收益率（%）。
        net_annualized_return_pct: 扣成本后年化收益率（%）。
        net_sharpe_ratio: 扣成本后年化夏普。
        net_excess_return_pct: 扣成本后年化超额（百分点）。
        year_return_share_max: 年度对数收益占比最大值（0-1）。
        year_return_share_hhi: 年度对数收益占比的赫芬达尔指数。
        best_year: 对数收益最大的年份。
        ex_best_year_annualized_return_pct: 剔除最好年份后的年化收益率（%）。
        ex_best_year_sharpe_ratio: 剔除最好年份后的年化夏普。
        annual_sharpe_positive_ratio: 夏普为正的自然年占比。
        segment_sharpe_positive_ratio: 三段等分区中夏普为正的比例。
        best_segment_sharpe: 最好段夏普。
        worst_segment_sharpe: 最差段夏普。
        max_drawdown_pct: 全期最大回撤（%）。
        current_drawdown_pct: 期末回撤（%）。
        current_drawdown_percentile_pct: 期末回撤在自身逐日回撤分布中的分位。
        max_drawdown_days: 最长水下持续天数。
        average_exposure: 平均仓位，无数据时为 None。
        position_concentration: 持仓权重平均赫芬达尔指数，无数据时为 None。
    """

    cost_bps: float
    execution_model: str | None = None
    data_quality_mode: str | None = None
    benchmark_index_code: str | None = None
    calendar_source: str | None = None
    turnover_model: str | None = None
    cost_ladder: list[CostLadderEntry] = Field(default_factory=list)
    candidate_pool: BacktestCandidatePool | None = None
    annualized_turnover: float = 0.0
    cost_drag_pct_per_year: float = 0.0
    net_cumulative_return_pct: float = 0.0
    net_annualized_return_pct: float = 0.0
    net_sharpe_ratio: float = 0.0
    net_excess_return_pct: float | None = None
    year_return_share_max: float = 0.0
    year_return_share_hhi: float = 0.0
    best_year: int | None = None
    ex_best_year_annualized_return_pct: float = 0.0
    ex_best_year_sharpe_ratio: float = 0.0
    annual_sharpe_positive_ratio: float = 0.0
    segment_sharpe_positive_ratio: float = 0.0
    best_segment_sharpe: float = 0.0
    worst_segment_sharpe: float = 0.0
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    current_drawdown_percentile_pct: float = 0.0
    max_drawdown_days: int = 0
    average_exposure: float | None = None
    position_concentration: float | None = None


class CostLadderEntry(BaseModel):
    """单个成本档位下的净口径指标（C3）。

    Attributes:
        cost_bps: 该档位的单边交易成本（基点），0 表示毛口径。
        net_cumulative_return_pct: 扣成本后累计收益率（%）。
        net_annualized_return_pct: 扣成本后年化收益率（%）。
        net_sharpe_ratio: 扣成本后年化夏普比率。
        net_excess_return_pct: 扣成本后相对基准的年化超额（百分点），无基准时为 None。
        cost_drag_pct_per_year: 该档位的成本拖累（百分点/年）。
    """

    cost_bps: float
    net_cumulative_return_pct: float
    net_annualized_return_pct: float
    net_sharpe_ratio: float
    net_excess_return_pct: float | None = None
    cost_drag_pct_per_year: float = 0.0


class CandidatePoolSegment(BaseModel):
    """有效候选池规模的游程段（C6）。

    Attributes:
        start_date: 该段起始交易日（含）。
        end_date: 该段结束交易日（含）。
        trading_days: 该段交易日数。
        size: 该段逐日有效候选池规模（指数个数）。
    """

    start_date: date
    end_date: date
    trading_days: int
    size: int


class CandidatePoolExclusion(BaseModel):
    """因数据缺失被剔除的指数—日期区间（C6）。

    Attributes:
        index_code: 指数代码。
        first_date: 首次被剔除的交易日。
        last_date: 末次被剔除的交易日。
        trading_days: 被剔除的交易日数。
        reasons: 剔除原因集合（MISSING_CLOSE / MISSING_FACTOR / MISSING_OPEN /
            MISSING_HIGH_LOW）。
    """

    index_code: str
    first_date: date
    last_date: date
    trading_days: int
    reasons: list[str] = Field(default_factory=list)


class BacktestCandidatePool(BaseModel):
    """回测逐日"有效候选池"时间线（C6）。

    回测只在 warnings 里给一条汇总提示时，看不出"早期实际可交易池只有 13~16 个"；
    本结构给出逐日规模（游程编码）与剔除区间，用于判断早期结论是否建立在
    缩水的候选池上。

    Attributes:
        base_size: 回测标的池的理论规模（策略限定范围后的指数个数）。
        min_size: 逐日有效候选池的最小规模。
        max_size: 逐日有效候选池的最大规模。
        median_size: 逐日有效候选池的中位数规模。
        trading_days: 参与统计的交易日数。
        pool_coverage_ratio: 逐日有效规模之和 / (理论规模 × 交易日数)。
        segments: 规模游程段（连续相同规模合并）。
        exclusions: 因数据缺失被剔除的指数—日期区间（按天数降序、已截断）。
        truncated_exclusions: 剔除明细是否被截断。
    """

    base_size: int
    min_size: int
    max_size: int
    median_size: int
    trading_days: int
    pool_coverage_ratio: float
    segments: list[CandidatePoolSegment] = Field(default_factory=list)
    exclusions: list[CandidatePoolExclusion] = Field(default_factory=list)
    truncated_exclusions: bool = False


class DanglingReferenceItem(BaseModel):
    """一条悬挂引用（C5）：持有者引用的回测已不存在。

    Attributes:
        holder: 持有者类型（robustness_run.variants / strategy_optimization /
            strategy_optimization.fold_backtests）。
        holder_id: 持有者主键（批次 ID / 优化会话 ID）。
        field: 持有者内部字段路径。
        missing_backtest_ids: 已不存在的回测 ID 列表。
    """

    holder: str
    holder_id: str
    field: str
    missing_backtest_ids: list[str] = Field(default_factory=list)


class DanglingReferenceReport(BaseModel):
    """悬挂引用审计报告（C5）。

    Attributes:
        total: 悬挂引用条数（按回测 ID 计）。
        items: 明细（最多 limit 条）。
    """

    total: int = 0
    items: list[DanglingReferenceItem] = Field(default_factory=list)


class BacktestDeleteResponse(BaseModel):
    """回测删除结果（C5）。

    Attributes:
        backtest_id: 被删除的回测 ID。
        deleted: 是否确实删除了记录。
        holders: 删除前检测到的引用持有者（JSONB 类引用需 force 才会删除）。
        candidate_pool_removed: 说明日结果/对比记录由外键级联清理。
    """

    backtest_id: str
    deleted: bool
    holders: list[DanglingReferenceItem] = Field(default_factory=list)
    message: str


class BacktestPruneResponse(BaseModel):
    """JSONB 悬挂引用清理结果（C5）。

    Attributes:
        dry_run: 是否为预演（True 时不写库）。
        robustness_batches: 被处理的稳健性批次数量。
        removed_windows: 移除的窗口引用数量。
        optimization_sessions: 被处理的优化会话数量。
        nulled_folds: 置空的折回测引用数量。
        items: 明细（批次/会话级）。
    """

    dry_run: bool = True
    robustness_batches: int = 0
    removed_windows: int = 0
    optimization_sessions: int = 0
    nulled_folds: int = 0
    items: list[DanglingReferenceItem] = Field(default_factory=list)


class ValidationUsageItem(BaseModel):
    """验证期数据使用记录（留痕）。

    Attributes:
        backtest_id: 回测 ID。
        strategy_id: 策略 ID。
        strategy_version: 回测创建时的策略版本。
        config_hash: 回测创建时的配置哈希。
        purpose: 用途（validation/monitor）。
        purpose_reason: 用途说明与触发来源。
        start_date: 回测起始日期。
        end_date: 回测截止日期。
        created_at: 回测创建时间（UTC）。
    """

    backtest_id: str
    strategy_id: str
    strategy_version: str | None = None
    config_hash: str | None = None
    purpose: str
    purpose_reason: str | None = None
    start_date: date
    end_date: date
    created_at: UtcDatetime


class ValidationUsageResponse(BaseModel):
    """验证期数据使用留痕列表响应。

    Attributes:
        items: 使用记录列表（按创建时间倒序）。
        total: 记录总数。
    """

    items: list[ValidationUsageItem] = Field(default_factory=list)
    total: int = 0


class AnnualMetrics(BaseModel):
    """单自然年的回测绩效汇总（分年度表）。"""

    year: int
    trading_days: int
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float


class BacktestSummary(BaseModel):
    """回测列表摘要，不含明细数据。

    Attributes:
        job_status: 对应后台任务状态（pending/running/success/failed/cancelled/paused），
            None 表示该回测未经过队列（如 CLI 同步执行）。
        queued_seconds: 仍在排队时的等待时长（秒）。
        elapsed_seconds: 执行中已耗时（秒）。
        queue_position: 回测 lane 内的排队位置（0 表示下一个执行）。
        job_priority: 队列优先级。
        batch_id: 批次标识（如稳健性批次 ID）。
    """

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
    purpose: str = "research"
    job_status: str | None = None
    queued_seconds: float | None = None
    elapsed_seconds: float | None = None
    queue_position: int | None = None
    job_priority: int | None = None
    batch_id: str | None = None


class BacktestCancelResponse(BaseModel):
    """回测取消请求的处理结果（B2）。

    Attributes:
        backtest_id: 回测标识。
        status: 取消请求发出时的回测状态。
        job_id: 队列任务 ID，None 表示该回测未经过队列。
        job_status: 处理后的队列任务状态（cancelled 或 running）。
        cancel_requested: True 表示任务正在运行，已打协作取消标记。
        message: 面向用户的结果说明。
    """

    backtest_id: str
    status: str
    job_id: str | None = None
    job_status: str | None = None
    cancel_requested: bool = False
    message: str


class BacktestDetail(BacktestSummary):
    """回测详情，含配置信息。"""

    universe_filter: dict[str, Any]
    params: dict[str, Any] | None = None
    warnings: list[BacktestWarning] = Field(default_factory=list)
    config_snapshot: dict[str, Any] | None = None
    config_hash: str | None = None
    data_cutoff_date: date | None = None
    optimization_id: str | None = None
    annual_metrics: list[AnnualMetrics] = Field(default_factory=list)
    stability: BacktestStability | None = None
    purpose_reason: str | None = None


class BacktestDailyResult(BaseModel):
    """回测单日组合绩效。

    Attributes:
        trade_date: 交易日。
        portfolio_return: 当日组合收益率（%）。
        cumulative_return: 累计收益率（%）。
        drawdown: 回撤（%）。
        timing_regime: 择时状态（资产配置模式）。
        total_exposure: 总仓位比例（资产配置模式）。
        cash_ratio: 现金比例（资产配置模式）。
        positions: 持仓明细（资产配置模式），index_code → 权重。
        benchmark_return: 基准日收益率（%）。
        turnover: 当日换手率。
        missing_bar_count: 当日受数据缺口影响的持仓资产数（B10）。
        rolling_sharpe_252: 截至当日的 252 交易日滚动夏普比率，样本不足时为 None。
        rolling_sortino_252: 截至当日的 252 交易日滚动索提诺比率，样本不足时为 None。
    """

    trade_date: date
    portfolio_return: float
    cumulative_return: float
    drawdown: float
    timing_regime: str | None = None
    total_exposure: float | None = None
    cash_ratio: float | None = None
    positions: dict[str, float] | None = None
    executed_positions: dict[str, float] | None = None
    benchmark_return: float | None = None
    turnover: float | None = None
    missing_bar_count: int = 0
    rolling_sharpe_252: float | None = None
    rolling_sortino_252: float | None = None


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
        execution_model: 两个子回测共用的执行模型。
        data_quality_mode: 两个子回测共用的数据缺口提示口径（口径一致，只影响 warnings 详细程度）。
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
    execution_model: Literal["t_plus_1_open", "t_plus_1_close"] = "t_plus_1_open"
    data_quality_mode: Literal["warn", "strict"] = "warn"
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
    - 移除：timing_regime, total_exposure,
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
