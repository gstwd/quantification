# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quant ETF Asset Allocation System — an asset allocation decision system for A-share ETFs (daily frequency only, no individual stocks, no trading execution). Full-stack: FastAPI backend + PostgreSQL + Vue 3 frontend. Uses a **component-based, configuration-driven** strategy engine: new strategies are created via JSON config stored in the database, no Python code needed.

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
pytest                           # run all tests
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
npx vue-tsc --noEmit            # TypeScript type check
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
HTTP → api/routers/ → services/ → engine/ (strategy execution pipeline)
                       ↓    ↑
                   infra/  domain/ (pure business rules)
                       ↓
                   factors/ (single-factor computation)
```

- **`api/routers/`** — 10 route groups: `health`, `system`, `etfs`, `indexes`, `market_data`, `strategies`, `signals`, `factors`, `runs`, `backtests`
- **`api/middleware.py`** — `RequestIdMiddleware`：为每个请求注入唯一 request_id，写入响应头和日志 ContextVar
- **`services/`** — Business logic; `IngestService` uses read-through cache (DB → lock → external API → upsert). `ContextBuilder` shim re-exports from `engine/context_builder.py`. New services: `metrics.py`（专业绩效指标，含 VaR/CVaR/连续亏损天数）、`benchmark.py`（基准收益计算）、`index_service.py`、`universe_service.py`、`data_quality.py`（日线/估值异常检测 + 连续性缺口检测）.
- **`engine/`** — **策略引擎核心**：组件化、配置驱动的策略执行管线（11 个文件）：
  - `config.py` — Pydantic 配置模型（含 `TimingConfig`、`ScoreConfig`、`FilterConfig`、`RankConfig`、`PortfolioConfig`、`RiskConfig`、`RebalanceConfig`）
  - `base.py` — `EngineContext`、`EngineResult` 数据结构
  - `score.py` — `ScoreCalculator` Protocol + `DefaultScoreCalculator`（含 `_TRANSFORM_REGISTRY`）
  - `filter.py` — `FilterEngine` Protocol + `DefaultFilterEngine`
  - `rank.py` — `RankEngine` Protocol + `DefaultRankEngine`
  - `portfolio.py` — `WeightAllocator` Protocol + `EqualWeight`/`ScoreWeight`
  - `risk.py` — `RiskManager` Protocol + `DefaultRiskManager`
  - `rebalance.py` — `RebalanceScheduler` Protocol + `DefaultRebalanceScheduler`
  - `orchestrator.py` — `StrategyEngine` 编排器（管线入口）
  - `factor_provider.py` — `FactorProvider`：桥接因子层与引擎层，实时模式从 DB 加载预计算因子，回测模式批量预计算
  - `context_builder.py` — `ContextBuilder`：统一的引擎上下文构建器，同时支持实时和回测两种模式
- **`infra/db/`** — SQLAlchemy 2 ORM models (`infra/db/models/core.py` has 22 tables) + 11 repository files (`infra/db/repositories/`). Repositories own all DB queries; services delegate to them for read operations, own only write logic.
- **`infra/clients/`** — 4 data source clients, all inherit from `base.py`:
  - `akshare_fund.py` (ETF K-line via Sina + shares/AUM via fund_etf_spot_em), `exchange_reference.py` (exchange ref)
  - `akshare_index.py` (index daily + PE/PB valuation), `akshare_macro.py` (CPI/PMI/LPR)
  - `retry.py` — `@with_retry()` 装饰器，指数退避重试，参数可通过环境变量 `AKSHARE_RETRY_MAX_ATTEMPTS` / `AKSHARE_RETRY_BASE_DELAY` 配置
- **`infra/trading_calendar.py`** — `TradingCalendar` 类，通过 `akshare.tool_trade_date_hist_sina()` 获取 A 股交易日历，内存缓存 TTL=1 天，API 不可用时降级为周末判断
- **`infra/scheduler/`** — `DailyIngestScheduler`: daemon `Thread` + `Event` loop, runs at `settings.schedule_time` (default 17:30), skips weekends. 调度器同步执行，不走线程池
- **`api/executor.py`** — 共享后台任务线程池。所有 bg 路由（runs、backtests）通过 `get_bg_executor()` 获取统一 executor，`main.py` lifespan 统一 shutdown。所有 bg 函数统一 `mark_running` → `mark_success/failed` 状态流转，外层 try/except 兜底。
- **`domain/`** — Pure domain logic (no SQLAlchemy/FastAPI imports):
  - `common/` — `bar_metrics.py` (BAR computation), `enums.py` (SignalLevel, RunStatus, RunType, FactorCategory, BacktestStatus), `values.py` (DateRange), `constants.py`（信号等级阈值和标签常量）
  - `strategies/` — `models.py` (StrategyContextData, StrategyResult, TimingSignal, AssetRanking, AllocationPlan dataclasses)
  - `etf/`、`market_data/`、`research/` — 预留包目录
- **`factors/`** — Single-factor computation layer: `base.py` (FactorSpec/FactorContext/FactorValue/FactorComputer Protocol), `registry.py` (FactorRegistry), `service.py` (FactorService orchestrates computation + persistence), `evaluation.py` (IC/IR analysis + factor correlation matrix), `normalization.py` (zscore/rank/minmax/winsorize/MAD 横截面标准化), `builtins/` (18 built-in computers: volume×1, momentum×3, volatility×1, valuation×2, ma×4, atr×1, donchian×2, rsi×1). **所有因子基于指数数据计算**（`index_factor_value` 表）。**架构原则：因子层只使用指数数据，不使用 ETF 特有数据（份额/AUM/折溢价等）**。
- **`plugins/`** — 已废弃，仅保留 re-export 和空壳 `StrategyRegistry`。策略执行已迁移至 `engine/`。
- **`config/`** — Pydantic settings loaded from `.env`
- **`schemas/`** — 11 个 Pydantic schema 文件：`etf.py`、`factor.py`、`market_data.py`、`pagination.py`、`run.py`、`signal.py`、`strategy.py`、`system.py`、`types.py`、`backtest.py`、`__init__.py`

### Strategy Engine（策略引擎）

策略执行管线：`StrategyEngine.run(config, context)`

```
[可选] Timing  → Score → [可选] Filter → Rank → [可选] Portfolio → [可选] Risk → Output
```

- **Timing**: 市场择时，综合估值/趋势/量能判断 regime（offensive/neutral/defensive）
- **Score**: 每资产综合得分 = Σ(transform(factor_value) × weight) / Σ(|weight|)。`scoring_mode` 支持：absolute（每资产独立评分，默认）/ rank（横截面排名分）/ zscore（横截面标准化）。非 absolute 模式时自动使用 `CrossSectionScorer`
- **Filter**: 过滤规则（gt/lt/gte/lte/eq/neq/between），AND/OR 逻辑
- **Rank**: 排序 + TopN/BottomN
- **Portfolio**: 权重分配（equal_weight / score_weight / winner_take_all），择时 regime 控制总仓位。`default_exposure` 控制无择时时的默认仓位（替代硬编码 0.50）
- **Risk**: 单资产上限、组合上限、最低现金比例
- **Rebalance**: 调仓频率控制（daily/weekly/monthly），回测中非调仓日沿用上次持仓

有 `portfolio` 配置 → 输出仓位（回测要求策略必须配置 portfolio 模块）。

内置 transform 函数（在 `engine/score.py` 的 `_TRANSFORM_REGISTRY` 中注册）：`invert_percentile`、`momentum_score`、`volume_score`、`trend_score`、`clamp_0_100`。

新建策略只需 JSON 配置，通过 `POST /strategies` 创建，存储在 `strategy_config` 表。

### FactorProvider（因子供应器）

`engine/factor_provider.py` 桥接因子计算层与策略引擎层：

- **实时模式**：`load_asset_factors()` / `load_market_factors()` 从 `index_factor_value` 表加载预计算因子值
- **回测模式**：`precompute_backtest_factors()` 利用预加载的 K 线数据，通过 `FactorComputer` 批量计算所有因子，避免逐日查库
- `collect_required_factor_ids()` 从 `StrategyConfig` 自动推导所有需要的因子 ID（遍历 timing、score、filters）

### ContextBuilder（上下文构建器）

`engine/context_builder.py` 提供统一的 `build()` 方法，同时支持实时和回测两种模式：

- **实时模式**：从 DB 加载全量指数数据、K 线（90 天回望）、估值、因子值
- **回测模式**：使用预加载数据和预计算因子值构建上下文
- 通过 `FactorProvider` 加载因子值，消除硬编码因子计算
- `services/context_builder.py` 是向后兼容 shim，re-export 自 engine 版本

### Database schema (24 tables, migrations 0001–0016)

| Group | Tables |
|---|---|
| Reference | `etf_universe`, `benchmark_index` |
| Market data | `etf_daily_bar`, `index_daily_bar`, `etf_daily_share`, `index_valuation`, `macro_indicator`, `source_payload_log` |
| Analytics | `factor_definition`, `etf_factor_value`, `index_factor_value`, `signal_definition`, `etf_signal`, `index_signal` |
| Runtime | `strategy_plugin`, `research_run`, `research_run_item` |
| Backtest | `backtest_run`（含 progress 列）, `backtest_daily_result`, `backtest_etf_result`, `backtest_index_result` |
| Strategy | `strategy_config` |

Key migrations:
- 0001–0004: 基础表结构、回测表、指数/宏观表、ETF 种子数据
- 0005–0006: 因子层表、因子定义增强
- 0007–0008: 指数因子回测、策略配置表
- 0009–0012: 回测模式字段、`index_signal` 表、回测日基准收益和换手率、回测指数原始得分
- 0013: `trading_calendar` 表、`benchmark_index` 增加 `is_active`/`delisting_date`、`macro_indicator` 增加 `period_date`
- 0016: `backtest_run` 增加 `progress` 列（回测执行进度 0-100）

### Frontend

- **Pages** (`src/pages/`): 14 pages — Dashboard, ETF list, ETF detail, Index list, Index detail, Macro, Strategy list, Strategy detail (config viewer + editor), Factors list, Factor detail, Runs, Backtest list, Backtest create, Backtest detail
- **State** (`src/stores/`): 4 Pinia stores — `etfs`, `strategies`, `signals`, `backtests`; stores are for mutable shared state only
- **API layer** (`src/api/`): 8 files — `client.ts` (Axios 实例) + 7 API wrapper modules (`etfs.ts`, `strategies.ts`, `signals.ts`, `backtests.ts`, `runs.ts`, `market_data.ts`, `factors.ts`); all return typed `PaginatedResponse<T>` (`{ items, total, offset, limit }`)
- **Read-only data pages** (index/macro): Fetch data **inline** via `ref()` + `onMounted`, no Pinia store — lighter pattern for static data views
- Charts use ECharts 5 (dynamic `import('echarts')`, `watch` with `flush: 'post'`, `dispose()` in `onUnmounted`)

## Current State

Services fully wired to PostgreSQL. 23 tables across 13 migrations (0001→0013). Each data type has exactly **one** source: ETF K-line→Sina, ETF shares→Eastmoney, Index K-line→AkShare, Index valuation→AkShare, Macro→AkShare. Read-through cache pattern: GET endpoint → check DB → lock → external API → upsert via `ON CONFLICT DO NOTHING`. Scheduler runs `daily_ingest` at 17:30 weekdays. `POST /api/runs/daily-ingest` triggers manual full refresh. Startup health check triggers `startup_fill` to backfill data gaps.

**Strategy Engine**: `engine/` 包实现组件化策略执行管线。策略通过 `strategy_config` 表的 JSON 配置驱动，`StrategyConfigService` 管理 CRUD，`StrategyEngine` 执行管线。`FactorProvider` 桥接因子层与引擎层，`ContextBuilder` 统一构建实时和回测上下文。`BacktestService` 和 `StrategyExecutionService` 统一使用引擎执行。

**因子系统**: 18 个内置因子（7 基础 + 4 均线 ma_5d/10d/20d/60d + atr_14d + donchian_20d_high/low + rsi_14d），通过 `FactorRegistry` 注册，`FactorService` 编排计算和持久化。所有因子基于指数数据计算（`index_factor_value` 表）。`FactorSpec` 增加 `lookback_days` 字段，`FactorService._load_context()` 动态使用所有因子的最大 lookback。`FactorContext` 增加 `macro_indicators` 字段。`normalization.py` 提供 zscore/rank/minmax/winsorize/MAD 横截面标准化。`evaluation.py` 提供 IC/IR 分析和因子相关性矩阵。

**Backtesting**: `BacktestService` 使用统一 `_run_backtest_loop`。集成 `FactorProvider` 预计算因子、`ContextBuilder` 构建上下文、专业绩效指标（`metrics.py`）、基准对比（`benchmark.py`）、交易成本模型（佣金+滑点）。支持调仓频率控制和换手率计算。回测仅支持配置模式（策略需配置 portfolio 模块）。

**Asset allocation API**: `GET /strategies/{strategy_id}/allocation` runs the full decision pipeline and returns timing signal, asset rankings, and allocation plan.

**Strategy config API**:
- `GET /strategies` — 列表
- `GET /strategies/{id}` — 详情
- `POST /strategies` — 创建配置
- `PUT /strategies/{id}` — 更新配置
- `DELETE /strategies/{id}` — 删除配置
- `POST /strategies/validate` — 校验配置

## Gotchas

- **Alembic**: `alembic/versions/` was empty on init — autogenerate requires a live DB connection. Hand-write the first migration if the DB is blank.
- **SQLAlchemy**: Stack is fully **sync** (`create_engine`, `sessionmaker`). Do not introduce async.
- **DB session injection**: Services take `db: Session` in `__init__`. Routers use `Depends(get_db)` from `api/deps.py` and construct services per-request (no module-level singletons).
- **`DailyBar.code` vs `etf_code`**: The schema field is `code` (not `etf_code`). Map `EtfDailyBarModel.etf_code → DailyBar(code=...)`.
- **AkShareFundClient share snapshot**: Uses `fund_etf_spot_em` with a 10-minute in-process cache; when `shares_total` is missing, falls back to `AUM / price`. Column names may vary across AkShare versions.
- **Sync blocking in uvicorn**: Services use synchronous `urlopen` for external APIs. FastAPI runs sync routes in a thread pool (default 40 threads). Concurrent cold-start requests can exhaust the pool and cause timeouts — use a per-resource `threading.Lock` to serialize first-fetch, then read from DB on subsequent requests.
- **ECharts + TypeScript**: `echarts/index.d.ts` triggers TS1203 with `vue-tsc`. Fix: add `"skipLibCheck": true` to `apps/web/tsconfig.json`.
- **Backend venv on Windows**: Executables are at `apps/api/.venv/Scripts/` (e.g. `.venv/Scripts/alembic`, `.venv/Scripts/python`). Source code is at `apps/api/src/quant_etf_api/`.
- **`strategy_plugin` table is always empty** — 策略已迁移至 `strategy_config` 表。Never add a FK referencing `strategy_plugin`; it will silently reject all inserts due to FK violation.
- **`universe` 字典 key**: `_resolve_index_universe()` 返回的字典同时包含 `etf_code` 和 `index_code`（值相同），以便引擎层通过 `item["etf_code"]` 透明兼容。
- **AkShare index valuation**: Only 沪深300(000300), 上证50(000016), 中证500(000905) return PE/PB from legulegu.com. Other indexes (000688/399001/399006) return empty — must handle gracefully in frontend.
- **Backend GET endpoints never return 500**: External API failures are caught/logged, returning `[]`. A 200 OK with empty array can mean either "no data yet" or "upstream error".
- **AkShare API instability**: Upstream network errors (ConnectionResetError, AttributeError) are common. Tests use `_retry_fetch()` with 3 attempts. Frontend pages catch errors silently and show "暂无数据".
- **PostgreSQL NULL uniqueness in `etf_factor_value`**: `NULL != NULL` means `(trade_date, etf_code, factor_id, strategy_id=NULL)` won't prevent duplicates via the composite unique constraint. Solved by partial unique index `uq_etf_factor_value_builtin` on `(trade_date, etf_code, factor_id) WHERE strategy_id IS NULL` (migration 0005). SQLAlchemy upsert uses `index_where=EtfFactorValueModel.strategy_id.is_(None)` to reference it.
- **`main.py` circular import via `factor_registry`**: `api/deps.py::get_factor_registry()` and `infra/scheduler/__init__.py` both import `factor_registry` from `main.py` using deferred `from quant_etf_api.main import factor_registry` inside the function body — never at module level, or a circular import will occur.
- **`FactorRow` (schemas/signal.py) is reused for factor API responses** — no separate factor value schema exists. `schemas/factor.py` only defines `FactorSpecResponse`.
- **`factor_definition.owner_plugin` is nullable** (migration 0005): independent built-in factors use `owner_plugin=NULL, strategy_id=NULL`.
- **`from __future__ import annotations` + `dict[str, Any]` requires explicit `from typing import Any`**: When a file has `from __future__ import annotations`, ruff (F821) treats `Any` as undefined even though it's only used in stringified type hints. Always add `from typing import Any` alongside the future import when using `dict[str, Any]` or similar generic types.
- **Ruff on Windows**: Installed at `.venv/Scripts/ruff.exe` (inside the project venv, not globally). Use `.venv/Scripts/ruff.exe check .` from `apps/api`.
- **Engine transform 函数**: 内置变换函数在 `engine/score.py` 的 `_TRANSFORM_REGISTRY` 中注册。新增 transform 只需在该注册表中添加。
- **EngineContext vs StrategyContextData**: `EngineContext` 使用结构化字段（`asset_factors`、`market_factors`），`StrategyContextData` 保留用于旧接口兼容（`plugins/base.py` re-export）。
- **FactorProvider 依赖注入**: `FactorProvider` 需要 `db: Session`（实时模式）和 `registry: FactorRegistry`（回测模式）。回测服务在 `__init__` 中构建 `FactorRegistry` 和 `FactorProvider`，通过 `ContextBuilder` 注入。
- **回测仅支持配置模式**: 策略必须配置 `portfolio` 模块，`create_backtest` 会校验并拒绝无 portfolio 的策略。`backtest_mode` 和 `weighting` 字段已移除。
- **回测日收益基准（benchmark_return）和换手率（turnover）**: 存储在 `backtest_daily_result` 表中（migration 0011），前端 `BacktestDailyResult` 接口包含这两个可选字段。
- **index_signal 表** (migration 0010): 与 `etf_signal` 结构对齐，但以 `index_code` 替代 `etf_code`，用于存储基于指数的策略信号。
- **信号等级判定常量**: 定义在 `domain/common/constants.py`（`SIGNAL_THRESHOLD_HIGH=70`、`SIGNAL_THRESHOLD_MID=50`），引擎和回测服务统一引用，避免硬编码散落。
- **`backtest_index_result.original_score`** (migration 0012): 配置模式下保留原始综合得分，避免被权重值覆盖，便于分析策略评分与仓位的对应关系。
- **FilterRule.compare_to**: 过滤器支持跨因子比较（如 `ma_5d > ma_20d`）。`compare_to` 与 `value` 二选一，不能同时设置。`between` 操作符不支持 `compare_to`。
- **FactorProvider.collect_required_factor_ids() 必须收集 compare_to**: 遍历 filter rules 时不仅要收集 `rule.factor`，还要收集 `rule.compare_to`（若存在）。遗漏会导致被比较的因子值未加载，filter 始终失败 → 空仓。
- **FilterRuleValue 前端接口**: 定义在 `StrategyConfigForm.vue`（非共享 types 文件）。修改 FilterRule schema 时需同步更新：接口定义、表单模板、`initFilter()`、`buildConfig()`、校验逻辑，以及 `StrategyDetailPage.vue` 的只读展示。
- **后台任务状态流转**: `research_run` 状态链：pending → running → success/failed。`RunService.mark_running()` 在 bg 函数开始时调用，`mark_success(run_id, metrics)` / `mark_failed(run_id, error_message)` 在结束时调用。进程重启后 `recover_stuck_runs_on_startup()` 自动恢复卡死任务。
- **数据刷新按类型拆分**: `IngestService` 提供 `refresh_etf_data()`、`refresh_index_data()`、`refresh_macro_data()` 三个公共方法，各有独立 run 生命周期。对应 API 端点：`POST /runs/etf-refresh`、`/runs/index-refresh`、`/runs/macro-refresh`。各数据页面（ETF/指数/宏观）有自己的"刷新数据"按钮，RunsPage 纯做监控。
- **Run detail API**: `GET /runs/{run_id}` 返回 `ResearchRunDetail`（含 metrics、duration_seconds），`GET /runs/{run_id}/items` 返回 `ResearchRunItemSchema` 逐条明细，`POST /runs/{run_id}/retry` 重试失败任务（创建新 run 并提交到线程池）。

## Coding Standards

**Before generating or modifying any code, read [`docs/CODING_STANDARDS.md`](docs/CODING_STANDARDS.md).**

Key rules (details in the doc):
- All comments and docstrings must be in **Chinese**
- Every Python class/function/method must have a Chinese Google-style docstring
- Every TypeScript function must have a Chinese JSDoc comment
- When refactoring, **update** existing comments — never delete them
- No `any` types in TypeScript; use semantic HTTP status codes in routers
- **FactorSpec.lookback_days**: 新增因子时必须设置合理的 `lookback_days`（自然日），`FactorService._load_context()` 取所有因子的最大值。参考：5d→15, 20d→40, 60d→90, 估值百分位→730（2年），技术指标→period×1.5+5。
- **volume_ratio_20d 返回值变更**: 数据不足时返回 `None`（原为 1.0），区分"无数据"与"量比恰好为 1"。`calc_volume_ratio_20d()` 返回 `float | None`，`calc_5d_return()` 仍返回 `float`（默认 0.0）。
- **BenchmarkIndexModel.is_active**: `ContextBuilder._build_live()` 和 `BacktestService._resolve_index_universe()` 只查询 `is_active=True` 的指数。新增指数默认 `is_active=True`。
- **TradingCalendar 缓存**: 首次调用时从 AkShare 加载（`tool_trade_date_hist_sina()`），TTL=1 天。`ingest_service.run_daily_ingest` 和 `check_data_freshness` 已接入，不再用 `weekday()>=5`。
- **rebalance.py 交易日历对齐**: `DefaultRebalanceScheduler` 接受 `TradingCalendar` 实例，周度/月度调仓如遇非交易日自动顺延至下一交易日。
- **StrategyConfig.index_codes**: 存储在 `config_json` 内部（非独立 DB 列），通过 `**row.config_json` 展开到 engine 的 `StrategyConfig` 模型。前端 API 请求中 `index_codes` 应在 `config_json` 内传递，非顶层字段。非空时 `_filter_by_scope()` 仅保留指定指数（实时和回测模式均生效）。
- **index_codes 回测强制应用**: `BacktestService.create_backtest()` 检查策略的 `config.index_codes`，非空时强制覆盖 `universe_filter` 为 subset 模式；`ContextBuilder._build_backtest()` 对传入的 index_codes 做交集过滤（双重保护）。
- **StrategyConfigForm 与 engine/config.py 的 StrategyConfig 同步**: 引擎新增配置模块时，需同步更新 `StrategyConfigForm.vue`（表单）、`StrategyDetailPage.vue`（详情展示）。目前已覆盖全部 7 个模块（score/timing/filters/rank/portfolio/risk/rebalance）+ 资产范围 index_codes。
- **`StrategySummary` 已有 `index_codes` 顶层字段**: 列表 API 直接返回 `index_codes`，前端无需额外调用 `fetchStrategyDetail()`。`StrategyConfigForm.vue` 读取 `modelValue.index_codes`（config_json 内），`StrategyDetailPage.vue` 和 `BacktestCreatePage.vue` 读取顶层 `store.current?.index_codes`。
- **index_daily_bar OHLC 字段**: `IndexDailyBarModel` 有 `open_price`、`high_price`、`low_price`、`close_price` 字段，技术指标因子（ATR/Donchian）通过 `ctx.index_bars` 直接访问。
- **`get_default_factor_registry()` vs `build_default_factor_registry()`**: 进程级单例通过 `get_default_factor_registry()` 获取（首次构建后缓存），避免 `BacktestService` 每请求重建。只有 `cli.py` 和 `registry.py` 内部使用 `build_default_factor_registry()`。
- **`BatchFactorComputer` Protocol**: 定义在 `factors/base.py`，回测因子预计算时优先调用 `compute_batch()`（一次遍历 bar 数据覆盖所有日期）。已在 momentum.py（return_5d/20d/60d/120d）实现。新增回测频繁使用的因子时建议实现此协议。
- **`validate_config` 是 `@staticmethod`**: `StrategyConfigService.validate_config()` 不依赖 DB 会话，直接静态调用无需实例化服务。
