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
pytest                           # run all tests (83 unit tests: services, factors, plugins, domain)
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

```bash
# ruff (inside .venv)
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format .
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

- **`api/routers/`** — 9 route groups: `health`, `system`, `etfs`, `market_data`, `strategies`, `signals`, `factors`, `runs`, `backtests`
- **`services/`** — Business logic; `IngestService` uses read-through cache (DB → lock → external API → upsert)
- **`infra/db/`** — SQLAlchemy 2 ORM models (`infra/db/models/core.py` has all 18 tables) + 7 repository classes (`infra/db/repositories/`). Repositories own all DB queries; services delegate to them for read operations, own only write logic.
- **`infra/clients/`** — 4 data source clients, all inherit from `base.py`:
  - `akshare_fund.py` (ETF K-line via Sina + shares/AUM via fund_etf_spot_em), `exchange_reference.py` (exchange ref)
  - `akshare_index.py` (index daily + PE/PB valuation), `akshare_macro.py` (CPI/PMI/LPR)
- **`infra/scheduler/`** — `DailyIngestScheduler`: daemon `Thread` + `Event` loop, runs at `settings.schedule_time` (default 17:30), skips weekends
- **`factors/`** — Independent factor layer: `base.py` (FactorSpec/FactorContext/FactorValue/FactorComputer Protocol), `registry.py` (FactorRegistry + build_default_factor_registry), `service.py` (FactorService), `builtins/` (6 built-in computers). Pattern mirrors `plugins/`. Context key format: `(etf_code, date)` dict, same as `domain/common/bar_metrics.py`.
- **`domain/`** — Pure domain logic: `common/bar_metrics.py` (BAR computation), `common/enums.py` (SignalLevel, RunStatus, etc.), `common/values.py` (DateRange, TradeDate), `signal_levels.py`. No infrastructure dependencies.
- **`plugins/`** — Strategy plugin system (see below)
- **`config/`** — Pydantic settings loaded from `.env`

### Database schema (18 tables, migration 0003)

| Group | Tables |
|---|---|
| Reference | `etf_universe`, `benchmark_index` |
| Market data | `etf_daily_bar`, `index_daily_bar`, `etf_daily_share`, `index_valuation`, `macro_indicator`, `source_payload_log` |
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

- **Pages** (`src/pages/`): Dashboard, ETF list, ETF detail, Index list, Index detail, Macro, Strategy list, Strategy detail, Runs, Data status, Backtest list, Backtest create, Backtest detail
- **State** (`src/stores/`): 4 Pinia stores — `etfs`, `strategies`, `signals`, `backtests`; stores are for mutable shared state only
- **API layer** (`src/api/`): Axios wrappers returning typed `PaginatedResponse<T>` (`{ items, total, offset, limit }`); `etfs.ts`, `strategies.ts`, `signals.ts`, `backtests.ts`, `runs.ts`, `market_data.ts`
- **Read-only data pages** (index/macro): Fetch data **inline** via `ref()` + `onMounted`, no Pinia store — lighter pattern for static data views
- Charts use ECharts 5 (dynamic `import('echarts')`, `watch` with `flush: 'post'`, `dispose()` in `onUnmounted`)

## Current State

Services fully wired to PostgreSQL. 18 tables across 6 migrations (0001→0002→0002_backtest→0003_index_macro→0004→0005_factor_layer). Each data type has exactly **one** source: ETF K-line→Tencent, ETF shares→Eastmoney, Index K-line→AkShare, Index valuation→AkShare, Macro→AkShare. Read-through cache pattern: GET endpoint → check DB → lock → external API → upsert via `ON CONFLICT DO NOTHING`. Scheduler runs `daily_ingest` at 17:30 weekdays. `POST /api/runs/daily-ingest` triggers manual full refresh.

## Gotchas

- **Alembic**: `alembic/versions/` was empty on init — autogenerate requires a live DB connection. Hand-write the first migration if the DB is blank.
- **SQLAlchemy**: Stack is fully **sync** (`create_engine`, `sessionmaker`). Do not introduce async.
- **DB session injection**: Services take `db: Session` in `__init__`. Routers use `Depends(get_db)` from `api/deps.py` and construct services per-request (no module-level singletons).
- **`DailyBar.code` vs `etf_code`**: The schema field is `code` (not `etf_code`). Map `EtfDailyBarModel.etf_code → DailyBar(code=...)`.
- **AkShareFundClient share snapshot**: Uses `fund_etf_spot_em` with a 10-minute in-process cache; when `shares_total` is missing, falls back to `AUM / price`. Column names may vary across AkShare versions.
- **Sync blocking in uvicorn**: Services use synchronous `urlopen` for external APIs. FastAPI runs sync routes in a thread pool (default 40 threads). Concurrent cold-start requests can exhaust the pool and cause timeouts — use a per-resource `threading.Lock` to serialize first-fetch, then read from DB on subsequent requests.
- **ECharts + TypeScript**: `echarts/index.d.ts` triggers TS1203 with `vue-tsc`. Fix: add `"skipLibCheck": true` to `apps/web/tsconfig.json`.
- **Backend venv on Windows**: Executables are at `apps/api/.venv/Scripts/` (e.g. `.venv/Scripts/alembic`, `.venv/Scripts/python`).
- **`strategy_plugin` table is always empty** — strategies are managed by the in-memory `StrategyRegistry`. Never add a FK referencing `strategy_plugin`; it will silently reject all inserts due to FK violation.
- **Backtest `context.extra["etf_bars"]`**: `BacktestService` injects real historical bar data here before calling `plugin.run_for_universe()`. Plugins check this key first and fall back to stubs when absent (live runs).
- **AkShare index valuation**: Only 沪深300(000300), 上证50(000016), 中证500(000905) return PE/PB from legulegu.com. Other indexes (000688/399001/399006) return empty — must handle gracefully in frontend.
- **Backend GET endpoints never return 500**: External API failures are caught/logged, returning `[]`. A 200 OK with empty array can mean either "no data yet" or "upstream error".
- **AkShare API instability**: Upstream network errors (ConnectionResetError, AttributeError) are common. Tests use `_retry_fetch()` with 3 attempts. Frontend pages catch errors silently and show "暂无数据".
- **PostgreSQL NULL uniqueness in `etf_factor_value`**: `NULL != NULL` means `(trade_date, etf_code, factor_id, strategy_id=NULL)` won't prevent duplicates via the composite unique constraint. Solved by partial unique index `uq_etf_factor_value_builtin` on `(trade_date, etf_code, factor_id) WHERE strategy_id IS NULL` (migration 0005). SQLAlchemy upsert uses `index_where=EtfFactorValueModel.strategy_id.is_(None)` to reference it.
- **`main.py` circular import via `factor_registry`**: `api/deps.py::get_factor_registry()` and `infra/scheduler/__init__.py` both import `factor_registry` from `main.py` using deferred `from quant_etf_api.main import factor_registry` inside the function body — never at module level, or a circular import will occur.
- **`FactorRow` (schemas/signal.py) is reused for factor API responses** — no separate factor value schema exists. `schemas/factor.py` only defines `FactorSpecResponse`.
- **`factor_definition.owner_plugin` is nullable** (migration 0005): independent built-in factors use `owner_plugin=NULL, strategy_id=NULL`. Plugins still set `owner_plugin` to their `strategy_id`.
- **`from __future__ import annotations` + `dict[str, Any]` requires explicit `from typing import Any`**: When a file has `from __future__ import annotations`, ruff (F821) treats `Any` as undefined even though it's only used in stringified type hints. Always add `from typing import Any` alongside the future import when using `dict[str, Any]` or similar generic types.
- **Ruff on Windows**: Installed at `.venv/Scripts/ruff.exe` (inside the project venv, not globally). Use `.venv/Scripts/ruff.exe check .` from `apps/api`.

## Coding Standards

**Before generating or modifying any code, read [`docs/CODING_STANDARDS.md`](docs/CODING_STANDARDS.md).**

Key rules (details in the doc):
- All comments and docstrings must be in **Chinese**
- Every Python class/function/method must have a Chinese Google-style docstring
- Every TypeScript function must have a Chinese JSDoc comment
- When refactoring, **update** existing comments — never delete them
- No `any` types in TypeScript; use semantic HTTP status codes in routers
