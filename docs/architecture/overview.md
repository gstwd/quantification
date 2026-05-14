# Quant ETF Research Platform Overview

This project is an A-share ETF daily-frequency research platform.

## Scope
- A-share ETFs only
- Daily data only
- Research platform only
- PostgreSQL persistence
- FastAPI backend
- Vue 3 frontend
- Strategy plugins for research signals

## Layout
- `apps/api/`: backend API and plugin runtime
- `apps/web/`: Vue 3 research frontend
- `packages/contracts/`: shared API/schema placeholders
- `docs/architecture/`: system docs
- `etf-three-factor-org/`: legacy reference implementation

## Current built-in plugins
- `three_factor_guard`
- `share_flow_monitor`
- `volume_breakout_daily`
