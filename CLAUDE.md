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

- **`api/routers/`** — 8 route groups: `health`, `system`, `etfs`, `market_data`, `strategies`, `signals`, `runs`, `backtests`
- **`services/`** — Business logic; currently return stub data (real data integration pending)
- **`infra/db/`** — SQLAlchemy 2 ORM models (`infra/db/models/core.py` has all 16 tables)
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
| Backtest | `backtest_run`, `backtest_daily_result`, `backtest_etf_result` |

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

- **Pages** (`src/pages/`): Dashboard, ETF list, ETF detail, Strategy list, Strategy detail, Runs, Data status, Backtest list, Backtest create, Backtest detail
- **State** (`src/stores/`): 4 Pinia stores — `etfs`, `strategies`, `signals`, `backtests`; components interact with stores only, never call API directly
- **API layer** (`src/api/`): Axios wrappers; stores call these
- Charts use ECharts 5

## Current State

Services are wired to PostgreSQL and real external clients. 16 tables exist across migrations `0001_initial_schema`, `ac0cbcadbda1_add_column_comments`, `0002_add_backtest_tables`. Backtest engine is in `services/backtest_service.py`; router at `api/routers/backtests.py`. On startup, `UniverseService._seed()` upserts the 4 seed ETFs. `IngestService` fetches from Tencent (K-lines) and Eastmoney (shares) on first request and persists to DB; subsequent requests read from DB. Signal/Run services read from DB with stub fallback when empty.

## Gotchas

- **Alembic**: `alembic/versions/` was empty on init — autogenerate requires a live DB connection. Hand-write the first migration if the DB is blank.
- **SQLAlchemy**: Stack is fully **sync** (`create_engine`, `sessionmaker`). Do not introduce async.
- **DB session injection**: Services take `db: Session` in `__init__`. Routers use `Depends(get_db)` from `api/deps.py` and construct services per-request (no module-level singletons).
- **`DailyBar.code` vs `etf_code`**: The schema field is `code` (not `etf_code`). Map `EtfDailyBarModel.etf_code → DailyBar(code=...)`.
- **EastmoneyClient coverage**: Only 7 ETFs in `market_map`; `fetch_share_snapshot` returns `None` for unknown codes — handle gracefully.
- **TencentClient response key**: API returns `qfqday` (not `day`) for forward-adjusted data. `fetch_daily_bars` reads `qfqday` with fallback to `day`.
- **Sync blocking in uvicorn**: Services use synchronous `urlopen` for external APIs. FastAPI runs sync routes in a thread pool (default 40 threads). Concurrent cold-start requests can exhaust the pool and cause timeouts — use a per-resource `threading.Lock` to serialize first-fetch, then read from DB on subsequent requests.
- **ECharts + TypeScript**: `echarts/index.d.ts` triggers TS1203 with `vue-tsc`. Fix: add `"skipLibCheck": true` to `apps/web/tsconfig.json`.
- **Backend venv on Windows**: Executables are at `apps/api/.venv/Scripts/` (e.g. `.venv/Scripts/alembic`, `.venv/Scripts/python`).
- **`strategy_plugin` table is always empty** — strategies are managed by the in-memory `StrategyRegistry`. Never add a FK referencing `strategy_plugin`; it will silently reject all inserts due to FK violation.
- **Backtest `context.extra["etf_bars"]`**: `BacktestService` injects real historical bar data here before calling `plugin.run_for_universe()`. Plugins check this key first and fall back to stubs when absent (live runs).

## Coding Standards

**Before generating or modifying any code, read [`docs/CODING_STANDARDS.md`](docs/CODING_STANDARDS.md).**

Key rules (details in the doc):
- All comments and docstrings must be in **Chinese**
- Every Python class/function/method must have a Chinese Google-style docstring
- Every TypeScript function must have a Chinese JSDoc comment
- When refactoring, **update** existing comments — never delete them
- No `any` types in TypeScript; use semantic HTTP status codes in routers
