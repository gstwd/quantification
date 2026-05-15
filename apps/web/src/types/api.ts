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
}

export interface BacktestEtfResult {
  trade_date: string
  etf_code: string
  signal_score: number
  signal_level: string
  in_portfolio: boolean
  etf_return: number | null
}
