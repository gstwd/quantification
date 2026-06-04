# Quant ETF Asset Allocation System Overview

This project is an A-share ETF asset allocation decision system.

## Scope
- A-share ETFs only
- Daily data only
- Asset allocation decision system (timing + rotation + position sizing)
- Dual-mode backtesting (signal scoring + asset allocation)
- PostgreSQL persistence
- FastAPI backend
- Vue 3 frontend
- Strategy plugins with optional decision pipeline

## Layout
- `apps/api/`: backend API and plugin runtime
- `apps/web/`: Vue 3 research frontend
- `packages/contracts/`: shared API/schema placeholders
- `docs/architecture/`: system docs
- `etf-three-factor-org/`: legacy reference implementation

## Current built-in plugins
- `three_factor_guard` — signal scoring mode
- `share_flow_monitor` — signal scoring mode
- `volume_breakout_daily` — signal scoring mode
- `etf_allocation` — asset allocation mode (timing → rotation → position sizing)
