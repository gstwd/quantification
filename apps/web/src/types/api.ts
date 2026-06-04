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

export interface StrategyDetail {
  strategy_id: string
  display_name: string
  version: string
  frequency: string
  asset_scope: string
  description: string
  parameter_schema: Record<string, unknown>
  required_inputs: string[]
  factors: Array<Record<string, unknown>>
  signal_definition: Record<string, unknown>
}

export interface SignalRow {
  trade_date: string
  etf_code: string
  strategy_id: string
  signal_score: number
  signal_level: string
  signal_label: string
  signal_payload: Record<string, unknown>
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
  asset_scope: string
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
  etf_codes: string[]
  params?: Record<string, unknown> | null
  weighting: 'equal' | 'signal_weighted'
  backtest_mode?: 'signal' | 'allocation'
}

export interface BacktestMetrics {
  cumulative_return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  win_rate_pct: number
  signal_accuracy_pct: number
  total_trading_days: number
  active_days: number
}

export interface BacktestSummary {
  backtest_id: string
  strategy_id: string
  start_date: string
  end_date: string
  status: string
  weighting: string
  backtest_mode?: string
  metrics: BacktestMetrics | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  error_message: string | null
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
}

export interface BacktestEtfResult {
  trade_date: string
  etf_code: string
  signal_score: number
  signal_level: string
  in_portfolio: boolean
  etf_return: number | null
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
  etf_code: string
  factor_id: string
  factor_value_numeric: number | null
  factor_value_text: string | null
  factor_payload: Record<string, unknown>
  strategy_id: string | null
}

/** 横截面数据行，包含 ETF 中文名 */
export interface CrossSectionRow {
  etf_code: string
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
  reasoning: string
}

export interface AllocationResponse {
  timing: AllocationTiming
  rankings: AllocationRanking[]
  plan: AllocationPlan
}

/** 统一分页响应格式 */
export interface PaginatedResponse<T> {
  items: T[]
  total: number
  offset: number
  limit: number
}
