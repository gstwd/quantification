export interface EtfDetail {
  etf_code: string
  exchange: string
  name_cn: string
  tracking_index_name: string
  tracking_index_code?: string | null
  category: string
  is_active: boolean
  fund_full_name?: string | null
  fund_company?: string | null
}

export interface EtfCreatePayload {
  etf_code: string
}

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

/** 单条运行明细，对应一只 ETF 的处理结果 */
export interface ResearchRunItem {
  id: number
  run_id: string
  etf_code: string
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
  active_etf_count: number
  latest_trade_date: string | null
  data_sources: DataSourceSnapshot[]
  recent_runs: ResearchRunSummary[]
  frequency: string
  database: string
  db_connected: boolean
}

/** 单个 ETF / 指数的数据新鲜度 */
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
}

/** 数据质量总览响应 */
export interface DataQualityResponse {
  etf_bars: DataFreshnessGroup
  etf_shares: DataFreshnessGroup
  index_bars: DataFreshnessGroup
  index_valuation: DataFreshnessGroup
  checked_at: string
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

export interface ShareSnapshot {
  trade_date: string
  etf_code: string
  shares_total: number | null
  shares_delta: number | null
  shares_delta_pct: number | null
  nav: number | null
  aum: number | null
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
  /** 执行模型：next_day=T日信号T+1开盘执行（默认），same_day=T日信号T日收盘执行 */
  execution_model?: 'same_day' | 'next_day'
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
}

export interface BacktestDetail extends BacktestSummary {
  universe_filter: Record<string, unknown>
  params: Record<string, unknown> | null
}

export interface BacktestDailyResult {
  trade_date: string
  portfolio_return: number
  cumulative_return: number
  drawdown: number
  high_signal_count: number
  mid_signal_count: number
  low_signal_count: number
  timing_regime?: string | null
  total_exposure?: number | null
  cash_ratio?: number | null
  positions?: Record<string, number> | null
  /** 基准指数当日收益率（%） */
  benchmark_return?: number | null
  /** 当日换手率（0-1） */
  turnover?: number | null
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
  name?: string | null
  /** 执行模型（两个策略共享）：next_day=T+1开盘执行（默认），same_day=T日收盘执行 */
  execution_model?: 'same_day' | 'next_day'
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

export interface FactorSpec {
  factor_id: string
  name: string
  category: string | null
  version: string
  description: string
  required_data: string[]
  is_active: boolean
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
  etf_count: number
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
  etf_code: string
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
  etf_code: string
  name_cn: string
  passed: boolean
  rule_results: FilterRuleResult[]
  fail_reason: string
}

/** 管线调试完整详情 */
export interface PipelineDetail {
  scoring: AssetScoreDetail[]
  filter_results: AssetFilterDetail[] | null
  timing_detail: Record<string, unknown> | null
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
  etf_code: string
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
