# Data Model

## Reference tables
- `etf_universe`
- `benchmark_index`

## Market data tables
- `etf_daily_bar`
- `index_daily_bar`
- `etf_daily_share`
- `index_valuation`
- `macro_indicator`
- `source_payload_log`

## Research tables
- `factor_definition`
- `etf_factor_value`
- `signal_definition`
- `etf_signal`

## Runtime tables
- `strategy_plugin`
- `research_run`
- `research_run_item`

## Backtest tables
- `backtest_run` — 回测运行记录，包含 backtest_mode（存储在 params JSON 字段）
- `backtest_daily_result` — 逐日回测结果，包含择时信号、仓位信息
- `backtest_etf_result` — 单 ETF 回测明细
