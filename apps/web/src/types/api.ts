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
