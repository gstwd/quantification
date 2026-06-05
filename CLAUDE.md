# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quant ETF Asset Allocation System — an asset allocation decision system for A-share ETFs (daily frequency only, no individual stocks, no trading execution). Full-stack: FastAPI backend + PostgreSQL + Vue 3 frontend. Supports dual-mode backtesting: signal scoring mode and asset allocation mode (timing → rotation → position sizing).

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
pytest                           # run all tests (123 unit tests: services, factors, plugins, domain, allocation)
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

### CLI commands

```bash
cd apps/api
python -m quant_etf_api.cli init-factors   # 将代码中的因子元数据同步到数据库
python -m quant_etf_api.cli init-indexes   # 将默认指数种子数据同步到数据库
```

### Factor definition sync

因子定义以数据库为唯一 source of truth。首次部署或添加新因子后，需要手动同步：

```bash
# 方式1：CLI 命令
python -m quant_etf_api.cli init-factors

# 方式2：API 端点
curl -X POST http://localhost:8000/api/factors/init
```

同步策略：
- 代码中有、DB 中没有 → INSERT（新因子）
- 代码和 DB 都有 → 仅更新 version、required_data（代码管控字段）
- DB 中有、代码中没有 → 设为 is_active=False（保留历史数据关联）

### Index seed data sync

首次部署或数据库重建后，需要同步默认指数种子数据（legulegu 估值源支持的 12 个指数）：

```bash
python -m quant_etf_api.cli init-indexes
```

幂等操作，已存在的指数自动跳过。同步后 `daily-ingest` 会自动拉取这些指数的日线和估值数据。

## Architecture

### Backend layers

```
HTTP → api/routers/ → services/ → infra/ → PostgreSQL
                          ↓           ↑
                      plugins/ ← domain/ (pure business rules)
                          ↓
                      factors/ (single-factor computation)
```

- **`api/routers/`** — 9 route groups: `health`, `system`, `etfs`, `market_data`, `strategies`, `signals`, `factors`, `runs`, `backtests`
- **`services/`** — Business logic; `IngestService` uses read-through cache (DB → lock → external API → upsert)
- **`infra/db/`** — SQLAlchemy 2 ORM models (`infra/db/models/core.py` has all 18 tables) + 7 repository classes (`infra/db/repositories/`). Repositories own all DB queries; services delegate to them for read operations, own only write logic.
- **`infra/clients/`** — 4 data source clients, all inherit from `base.py`:
  - `akshare_fund.py` (ETF K-line via Sina + shares/AUM via fund_etf_spot_em), `exchange_reference.py` (exchange ref)
  - `akshare_index.py` (index daily + PE/PB valuation), `akshare_macro.py` (CPI/PMI/LPR)
- **`infra/scheduler/`** — `DailyIngestScheduler`: daemon `Thread` + `Event` loop, runs at `settings.schedule_time` (default 17:30), skips weekends
- **`domain/`** — Pure domain logic (no SQLAlchemy/FastAPI imports). Three sub-layers with clear dependency direction: `domain ← factors ← plugins`. Factor layer computes raw values + IC/IR evaluation; strategy layer handles normalization, weighting, and signal generation:
  - `common/` — `bar_metrics.py` (BAR computation), `enums.py` (SignalLevel, RunStatus, etc.), `values.py` (DateRange)
  - `strategies/` — `models.py` (StrategyContextData, StrategyResult, TimingSignal, AssetRanking, AllocationPlan dataclasses)
- **`factors/`** — Single-factor computation layer: `base.py` (FactorSpec/FactorContext/FactorValue/FactorComputer Protocol), `registry.py` (FactorRegistry), `service.py` (FactorService orchestrates computation + persistence), `evaluation.py` (IC/IR analysis + factor correlation matrix), `builtins/` (6 built-in computers: volume, momentum×3, volatility, valuation×2). **所有因子基于指数数据计算**（`index_factor_value` 表）。Depends on `domain/common/` for calculations.
- **`plugins/`** — Strategy plugin layer: `base.py` (StrategyPlugin Protocol, re-exports domain models), `registry.py` (StrategyRegistry), `builtins/` (2 strategy plugins). Each plugin implements `StrategyPlugin` Protocol (structural subtyping — no inheritance required). The Protocol includes 3 optional decision pipeline methods (`assess_market_timing`, `rank_assets`, `allocate_positions`) for asset allocation mode.
- **`config/`** — Pydantic settings loaded from `.env`

### Database schema (20 tables, migrations 0001–0007)

| Group | Tables |
|---|---|
| Reference | `etf_universe`, `benchmark_index` |
| Market data | `etf_daily_bar`, `index_daily_bar`, `etf_daily_share`, `index_valuation`, `macro_indicator`, `source_payload_log` |
| Analytics | `factor_definition`, `etf_factor_value`, `index_factor_value`, `signal_definition`, `etf_signal` |
| Runtime | `strategy_plugin`, `research_run`, `research_run_item` |
| Backtest | `backtest_run`, `backtest_daily_result`, `backtest_etf_result`, `backtest_index_result` |

### Plugin system

Plugins implement the `StrategyPlugin` Protocol (structural subtyping — no inheritance required). Required interface:

- **Metadata attributes:** `strategy_id`, `display_name`, `version`, `frequency`, `asset_scope`, `description`
- **Methods:** `parameter_schema()`, `required_inputs()`, `factor_definitions()`, `signal_definition()`, `prepare_context()`, `run_for_universe()`, `explain_result()`
- **Optional decision pipeline methods** (checked via `hasattr`): `assess_market_timing()`, `rank_assets()`, `allocate_positions()`

Built-in plugins in `plugins/builtins/`:
1. **`volume_breakout_daily`** — Volume breakout baseline（指数模式）
2. **`etf_allocation`** — Asset allocation strategy with full decision pipeline: timing assessment (valuation 40% + trend 40% + volume 20%) → asset rotation ranking (momentum 60% + valuation 40%) → position sizing (regime-based exposure: offensive 80%, neutral 50%, defensive 20%)（指数模式）

To add a strategy: create a plugin file in `plugins/builtins/`, implement the Protocol, register in `plugins/registry.py`.

### Frontend

- **Pages** (`src/pages/`): Dashboard, ETF list, ETF detail, Index list, Index detail, Macro, Strategy list, Strategy detail, Runs, Data status, Backtest list, Backtest create, Backtest detail
- **State** (`src/stores/`): 4 Pinia stores — `etfs`, `strategies`, `signals`, `backtests`; stores are for mutable shared state only
- **API layer** (`src/api/`): Axios wrappers returning typed `PaginatedResponse<T>` (`{ items, total, offset, limit }`); `etfs.ts`, `strategies.ts`, `signals.ts`, `backtests.ts`, `runs.ts`, `market_data.ts`
- **Read-only data pages** (index/macro): Fetch data **inline** via `ref()` + `onMounted`, no Pinia store — lighter pattern for static data views
- Charts use ECharts 5 (dynamic `import('echarts')`, `watch` with `flush: 'post'`, `dispose()` in `onUnmounted`)

## Current State

Services fully wired to PostgreSQL. 20 tables across 7 migrations (0001→0002→0002_backtest→0003_index_macro→0004→0005_factor_layer→0007_index_factor_backtest). Each data type has exactly **one** source: ETF K-line→Sina, ETF shares→Eastmoney, Index K-line→AkShare, Index valuation→AkShare, Macro→AkShare. Read-through cache pattern: GET endpoint → check DB → lock → external API → upsert via `ON CONFLICT DO NOTHING`. Scheduler runs `daily_ingest` at 17:30 weekdays. `POST /api/runs/daily-ingest` triggers manual full refresh.

**Backtesting**: `BacktestService` 以指数模式运行（`index_factor_value` + `backtest_index_result`），收益以百分比展示。适用于所有策略（`etf_allocation`、`volume_breakout_daily`）。Backtest mode（signal/allocation）存储在 `backtest_run.params` JSON 字段。

**Asset allocation API**: `GET /strategies/{strategy_id}/allocation` runs the full decision pipeline and returns timing signal, asset rankings, and allocation plan.

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
- **Backtest `context.extra["etf_bars"]`**: `BacktestService` injects real historical bar data here before calling `plugin.run_for_universe()`. Plugins check this key first and fall back to stubs when absent (live runs). **指数模式下此 key 实际存放指数数据**（透明映射），插件无需区分。
- **`universe` 字典 key**: `_resolve_index_universe()` 返回的字典同时包含 `etf_code` 和 `index_code`（值相同），以便插件层通过 `item["etf_code"]` 透明兼容。
- **AkShare index valuation**: Only 沪深300(000300), 上证50(000016), 中证500(000905) return PE/PB from legulegu.com. Other indexes (000688/399001/399006) return empty — must handle gracefully in frontend.
- **Backend GET endpoints never return 500**: External API failures are caught/logged, returning `[]`. A 200 OK with empty array can mean either "no data yet" or "upstream error".
- **AkShare API instability**: Upstream network errors (ConnectionResetError, AttributeError) are common. Tests use `_retry_fetch()` with 3 attempts. Frontend pages catch errors silently and show "暂无数据".
- **PostgreSQL NULL uniqueness in `etf_factor_value`**: `NULL != NULL` means `(trade_date, etf_code, factor_id, strategy_id=NULL)` won't prevent duplicates via the composite unique constraint. Solved by partial unique index `uq_etf_factor_value_builtin` on `(trade_date, etf_code, factor_id) WHERE strategy_id IS NULL` (migration 0005). SQLAlchemy upsert uses `index_where=EtfFactorValueModel.strategy_id.is_(None)` to reference it.
- **`main.py` circular import via `factor_registry`**: `api/deps.py::get_factor_registry()` and `infra/scheduler/__init__.py` both import `factor_registry` from `main.py` using deferred `from quant_etf_api.main import factor_registry` inside the function body — never at module level, or a circular import will occur.
- **`FactorRow` (schemas/signal.py) is reused for factor API responses** — no separate factor value schema exists. `schemas/factor.py` only defines `FactorSpecResponse`.
- **`factor_definition.owner_plugin` is nullable** (migration 0005): independent built-in factors use `owner_plugin=NULL, strategy_id=NULL`. Plugins still set `owner_plugin` to their `strategy_id`.
- **`from __future__ import annotations` + `dict[str, Any]` requires explicit `from typing import Any`**: When a file has `from __future__ import annotations`, ruff (F821) treats `Any` as undefined even though it's only used in stringified type hints. Always add `from typing import Any` alongside the future import when using `dict[str, Any]` or similar generic types.
- **Ruff on Windows**: Installed at `.venv/Scripts/ruff.exe` (inside the project venv, not globally). Use `.venv/Scripts/ruff.exe check .` from `apps/api`.
- **Backtest mode storage**: `backtest_mode` ("signal" | "allocation") is stored in `backtest_run.params` JSON field, not a separate column. Access via `params.get("backtest_mode", "signal")`.
- **Optional plugin methods**: Decision pipeline methods (`assess_market_timing`, `rank_assets`, `allocate_positions`) are optional on `StrategyPlugin`. Use `hasattr(plugin, "assess_market_timing")` to check support. `StrategyRegistry.has_decision_pipeline()` wraps this check.

## Coding Standards

**Before generating or modifying any code, read [`docs/CODING_STANDARDS.md`](docs/CODING_STANDARDS.md).**

Key rules (details in the doc):
- All comments and docstrings must be in **Chinese**
- Every Python class/function/method must have a Chinese Google-style docstring
- Every TypeScript function must have a Chinese JSDoc comment
- When refactoring, **update** existing comments — never delete them
- No `any` types in TypeScript; use semantic HTTP status codes in routers
