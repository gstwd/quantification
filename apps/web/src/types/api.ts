export interface StrategySummary {
  strategy_id: string
  display_name: string
  version: string
  frequency: string
  description: string
  status: string
  /** 是否星标关注 */
  is_starred: boolean
  /** 策略绑定的指数代码列表，空数组表示全指数通用 */
  index_codes: string[]
  /** 是否为稳健性验证派生的变体草稿策略（D-4） */
  is_variant?: boolean
  /** 派生来源批次 ID（稳健性验证批次），手工策略为 null（D-4） */
  source_batch_id?: string | null
  /** 验证期（2026-01-01 起）数据首次被消费的时间（D-5） */
  validation_consumed_at?: string | null
  /** 验证期消费说明（D-5） */
  validation_consumed_note?: string | null
}

export interface StrategyDetail extends StrategySummary {
  config_json: Record<string, unknown>
  created_at: string | null
  updated_at: string | null
}

export interface StrategyConfigCreate {
  strategy_id: string
  display_name: string
  version?: string
  description?: string
  frequency?: string
  config_json: Record<string, unknown>
}

export interface StrategyConfigUpdate {
  display_name?: string
  version?: string
  description?: string
  frequency?: string
  config_json?: Record<string, unknown>
  status?: string
}

export interface StrategyValidationResult {
  valid: boolean
  errors: string[]
  warnings: string[]
}

export interface ResearchRunSummary {
  run_id: string
  run_type: string
  strategy_id?: string | null
  trade_date?: string | null
  status: string
  started_at?: string | null
  finished_at?: string | null
  error_message?: string | null
}

/** 运行记录详情，包含完整指标和耗时 */
export interface ResearchRunDetail {
  run_id: string
  run_type: string
  strategy_id?: string | null
  trade_date?: string | null
  status: string
  params?: Record<string, unknown> | null
  metrics?: Record<string, unknown> | null
  error_message?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_seconds?: number | null
}

/** 单条运行明细，对应一个指数标的的处理结果 */
export interface ResearchRunItem {
  id: number
  run_id: string
  index_code: string
  status: string
  message?: string | null
  metrics?: Record<string, unknown> | null
}

/** 单个数据表/数据源的快照信息 */
export interface DataSourceSnapshot {
  source_name: string
  table_name: string
  record_count: number
  latest_trade_date: string | null
  latest_ingested_at: string | null
}

/** 系统运行状态完整响应 */
export interface SystemStatusResponse {
  active_index_count: number
  latest_trade_date: string | null
  data_sources: DataSourceSnapshot[]
  recent_runs: ResearchRunSummary[]
  frequency: string
  database: string
  db_connected: boolean
}

/** 单个指数的数据新鲜度 */
export interface DataFreshnessItem {
  code: string
  name: string
  latest_date: string | null
  is_stale: boolean
}

/** 单个数据表的新鲜度汇总 */
export interface DataFreshnessGroup {
  total: number
  up_to_date: number
  stale: DataFreshnessItem[]
  missing: DataFreshnessItem[]
  latest_date: string | null
  /** 大数据量表（如个股）的完整过期/缺失计数，数组仅含样本 */
  stale_total?: number | null
  missing_total?: number | null
}

/** 数据质量总览响应 */
export interface DataQualityResponse {
  index_bars: DataFreshnessGroup
  index_valuation: DataFreshnessGroup
  stock_bars?: DataFreshnessGroup | null
  industry_bars?: DataFreshnessGroup | null
  checked_at: string
}

// =============================================================================
// 统一数据管理
// =============================================================================

/** 受管逻辑数据集的当前健康摘要。 */
export interface DataSetHealthSummary {
  dataset_key: string
  display_name: string
  frequency: string
  partition_label: string | null
  source_label: string
  source_name: string | null
  supported_operations: string[]
  health_status: 'healthy' | 'warning' | 'error' | 'unknown' | 'unsupported'
  earliest_date: string | null
  latest_date: string | null
  expected_date: string | null
  record_count: number
  missing_count: number
  invalid_count: number
  issue_summary: Record<string, unknown> | null
  last_run_id: string | null
  last_run_status: string | null
  last_checked_at: string | null
  last_synced_at: string | null
  last_success_at: string | null
}

/** 单个可维护数据分区的当前健康状态。 */
export interface DataPartitionHealth extends DataSetHealthSummary {
  partition_key: string
  partition_name: string | null
}

/** 数据集详情及其分区分页响应。 */
export interface DataSetDetailResponse {
  dataset: DataSetHealthSummary
  quality_rules: string[]
  items: DataPartitionHealth[]
  total: number
  offset: number
  limit: number
}

/** 数据管理首页总览。 */
export interface DataManagementOverview {
  schedule_time: string
  datasets: DataSetHealthSummary[]
  healthy_count: number
  warning_count: number
  error_count: number
  unknown_count: number
}

/** 数据维护操作名称。 */
export type DataManagementOperation = 'sync_latest' | 'check' | 'repair_gaps' | 'rebuild'

/** 提交数据维护操作的请求体。 */
export interface DataManagementOperationRequest {
  operation: DataManagementOperation
  dataset_key?: string
  partition_key?: string
  force?: boolean
  confirmation_token?: string
}

/** 数据维护任务已入队响应。 */
export interface DataManagementOperationAccepted {
  status: string
  run_id: string
  run_type: string
  operation: DataManagementOperation
}

export interface DailyBar {
  trade_date: string
  code: string
  open_price: number | null
  high_price: number | null
  low_price: number | null
  close_price: number | null
  prev_close_price: number | null
  change_pct: number | null
  volume: number | null
  turnover: number | null
  source: string
}

export interface BacktestCreateRequest {
  strategy_id: string
  start_date: string
  end_date: string
  universe_mode: 'all' | 'subset'
  index_codes: string[]
  params?: Record<string, unknown> | null
  /** 是否启用基准对比，默认 true */
  enable_benchmark?: boolean
  /** 基准指数代码，默认 000300（沪深300） */
  benchmark_index_code?: string
  execution_model?: 't_plus_1_open' | 't_plus_1_close'
  data_quality_mode?: 'warn' | 'strict'
  /** 回测用途：research=研究期研究（不得越过研究期末端），validation=验证期验收，monitor=上线后监控 */
  purpose?: 'research' | 'validation' | 'monitor'
  /** 用途说明与触发来源，用于验证期数据留痕 */
  purpose_reason?: string | null
  /** 净口径指标使用的单边成本（基点），留空取系统默认值 */
  cost_bps?: number | null
}

export interface BacktestMetrics {
  cumulative_return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  win_rate_pct: number
  signal_accuracy_pct: number
  total_trading_days: number
  active_days: number
  /** 年化收益率（%） */
  annualized_return_pct: number
  /** 索提诺比率（下行风险调整） */
  sortino_ratio: number
  /** 卡玛比率（年化收益/最大回撤） */
  calmar_ratio: number
  /** 最大回撤持续天数 */
  max_drawdown_days: number
  /** 盈亏比（平均盈利/平均亏损） */
  profit_loss_ratio: number | null
  /** vs 基准的年化 Alpha（%） */
  alpha: number | null
  /** vs 基准的 Beta 系数 */
  beta: number | null
  /** 信息比率 */
  information_ratio: number | null
  /** 基准累计收益率（%） */
  benchmark_return_pct: number | null
  /** 超额收益率（%） */
  excess_return_pct: number | null
  /** 数据缺口天数：至少一个持仓资产受缺失行情影响的交易日数 */
  data_gap_days: number
  /** 扣成本后年化收益率（%），明细见 BacktestStability */
  net_annualized_return_pct?: number | null
  /** 扣成本后年化夏普比率 */
  net_sharpe_ratio?: number | null
  /** 扣成本后年化超额（百分点） */
  net_excess_return_pct?: number | null
  /** 年化单边换手率（倍） */
  annualized_turnover?: number | null
}

/** 单个成本档位下的净口径指标（C3） */
export interface CostLadderEntry {
  /** 单边交易成本（基点），0 表示毛口径 */
  cost_bps: number
  /** 扣成本后累计收益率（%） */
  net_cumulative_return_pct: number
  /** 扣成本后年化收益率（%） */
  net_annualized_return_pct: number
  /** 扣成本后年化夏普 */
  net_sharpe_ratio: number
  /** 扣成本后年化超额（百分点），无基准时为 null */
  net_excess_return_pct: number | null
  /** 该档位的成本拖累（百分点/年） */
  cost_drag_pct_per_year: number
}

/** 有效候选池规模的游程段（C6） */
export interface CandidatePoolSegment {
  start_date: string
  end_date: string
  trading_days: number
  size: number
}

/** 因数据缺失被剔除的指数—日期区间（C6） */
export interface CandidatePoolExclusion {
  index_code: string
  first_date: string
  last_date: string
  trading_days: number
  reasons: string[]
}

/** 回测逐日有效候选池时间线（C6） */
export interface BacktestCandidatePool {
  /** 回测标的池的理论规模 */
  base_size: number
  min_size: number
  max_size: number
  median_size: number
  trading_days: number
  /** 逐日有效规模之和 / (理论规模 × 交易日数) */
  pool_coverage_ratio: number
  segments: CandidatePoolSegment[]
  exclusions: CandidatePoolExclusion[]
  truncated_exclusions: boolean
}

/** 回测稳健性指标：描述结果对历史细节的依赖程度（读取路径现算） */
export interface BacktestStability {
  /** 成本折算使用的单边成本（基点） */
  cost_bps: number
  /** 指标口径指纹：该回测使用的执行模型 */
  execution_model: string | null
  /** 指标口径指纹：数据缺口提示口径 */
  data_quality_mode: string | null
  /** 指标口径指纹：基准指数代码 */
  benchmark_index_code: string | null
  /** 指标口径指纹：调仓日历来源（upstream / database / not_required），null=早于 C1 */
  calendar_source?: string | null
  /** 指标口径指纹：换手口径（delta_w_v2 含清仓/建仓腿；legacy_v1 未含） */
  turnover_model?: string | null
  /** 多档成本并列的净口径指标（C3） */
  cost_ladder?: CostLadderEntry[]
  /** 逐日有效候选池时间线（C6），存量回测为 null */
  candidate_pool?: BacktestCandidatePool | null
  /** 年化单边换手率（倍） */
  annualized_turnover: number
  /** 成本拖累（百分点/年） */
  cost_drag_pct_per_year: number
  /** 扣成本后累计收益率（%） */
  net_cumulative_return_pct: number
  /** 扣成本后年化收益率（%） */
  net_annualized_return_pct: number
  /** 扣成本后年化夏普 */
  net_sharpe_ratio: number
  /** 扣成本后年化超额（百分点） */
  net_excess_return_pct: number | null
  /** 年度对数收益占比最大值（0-1） */
  year_return_share_max: number
  /** 年度对数收益占比的赫芬达尔指数 */
  year_return_share_hhi: number
  /** 对数收益最大的年份 */
  best_year: number | null
  /** 剔除最好年份后的年化收益率（%） */
  ex_best_year_annualized_return_pct: number
  /** 剔除最好年份后的年化夏普 */
  ex_best_year_sharpe_ratio: number
  /** 夏普为正的自然年占比 */
  annual_sharpe_positive_ratio: number
  /** 三段等分区中夏普为正的比例 */
  segment_sharpe_positive_ratio: number
  /** 最好段夏普 */
  best_segment_sharpe: number
  /** 最差段夏普 */
  worst_segment_sharpe: number
  /** 全期最大回撤（%） */
  max_drawdown_pct: number
  /** 期末回撤（%） */
  current_drawdown_pct: number
  /** 期末回撤在自身逐日回撤分布中的分位 */
  current_drawdown_percentile_pct: number
  /** 最长水下持续天数 */
  max_drawdown_days: number
  /** 平均仓位 */
  average_exposure: number | null
  /** 持仓权重平均赫芬达尔指数 */
  position_concentration: number | null
}

/** 单自然年的回测绩效汇总（分年度表） */
export interface AnnualMetrics {
  year: number
  /** 该年参与统计的交易日数 */
  trading_days: number
  /** 该年累计收益率（%） */
  total_return_pct: number
  /** 该年年化收益率（%） */
  annualized_return_pct: number
  /** 该年夏普比率 */
  sharpe_ratio: number
  /** 该年索提诺比率 */
  sortino_ratio: number
  /** 该年最大回撤（%，负值） */
  max_drawdown_pct: number
}

export interface BacktestSummary {
  backtest_id: string
  strategy_id: string
  start_date: string
  end_date: string
  status: string
  metrics: BacktestMetrics | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  /** 执行进度（0-100），running 状态时有效 */
  progress: number
  /** 回测用途：research / validation / monitor */
  purpose: string
  /** 对应后台任务状态；null 表示该回测未经过队列（如 CLI 同步执行） */
  job_status?: string | null
  /** 仍在排队时的等待时长（秒） */
  queued_seconds?: number | null
  /** 执行中已耗时（秒） */
  elapsed_seconds?: number | null
  /** 回测 lane 内的排队位置（0 = 下一个执行） */
  queue_position?: number | null
  /** 队列优先级 */
  job_priority?: number | null
  /** 批次标识（如稳健性批次 ID） */
  batch_id?: string | null
}

/** 回测取消请求的处理结果 */
export interface BacktestCancelResponse {
  backtest_id: string
  status: string
  job_id?: string | null
  job_status?: string | null
  /** true 表示任务运行中，已打协作取消标记 */
  cancel_requested: boolean
  message: string
}

/** 一条悬挂引用：持有者引用的回测已不存在（C5） */
export interface DanglingReferenceItem {
  /** 持有者类型：robustness_run.variants / strategy_optimization / strategy_optimization.fold_backtests */
  holder: string
  /** 持有者主键（批次 ID / 优化会话 ID） */
  holder_id: string
  /** 持有者内部字段路径 */
  field: string
  /** 已不存在的回测 ID 列表 */
  missing_backtest_ids: string[]
}

/** 悬挂引用审计报告（C5） */
export interface DanglingReferenceReport {
  total: number
  items: DanglingReferenceItem[]
}

/** 回测删除结果（C5） */
export interface BacktestDeleteResponse {
  backtest_id: string
  deleted: boolean
  holders: DanglingReferenceItem[]
  message: string
}

/** 队列中单个运行中任务的摘要 */
export interface QueueRunningJob {
  job_id: string
  job_type: string
  lane: string
  batch_id?: string | null
  attempts: number
  elapsed_seconds: number
  cancel_requested: boolean
}

/** 按任务类型统计的队列积压 */
export interface QueueBacklogItem {
  job_type: string
  pending: number
  running: number
  paused: number
  oldest_pending_at?: string | null
}

/** 队列并发预算与异常回收阈值 */
export interface QueueCapacity {
  general_workers: number
  backtest_workers: number
  heartbeat_interval_seconds: number
  stuck_timeout_seconds: number
  max_runtime_seconds: number
  zombie_scan_enabled: boolean
}

/** 后台任务队列统计（积压 / 吞吐 / 运行中任务 / 并发预算） */
export interface QueueStatsResponse {
  status_counts: Record<string, number>
  pending: number
  running: number
  paused: number
  throughput_window_hours: number
  throughput: Record<string, number>
  backlog_by_type: QueueBacklogItem[]
  running_jobs: QueueRunningJob[]
  capacity: QueueCapacity
}

export interface BacktestDetail extends BacktestSummary {
  universe_filter: Record<string, unknown>
  params: Record<string, unknown> | null
  /** 回测执行过程中的结构化提示（预热期/因子缺失/数据缺口/部分结果等） */
  warnings?: BacktestWarning[]
  /** 分年度绩效表（仅成功回测返回） */
  annual_metrics?: AnnualMetrics[]
  /** 稳健性指标（仅成功回测返回，读取路径从逐日结果现算） */
  stability?: BacktestStability | null
  /** 回测用途说明与触发来源 */
  purpose_reason?: string | null
}

export interface BacktestDailyResult {
  trade_date: string
  portfolio_return: number
  cumulative_return: number
  drawdown: number
  timing_regime?: string | null
  total_exposure?: number | null
  cash_ratio?: number | null
  positions?: Record<string, number> | null
  /** 收益窗口实际执行持仓 */
  executed_positions?: Record<string, number> | null
  /** 基准指数当日收益率（%） */
  benchmark_return?: number | null
  /** 当日换手率（0-1） */
  turnover?: number | null
  /** 当日受数据缺口影响的持仓资产数（0=无缺口） */
  missing_bar_count?: number
  /** 截至当日的 252 交易日滚动夏普比率，样本不足时为 null */
  rolling_sharpe_252?: number | null
  /** 截至当日的 252 交易日滚动索提诺比率，样本不足时为 null */
  rolling_sortino_252?: number | null
}

/** 回测执行过程中的结构化提示级别 */
export type BacktestWarningLevel = 'info' | 'warning' | 'error'

/** 回测执行过程中的结构化提示（预热期/因子缺失/数据缺口/部分结果等） */
export interface BacktestWarning {
  level: BacktestWarningLevel
  code: string
  message: string
  trade_date?: string | null
  index_code?: string | null
}

export interface BacktestIndexResult {
  trade_date: string
  index_code: string
  signal_score: number
  signal_level: string
  in_portfolio: boolean
  index_return: number | null
  /** 信号目标仓位权重（0-1），与实时信号 payload.target_weight 同义 */
  target_weight?: number | null
}

// =============================================================================
// 策略对比回测模块
// =============================================================================

/** 策略对比回测创建请求 */
export interface ComparisonCreateRequest {
  strategy_a_id: string
  strategy_b_id: string
  start_date: string
  end_date: string
  /** 策略 A 的指数代码列表（空数组=全部指数）。若策略 A 已配置标的范围则忽略 */
  a_index_codes: string[]
  /** 策略 B 的指数代码列表（空数组=全部指数）。若策略 B 已配置标的范围则忽略 */
  b_index_codes: string[]
  enable_benchmark?: boolean
  benchmark_index_code?: string
  execution_model?: 't_plus_1_open' | 't_plus_1_close'
  data_quality_mode?: 'warn' | 'strict'
  name?: string | null
}

/** 双策略对比指标 */
export interface ComparisonMetrics {
  // ── 策略 A/B 各自指标 ──
  a_cumulative_return_pct: number
  b_cumulative_return_pct: number
  a_annualized_return_pct: number
  b_annualized_return_pct: number
  a_max_drawdown_pct: number
  b_max_drawdown_pct: number
  a_sharpe_ratio: number
  b_sharpe_ratio: number
  a_sortino_ratio: number
  b_sortino_ratio: number
  a_calmar_ratio: number
  b_calmar_ratio: number
  a_win_rate_pct: number
  b_win_rate_pct: number
  a_signal_accuracy_pct: number
  b_signal_accuracy_pct: number
  a_total_trading_days: number
  b_total_trading_days: number
  a_active_days: number
  b_active_days: number

  // ── 差值（A - B） ──
  cumulative_return_diff_pct: number
  annualized_return_diff_pct: number
  max_drawdown_diff_pct: number
  sharpe_diff: number
  sortino_diff: number
  calmar_diff: number
  win_rate_diff_pct: number
  signal_accuracy_diff_pct: number

  // ── 基准对比（若启用） ──
  a_benchmark_return_pct: number | null
  b_benchmark_return_pct: number | null
  a_excess_return_pct: number | null
  b_excess_return_pct: number | null
  a_alpha: number | null
  b_alpha: number | null
  a_beta: number | null
  b_beta: number | null
  a_information_ratio: number | null
  b_information_ratio: number | null
}

/** 对比回测列表摘要 */
export interface ComparisonSummary {
  comparison_id: string
  name: string | null
  strategy_a_id: string
  strategy_b_id: string
  backtest_a_id: string
  backtest_b_id: string
  start_date: string
  end_date: string
  status: string
  comparison_metrics: ComparisonMetrics | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
  progress: number
}

/** 对比回测详情 */
export interface ComparisonDetail extends ComparisonSummary {
  backtest_a: BacktestDetail | null
  backtest_b: BacktestDetail | null
  params: Record<string, unknown> | null
}

/** 对比回测单日绩效摘要（仅图表渲染所需字段，减少网络传输） */
export interface ComparisonDailyPoint {
  trade_date: string
  portfolio_return: number
  cumulative_return: number
  drawdown: number
}

/** 对比回测每日收益响应（两个策略的叠加数据） */
export interface ComparisonDailyResponse {
  a_daily: ComparisonDailyPoint[]
  b_daily: ComparisonDailyPoint[]
}

export interface IndexValuation {
  trade_date: string
  index_code: string
  pe: number | null
  pe_percentile: number | null
  pb: number | null
  pb_percentile: number | null
  dividend_yield: number | null
  source: string
}

export interface MacroIndicator {
  indicator_code: string
  indicator_name: string
  period: string
  value: number
  unit: string | null
  source: string
}

export interface BenchmarkIndex {
  index_code: string
  index_name: string
}

/** 指数列表汇总数据，包含最新行情和估值快照 */
export interface IndexSummary {
  index_code: string
  index_name: string
  close_price: number | null
  change_pct: number | null
  bar_date: string | null
  pe: number | null
  pe_percentile: number | null
  pb: number | null
  pb_percentile: number | null
  dividend_yield: number | null
  valuation_date: string | null
}

export interface IndexCreatePayload {
  index_code: string
  name_cn?: string
}

export interface DateRange {
  min_date: string | null
  max_date: string | null
}

/** 单指数日线数据质量统计 */
export interface BarQuality {
  total: number
  min_date: string | null
  max_date: string | null
  missing_open: number
  missing_high: number
  missing_low: number
  missing_close: number
  incomplete_rows: number
  incomplete_ratio: number
}

/** 单指数估值数据质量统计 */
export interface ValuationQuality {
  total: number
  min_date: string | null
  max_date: string | null
  missing_pe: number
  missing_pb: number
  missing_dividend_yield: number
}

/** 指数详情页数据质量总览 */
export interface IndexDataQuality {
  index_code: string
  bars: BarQuality
  valuations: ValuationQuality
}

export interface FactorSpec {
  factor_id: string
  name: string
  category: string | null
  version: string
  description: string
  required_data: string[]
  is_active: boolean
  /** 因子值形态：asset/market */
  value_shape?: 'asset' | 'market'
  /** 适用位置：timing/score/filter/rank */
  usage?: string[]
  /** 因子默认参数（参数化因子） */
  default_params?: Record<string, unknown> | null
}

export interface FactorRow {
  trade_date: string
  index_code: string
  factor_id: string
  factor_value_numeric: number | null
  factor_value_text: string | null
  factor_payload: Record<string, unknown>
  strategy_id: string | null
}

/** 横截面数据行，包含指数中文名 */
export interface CrossSectionRow {
  index_code: string
  name_cn: string
  factor_value_numeric: number | null
  factor_value_text: string | null
}

/** 横截面查询响应 */
export interface CrossSectionResponse {
  factor_id: string
  trade_date: string
  rows: CrossSectionRow[]
}

/** 因子编辑请求体 */
export interface FactorUpdatePayload {
  name?: string
  description?: string
  category?: string
  is_active?: boolean
}

/** IC 时间序列单点 */
export interface ICPoint {
  trade_date: string
  ic: number
}

/** IC 汇总统计 */
export interface ICSummary {
  ic_mean: number | null
  ic_std: number | null
  ic_ir: number | null
  ic_positive_ratio: number | null
  count: number
}

/** 因子 IC 分析响应 */
export interface ICResponse {
  factor_id: string
  summary: ICSummary
  series: ICPoint[]
}

/** 因子相关性矩阵响应 */
export interface CorrelationResponse {
  factor_ids: string[]
  matrix: number[][]
  index_count: number
  trade_date: string
}

/** 单个因子在评分中的贡献分解 */
export interface FactorScoreBreakdown {
  factor_id: string
  raw_value: number | null
  transformed_value: number | null
  weight: number
  contribution: number
  status: string // "ok" | "missing_excluded" | "missing_zero" | "missing_ignored"
}

/** 单个资产的评分明细 */
export interface AssetScoreDetail {
  index_code: string
  name_cn: string
  raw_score: number | null
  final_score: number | null
  factors: FactorScoreBreakdown[]
  excluded: boolean
  exclude_reason: string
}

/** 单条过滤规则对单个资产的判定结果 */
export interface FilterRuleResult {
  rule_index: number
  factor: string
  op: string
  threshold: unknown // number | [number, number] | string (compare_to 因子ID)
  factor_value: number | null
  compare_value: number | null
  passed: boolean
}

/** 单个资产的过滤明细 */
export interface AssetFilterDetail {
  index_code: string
  name_cn: string
  passed: boolean
  rule_results: FilterRuleResult[]
  fail_reason: string
}

/** 管线调试完整详情 */
/** 择时因子明细（pipeline_detail.timing_detail 的值） */
export interface TimingFactorDetail {
  raw?: number | string | null
  transformed?: number | string | null
  weight?: number | null
  status?: string
}

export interface PipelineDetail {
  scoring: AssetScoreDetail[]
  filter_results: AssetFilterDetail[] | null
  timing_detail: Record<string, TimingFactorDetail> | null
  cross_section_stats: Record<string, unknown> | null
}

/** 资产配置决策管线响应 */
export interface AllocationTiming {
  regime: string
  confidence: number
  label: string
  factors: Record<string, unknown>
}

export interface AllocationRanking {
  index_code: string
  name_cn: string
  category: string
  score: number
  momentum_rank: number
  valuation_rank: number
  details: Record<string, unknown>
}

export interface AllocationPlan {
  positions: Record<string, number>
  total_exposure: number
  cash_ratio: number
  method: string
}

export interface AllocationResponse {
  timing: AllocationTiming
  rankings: AllocationRanking[]
  plan: AllocationPlan
  /** 本次决策所用因子数据的交易日（YYYY-MM-DD），用于展示数据新鲜度 */
  data_date?: string | null
  /** 管线调试详情，含评分分解、过滤明细、择时因子等中间数据 */
  pipeline_detail?: PipelineDetail | null
  /** 执行过程中的结构化提示（如因子缺失） */
  warnings?: BacktestWarning[]
}

/** 星标策略执行摘要中的单个策略项 */
export interface StarredStrategyItem {
  strategy_id: string
  display_name: string
  frequency: string
  /** 是否为调仓日 */
  is_rebalance_day: boolean
  /** 调仓频率（daily/weekly/monthly） */
  rebalance_frequency: string | null
  /** 周度调仓的星期几（0=周一） */
  rebalance_day_of_week: number | null
  /** 月度调仓的日期（1-31） */
  rebalance_day_of_month: number | null
  timing: AllocationTiming | null
  rankings: AllocationRanking[]
  plan: AllocationPlan
  data_date: string | null
}

/** 星标策略执行摘要响应 */
export interface StarredSummaryResponse {
  trade_date: string
  items: StarredStrategyItem[]
}

/** 统一分页响应格式 */
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  offset: number
  limit: number
}

// =============================================================================
// AI 舆情分析模块
// =============================================================================

/** AI 分析运行响应 */
export interface AIAnalysisRunResponse {
  status: string
  collected: number
  saved: number
  analyzed: number
  aggregated: number
  error: string | null
  /** 异步模式运行 ID（status=accepted 时返回） */
  run_id?: string | null
  /** 市场研判生成数（0 或 1） */
  synthesis?: number
}

/** 每日情绪聚合数据 */
export interface DailySentimentResponse {
  trade_date: string
  asset_tag: string
  /** 平均情绪分 [-1, 1] */
  avg_sentiment: number
  /** 关注度加权情绪分 [-1, 1] */
  weighted_sentiment: number
  /** 总关注度 [0, 100] */
  total_attention: number
  /** 相关新闻数量 */
  news_count: number
  /** Top 热门主题 */
  top_topics: string[]
  /** 正面新闻占比 [0, 1] */
  positive_ratio: number
  /** 负面新闻占比 [0, 1] */
  negative_ratio: number
}

/** 资产标签下的单条新闻明细 */
export interface TagNewsItem {
  title: string
  url: string
  source_name: string
  sentiment_score: number
  attention_score: number
}

/** 市场综合研判 */
export interface MarketSynthesisResponse {
  trade_date: string
  content: string
  key_topics: string[]
  risk_notes: string | null
  sentiment_summary: Record<string, unknown>
  created_at: string
}

/** 关键词→标签映射配置 */
export interface KeywordTagConfig {
  id: number
  keyword: string
  tag: string
  is_active: boolean
  priority: number
  created_at: string | null
  updated_at: string | null
}

/** 策略健康体检快照（上线后监控） */
export interface HealthSnapshot {
  id: number
  as_of_date: string
  live_start: string
  live_end: string
  health_level: string
  diagnosis: string
  recommended_action: string
  reasons: string[]
  trigger: string
  computed_at: string | null
  metrics: Record<string, unknown> | null
}

/** 策略生命周期列表项 */
export interface LifecycleSummary {
  strategy_id: string
  display_name: string | null
  lifecycle_status: string
  live_at: string
  retired_at: string | null
  live_days: number
  health_level: string | null
  diagnosis: string | null
  recommended_action: string | null
  last_refreshed_at: string | null
}

/** 策略生命周期详情（含冻结快照与最近的体检记录） */
export interface LifecycleDetail extends LifecycleSummary {
  frozen_config_hash: string
  frozen_config_snapshot: Record<string, unknown> | null
  research_backtest_id: string | null
  validation_backtest_id: string | null
  baseline_distribution: Record<string, unknown> | null
  note: string | null
  created_at: string | null
  updated_at: string | null
  snapshots: HealthSnapshot[]
}

/** 验证期数据使用记录（留痕） */
export interface ValidationUsageItem {
  backtest_id: string
  strategy_id: string
  strategy_version: string | null
  config_hash: string | null
  purpose: string
  purpose_reason: string | null
  start_date: string
  end_date: string
  created_at: string
  /** 该策略验证期数据首次被消费的时间（D-5，策略级留痕） */
  strategy_validation_consumed_at?: string | null
  /** 验证期消费说明（D-5） */
  strategy_validation_consumed_note?: string | null
}

/** 验证期数据使用留痕列表响应 */
export interface ValidationUsageResponse {
  items: ValidationUsageItem[]
  total: number
}

/** 稳健性验证批次变体 */
export interface RobustnessVariant {
  label: string
  kind: string
  knob: string | null
  value: unknown
  strategy_id: string | null
  backtest_ids: Record<string, string>
  deleted_windows: string[]
}

/** 稳健性验证批次摘要 */
export interface RobustnessSummary {
  robustness_id: string
  strategy_id: string
  strategy_version: string
  kind: string
  status: string
  start_date: string
  end_date: string
  trial_count: number
  created_at: string | null
  finished_at: string | null
  error_message: string | null
}

/** 稳健性汇总中的单个变体明细（Δ 相对基线） */
export interface RobustnessVariantDetail {
  label: string
  kind: string | null
  knob: string | null
  value: unknown
  sharpe_mean: number | null
  annualized_return_mean: number | null
  windows: number
  delta_sharpe: number | null
  delta_annualized_return: number | null
}

/** 参数邻域稳定度（scan） */
export interface RobustnessNeighborhood {
  n_variants: number
  delta_min: number | null
  delta_max: number | null
  worse_ratio: number | null
  reversal: boolean
  is_plateau: boolean
}

/** 资产池扰动分布（pool） */
export interface RobustnessPool {
  n_variants: number
  delta_median: number | null
  delta_min: number | null
  delta_max: number | null
}

/** 部分汇总覆盖率（B7） */
export interface RobustnessCoverage {
  expected_windows: number
  completed_windows: number
  pending_windows: number
  missing_windows: string[]
  failed_windows: string[]
  is_partial: boolean
}

/** 稳健性汇总结构 */
export interface RobustnessSummaryBody {
  baseline_sharpe_mean: number | null
  baseline_annualized_return_mean: number | null
  variants: RobustnessVariantDetail[]
  neighborhood?: RobustnessNeighborhood
  marginal?: Array<{
    label: string
    delta_sharpe: number | null
    delta_annualized_return: number | null
  }>
  pool?: RobustnessPool
  coverage?: RobustnessCoverage
}

/** 统计显著性（CSCV-PBO / Deflated Sharpe / 块自助法） */
export interface RobustnessStatistics {
  n_trials: number
  n_windows: number
  cost_bps: number
  pbo: {
    value: number
    n_candidates: number
    n_splits: number
    n_blocks: number
  } | null
  deflated_sharpe: {
    sharpe_annualized: number
    expected_max_sharpe_annualized: number
    deflated_sharpe: number
    n_observations: number
    n_trials: number
  } | null
  bootstrap: {
    sharpe_annualized: number
    lower: number
    upper: number
    confidence: number
    block: number
    n_bootstrap: number
  } | null
}

/** 稳健性验证批次详情 */
export interface RobustnessDetail extends RobustnessSummary {
  baseline_config_hash: string
  windows: Array<{ label: string; start: string; end: string }>
  variants: RobustnessVariant[]
  summary: RobustnessSummaryBody | null
  statistics: RobustnessStatistics | null
  missing_backtest_ids: string[]
  /** 本次扫描口径：预设、关键旋钮清单、窗口数（D-2） */
  scan_params?: Record<string, unknown>
}

/** 稳健性验证批次列表响应 */
export interface RobustnessListResponse {
  items: RobustnessSummary[]
  total: number
}
