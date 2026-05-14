# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quant ETF Research Platform — a quantitative research platform for A-share ETFs (daily frequency only, no individual stocks, no trading execution). Full-stack: FastAPI backend + PostgreSQL + Vue 3 frontend.

## Commands

### Backend (`apps/api`)

```bash
cd apps/api
pip install -e ".[dev]"          # install with dev deps
cp .env.example .env             # configure DATABASE_URL etc.
alembic upgrade head             # apply migrations
uvicorn quant_etf_api.main:app --reload --port 8000  # dev server
```

```bash
pytest                           # run all tests (22 unit tests for three-factor model)
pytest tests/path/to/test.py     # run single test file
ruff check .                     # lint
ruff format .                    # format
```

Swagger UI: `http://localhost:8000/docs`

### Frontend (`apps/web`)

```bash
cd apps/web
npm install
npm run dev                      # dev server at http://localhost:5173
npm run build
npm run lint
```

### Database migrations

```bash
cd apps/api
alembic revision --autogenerate -m "description"   # generate migration
alembic upgrade head                               # apply
```

## Architecture

### Backend layers

```
HTTP → api/routers/ → services/ → infra/ → PostgreSQL
```

- **`api/routers/`** — 7 route groups: `health`, `system`, `etfs`, `market_data`, `strategies`, `signals`, `runs`
- **`services/`** — Business logic; currently return stub data (real data integration pending)
- **`infra/db/`** — SQLAlchemy 2 ORM models (`infra/db/models/core.py` has all 13 tables)
- **`infra/clients/`** — External API wrappers: `tencent.py` (K-lines), `eastmoney.py` (shares), `exchange_reference.py` — defined but not yet wired into services
- **`infra/scheduler/`** — Task scheduling
- **`plugins/`** — Strategy plugin system (see below)
- **`config/`** — Pydantic settings loaded from `.env`

### Database schema (13 tables)

| Group | Tables |
|---|---|
| Reference | `etf_universe`, `benchmark_index` |
| Market data | `etf_daily_bar`, `index_daily_bar`, `etf_daily_share`, `source_payload_log` |
| Analytics | `factor_definition`, `etf_factor_value`, `signal_definition`, `etf_signal` |
| Runtime | `strategy_plugin`, `research_run`, `research_run_item` |

### Plugin system

Plugins implement the `StrategyPlugin` Protocol (structural subtyping — no inheritance required). Required interface:

- **Metadata attributes:** `strategy_id`, `display_name`, `version`, `frequency`, `asset_scope`, `description`
- **Methods:** `parameter_schema()`, `required_inputs()`, `factor_definitions()`, `signal_definition()`, `prepare_context()`, `run_for_universe()`, `explain_result()`

Built-in plugins in `plugins/builtins/`:
1. **`three_factor_guard`** — Volume probability (50%) + direction probability (20%) + share probability (30%); signal levels HIGH ≥70, MID 50–69, LOW <50
2. **`share_flow_monitor`** — Single-factor share flow validation
3. **`volume_breakout_daily`** — Volume breakout baseline

To add a strategy: create a plugin file in `plugins/builtins/`, implement the Protocol, register in `plugins/registry.py`.

### Frontend

- **Pages** (`src/pages/`): Dashboard, ETF list, ETF detail, Strategy list, Strategy detail, Runs, Data status
- **State** (`src/stores/`): 3 Pinia stores — `etfs`, `strategies`, `signals`; components interact with stores only, never call API directly
- **API layer** (`src/api/`): Axios wrappers; stores call these
- Charts use ECharts 5

## Current State

All services return stub/hardcoded data. The external API clients and database models are fully defined — the next integration step is wiring `infra/clients/` into `services/` and persisting to the database.
