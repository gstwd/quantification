# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Quant Index Asset Allocation System — an asset allocation decision system for A-share indexes (daily frequency only, no individual stocks, no ETF, no trading execution). Full-stack: FastAPI backend + PostgreSQL + Vue 3 frontend. Uses a **component-based, configuration-driven** strategy engine: new strategies are created via JSON config stored in the database, no Python code needed.

> **系统范围：本系统只研究 A 股指数，不研究 ETF。** 代码库中不含任何 ETF 数据源、数据表、接口或前端页面（`quant_etf_api` 等包名/环境变量为历史命名保留）。

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

数据库迁移会初始化基础指数；后续指数通过页面或 API 添加：

```bash
```


## Architecture

### Backend layers

```
HTTP → api/routers/ → services/ → engine/ (strategy execution pipeline)
                       ↓    ↑
                   infra/  domain/ (pure business rules)
                       ↓
                   factors/ (single-factor computation)
```

- **`api/routers/`** — 10 route groups: `health`, `system`, `indexes`, `market_data`, `strategies`, `factors`, `runs`, `backtests`, `ai_factors`, `keyword_tags`
- **`api/middleware.py`** — `RequestIdMiddleware`：为每个请求注入唯一 request_id，写入响应头和日志 ContextVar
- **`services/`** — Business logic; `IngestService` 收敛为**抓取后端**：只做外部数据拉取、字段归一化、幂等写入与读穿透缓存（GET 未命中入队 `data_fill`），不再持有运行状态流转、进程内互斥锁与批量编排；定时/手动同步、补缺口、全量重拉、**质量快照**统一由 `DataManagementService` 编排，**数据质量只有 `data_health_snapshot` 一个口径**（经数据管理页暴露，不再有独立重算的质量接口）。`ContextBuilder` shim re-exports from `engine/context_builder.py`。其他服务包括 `index_service.py`（基准指数增删查：`list_indexes`/`add_index`/`remove_index`）。基准收益和绩效指标规则位于 `domain/`，`services/benchmark.py`、`services/metrics.py` 仅保留历史导入兼容转发。
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
- **`infra/clients/`** — 2 data source clients, all inherit from `base.py`:
  - `akshare_index.py` (index daily + PE/PB valuation), `akshare_macro.py` (CPI/PMI/LPR)
  - `retry_decorator.py` — `@with_retry()` 装饰器，指数退避重试，参数可通过环境变量 `AKSHARE_RETRY_MAX_ATTEMPTS` / `AKSHARE_RETRY_BASE_DELAY` 配置
- **`infra/trading_calendar.py`** — `TradingCalendar` 类，通过 `akshare.tool_trade_date_hist_sina()` 获取 A 股交易日历，内存缓存 TTL=1 天，API 不可用时降级为周末判断
- **`infra/scheduler/`** — `DailyIngestScheduler` / `AIAnalysisScheduler`: daemon `Thread` + `Event` 定时器，仅将任务入队（`job_key="data_sync_all"` / `ai_analysis:{date}`），不执行外部调用；**所有数据源的定时摄取统一走全局同步调度器**（指数/宏观/行业/个股均为受管数据集，无行业等子调度器）；数据摄取调度器不做交易日判断（周末/节假日也入队，由摄取侧按最近交易日缺口补拉），任务由 `background_job` worker 执行。
- **`api/executor.py`** — 共享后台任务线程池。所有 bg 路由（runs、backtests）通过 `get_bg_executor()` 获取统一 executor，`main.py` lifespan 统一 shutdown。所有 bg 函数统一 `mark_running` → `mark_success/failed` 状态流转，外层 try/except 兜底。
- **`domain/`** — Pure domain logic (no SQLAlchemy/FastAPI imports):
  - `common/` — `bar_metrics.py` (BAR computation), `numeric.py`（NaN/Inf 和价格字段容错）、`enums.py` (SignalLevel, RunStatus, RunType, FactorCategory, BacktestStatus), `values.py` (DateRange), `constants.py`（信号等级阈值和标签常量）
  - `strategies/` — `models.py` (StrategyContextData, StrategyResult, TimingSignal, AssetRanking, AllocationPlan dataclasses)
  - `research/` — 研究评估领域规则（绩效指标、walk-forward 窗口切分）
- **`factors/`** — 单因子现算层：`base.py`（FactorSpec/FactorContext/FactorValue/FactorComputer Protocol + 交易日→自然日回望折算）、`templates.py`（FactorTemplate/ParameterSpec：参数模式与规范化）、`catalog.py`（FactorTemplateRegistry：模板目录与「别名 → 模板 + 参数」解析）、`compute.py`（FactorComputeService：按模板实例从原始数据现算，不读写任何因子值表）、`evaluation.py`（IC/IR 分析 + 因子相关性矩阵）、`normalization.py`（zscore/rank/minmax/winsorize/MAD 横截面标准化）、`builtins/`（32 个计算器类，一一对应 32 个模板）。**所有因子基于指数数据计算**。**架构原则：因子层只使用指数数据**。
- **`config/`** — Pydantic settings loaded from `.env`
- **`schemas/`** — 10 个 Pydantic schema 文件：`factor.py`、`market_data.py`、`pagination.py`、`run.py`、`signal.py`、`strategy.py`、`system.py`、`types.py`、`backtest.py`、`__init__.py`

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
- **Rebalance**: 双腿调仓控制（daily/weekly/biweekly/monthly）。`selection` 腿重建成分，`risk` 腿只把现有成分等比缩放到择时目标总仓位；两腿同频（旧配置升级后的默认形态）时等价于改造前的单腿口径，非到期日沿用上次持仓

有 `portfolio` 配置 → 输出仓位（回测要求策略必须配置 portfolio 模块）。

内置 transform 函数（注册在 `engine/transforms.py` 的 `_TRANSFORM_REGISTRY`，`engine/score.py` 仅转发导入）：`invert_percentile`、`momentum_score`、`volume_score`、`trend_score`、`clamp_0_100`、`erp_score`、`drawdown_score`、`rsrs_score`。

新建策略只需 JSON 配置，通过 `POST /strategies` 创建，存储在 `strategy_config` 表。

### FactorProvider（因子供应器）

`engine/factor_provider.py` 桥接因子计算层与策略引擎层：

- **实时模式**：`load_asset_factor_matrix()` / `load_market_factors()` 按策略实际参数从原始数据现算因子值
- **回测模式**：`precompute_backtest_factors()` 利用预加载的 K 线数据，通过 `FactorComputer` 批量现算整个区间，避免逐日查库
- `collect_required_factor_ids()` 从 `StrategyConfig` 自动推导所有需要的因子引用（遍历 timing、score、filters、rank 子因子与 regime 规则）
- `resolve_instances()` 把引用解析为因子模板实例：别名按 `factor_aliases` 声明解析，未声明的引用名本身就是模板 ID（用默认参数）

### ContextBuilder（上下文构建器）

`engine/context_builder.py` 提供统一的 `build()` 方法，同时支持实时和回测两种模式：

- **实时模式**：从 DB 加载指数清单，按策略实际参数现算因子值（回望窗口由本次实例推导，出现市场级模板时额外加载全市场行情）
- **回测模式**：使用预加载行情与预计算的因子值构建上下文
- 通过 `FactorProvider` 现算因子值，消除硬编码因子计算
- `_build_live()` 只查询 `is_active=True` 的指数（实时只能用当日活跃集合）

### Database schema (24 tables, migrations 0001–0016)

| Group | Tables |
|---|---|
| Reference | `benchmark_index` |
| Market data | `index_daily_bar`, `index_valuation`, `macro_indicator`, `source_payload_log` |
| Analytics | `factor_definition`, `signal_definition`, `index_signal` |
| Runtime | `research_run`, `research_run_item` |
| Backtest | `backtest_run`（含 progress 列）, `backtest_daily_result`, `backtest_index_result`, `backtest_comparison` |
| Strategy | `strategy_config` |

Key migrations:
- 0001–0004: 基础表结构、回测表、指数/宏观表
- 0005–0006: 因子层表、因子定义增强
- 0007–0008: 指数因子回测、策略配置表
- 0009–0012: 回测模式字段、`index_signal` 表、回测日基准收益和换手率、回测指数原始得分
- 0013: `trading_calendar` 表、`benchmark_index` 增加 `is_active`/`delisting_date`、`macro_indicator` 增加 `period_date`
- 0016: `backtest_run` 增加 `progress` 列（回测执行进度 0-100）
- 0050: `index_daily_bar`/`index_valuation`/`macro_indicator`/`stock_daily_close`/`industry_daily_bar` 的 `ingested_at` 与 `industry_membership_event.fetched_at` 索引（`GET /api/system/status` 的 `max()` 聚合免全表扫描）
- 0042 / 0052: 把 `stock_universe` / `industry_universe` 上重复的质量快照列迁入 `data_health_snapshot` 后删除，质量口径收敛为一张表

### Frontend

- **Pages** (`src/pages/`): 16 pages — Dashboard, Index list, Index detail, Macro, Strategy list, Strategy detail (config viewer + editor), Factors list, Factor detail, Runs, Backtest list, Backtest create, Backtest detail, Backtest comparison create/detail, AI factors, Keyword tags
- **State** (`src/stores/`): 3 Pinia stores — `strategies`, `signals`, `backtests`; stores are for mutable shared state only
- **API layer** (`src/api/`): 7 files — `client.ts` (Axios 实例) + 6 API wrapper modules (`strategies.ts`, `signals.ts`, `backtests.ts`, `runs.ts`, `market_data.ts`, `factors.ts`); all return typed `PaginatedResponse<T>` (`{ items, total, offset, limit }`)
- **Read-only data pages** (index/macro): Fetch data **inline** via `ref()` + `onMounted`, no Pinia store — lighter pattern for static data views
- Charts use ECharts 5 (dynamic `import('echarts')`, `watch` with `flush: 'post'`, `dispose()` in `onUnmounted`)

## Current State

Services fully wired to PostgreSQL. Each data type has exactly **one** source: Index K-line→AkShare, Index valuation→AkShare, Macro→AkShare. Read-through cache pattern: GET endpoint → check DB → 未命中时入队 `data_fill` 后台任务并返回空列表。后台任务统一走 `background_job` 持久化队列（迁移 0023）。**所有外部数据的定时与手动同步、补缺口、全量重拉统一走 `POST /api/data-management/operations`**；旧入口 `POST /api/runs/daily-ingest`、`/runs/index-refresh`、`/runs/macro-refresh`、`/runs/cold-start`、`/runs/indexes/{code}/rebuild|incremental-fill` 已下线。Startup 时 lifespan 仅入队 `warm_calendar` 预热任务（启动补全已移除，**启动也不再自动执行健康检查**）；同步不再按"当天是否交易日"跳过，改为按最近交易日缺口补拉。`data_health_snapshot` 只在手动维护操作或数据摄取任务（全局同步）完成后刷新，首次部署无快照时 `GET /api/data-management` 返回 `snapshot_count=0`，前端据此提示而非报错。

**Strategy Engine**: `engine/` 包实现组件化策略执行管线。策略通过 `strategy_config` 表的 JSON 配置驱动，`StrategyConfigService` 管理 CRUD，`StrategyEngine` 执行管线。`FactorProvider` 桥接因子层与引擎层，`ContextBuilder` 统一构建实时和回测上下文。`BacktestService` 和 `StrategyExecutionService` 统一使用引擎执行。

**因子系统**: 32 个因子模板（32 个计算器类，一一对应），通过 `FactorTemplateRegistry` 登记，`FactorComputeService` 按「模板 + 规范化参数」现算。同一计算逻辑只登记一个模板：周期/窗口/比例等可调数值一律声明为 `parameter_schema`（22 个模板可调参），只有确实不含可调数值的模板才零参数（如 `close_price`/`pe_percentile`/`erp`/`index_diffusion_ratio`/`rrg_industry_match_score`）。**因子值不落库**：实时分配、因子详情/IC/相关性、回测、稳健性扫描一律按当次参数从原始数据现算。策略用 `factor_aliases` 声明「别名 → 模板 + 参数」，未声明的引用按模板 ID 与默认参数解释。`normalization.py` 提供 zscore/rank/minmax/winsorize/MAD 横截面标准化。`evaluation.py` 提供 IC/IR 分析和因子相关性矩阵（现算结果派生）。

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

- **时间与日期统一规则**: 时间戳统一按 UTC 生成、存储和 API 传输；后端使用 `utcnow()`，API 使用 `UtcDatetime`；交易日/回测日期等业务日期统一按北京时间 `Asia/Shanghai` 计算，使用 `today_cn()`。调度器配置时间也解释为北京时间。前端时间戳展示显式指定北京时间，日期字符串使用 `src/utils/date.ts`，禁止直接使用 `date.today()`、`datetime.now()` 或 `toISOString().slice(0, 10)` 处理业务日期。

- **Alembic**: `alembic/versions/` was empty on init — autogenerate requires a live DB connection. Hand-write the first migration if the DB is blank.
- **SQLAlchemy**: Stack is fully **sync** (`create_engine`, `sessionmaker`). Do not introduce async.
- **DB session injection**: Services take `db: Session` in `__init__`. Routers use `Depends(get_db)` from `api/deps.py` and construct services per-request (no module-level singletons).
- **`DailyBar.code` 语义**: 指数日线转换时 `IndexDailyBarModel.index_code → DailyBar(code=...)`。
- **Sync blocking in uvicorn**: Services use synchronous `urlopen` for external APIs. FastAPI runs sync routes in a thread pool (default 40 threads). Concurrent cold-start requests can exhaust the pool and cause timeouts — use a per-resource `threading.Lock` to serialize first-fetch, then read from DB on subsequent requests.
- **ECharts + TypeScript**: `echarts/index.d.ts` triggers TS1203 with `vue-tsc`. Fix: add `"skipLibCheck": true` to `apps/web/tsconfig.json`.
- **Backend venv on Windows**: Executables are at `apps/api/.venv/Scripts/` (e.g. `.venv/Scripts/alembic`, `.venv/Scripts/python`). Source code is at `apps/api/src/quant_etf_api/`.
- **`universe` 字典 key**: `build_universe_items()` 输出的 universe 字典以 `index_code` 为资产主键，引擎层统一通过 `item["index_code"]` 读取。
- **AkShare index valuation**: Only 沪深300(000300), 上证50(000016), 中证500(000905) return PE/PB from legulegu.com. Other indexes (000688/399001/399006) return empty — must handle gracefully in frontend.
- **Backend GET endpoints never return 500**: External API failures are caught/logged, returning `[]`. A 200 OK with empty array can mean either "no data yet" or "upstream error".
- **AkShare API instability**: Upstream network errors (ConnectionResetError, AttributeError) are common. Tests use `_retry_fetch()` with 3 attempts. Frontend pages catch errors silently and show "暂无数据".
- **因子值现算与同参去重**: `FactorComputeService.compute_matrix()` 以「模板 ID + 规范化参数」为去重键归并实例，同一模板同参数的多个别名只构建一个计算器、只算一次，结果再按实例 ID 展开。`asset_factors` 的键就是策略里的引用名，所以别名与模板 ID 对引擎完全等价。
- **`main.py` circular import via `factor_template_registry`**: `api/deps.py::get_factor_registry()` imports `factor_template_registry` from `main.py` using a deferred `from quant_etf_api.main import factor_template_registry` inside the function body — never at module level, or a circular import will occur.
- **`FactorRow` (schemas/signal.py) is reused for factor API responses** — `schemas/factor.py` 只额外定义模板元数据与横截面/IC 响应；因子值行本身仍复用 `FactorRow`（字段为 `factor_id` + `params`，不携带 `strategy_id`）。
- **`from __future__ import annotations` + `dict[str, Any]` requires explicit `from typing import Any`**: When a file has `from __future__ import annotations`, ruff (F821) treats `Any` as undefined even though it's only used in stringified type hints. Always add `from typing import Any` alongside the future import when using `dict[str, Any]` or similar generic types.
- **Ruff on Windows**: Installed at `.venv/Scripts/ruff.exe` (inside the project venv, not globally). Use `.venv/Scripts/ruff.exe check .` from `apps/api`.
- **Engine transform 函数**: 内置变换函数在 `engine/transforms.py` 的 `_TRANSFORM_REGISTRY` 中注册（`engine/score.py` 只做转发导入保持既有引用可用）。新增 transform 只需在该注册表中添加。
- **FactorProvider 依赖注入**: `FactorProvider` 需要 `db: Session` 与 `registry: FactorTemplateRegistry`（两者齐备才能现算）。`BacktestService` 在 `__init__` 中取进程级单例模板注册表并注入 `FactorProvider`/`ContextBuilder`。
- **回测仅支持配置模式**: 策略必须配置 `portfolio` 模块，`create_backtest` 会校验并拒绝无 portfolio 的策略。`backtest_mode` 和 `weighting` 字段已移除。
- **回测日收益基准（benchmark_return）和换手率（turnover）**: 存储在 `backtest_daily_result` 表中（migration 0011），前端 `BacktestDailyResult` 接口包含这两个可选字段。
- **index_signal 表** (migration 0010): 存储策略引擎对指数的信号计算结果，以 `index_code` 关联指数。
- **信号等级判定常量**: 定义在 `domain/common/constants.py`（`SIGNAL_THRESHOLD_HIGH=70`、`SIGNAL_THRESHOLD_MID=50`），引擎和回测服务统一引用，避免硬编码散落。
- **`backtest_index_result.original_score`** (migration 0012): 配置模式下保留原始综合得分，避免被权重值覆盖，便于分析策略评分与仓位的对应关系。
- **`backtest_index_result.scored`** (migration 0053): `backtest_index_result` 按当日 universe 全量标的落库，而引擎只对通过候选池与过滤规则的资产产出得分（`DefaultFilterEngine.filter` 会从 scores 中**删除**未通过项），`_write_index_results` 用 `score_map.get(code, 0.0)` 兜底 → 未参与评分的资产 `signal_score` 是**占位 0.0**。`scored=False` 标记这些行。**任何读 `signal_score` 做横截面统计的代码都必须跳过 `scored=False`**，否则占位 0 会固定占据底部名次、压缩日间波动并虚高 t 值。迁移不回填历史行（千万行级，且 zscore 模式的合法得分经 clamp 后可以恰好为 0，`signal_score <> 0` 回填会误伤），历史回测需重跑才有标记；生命周期体检每次新建监控回测，不受影响。
- **`backtest_index_result.selection_rebalanced`** (migration 0054): 标记当日是否执行**选股腿**调仓（取 `legs.selection`，风险腿只缩放总仓位、不算决策日）。周/月策略非调仓日的 `signal_score` 是引擎每天算出的潜在分数但并未被执行，组合分数 IC 只取 `TRUE` 的日期。历史行一律 `TRUE`（旧结果未记录实际选股日期）。
- **组合分数 IC 的横截面门槛是 5 而非 20**: 单因子 IC 用 `MIN_CROSS_SECTION_N=20`（横截面是指数池），组合分数 IC 用 `MIN_SCORE_CROSS_SECTION_N=5`（横截面是"当日评分集合"，规模由策略标的范围与过滤设计决定）。沿用 20 会让窄池策略永久拿不到 IC 证据。
- **`summarize_ic` 的 `already_non_overlapping`**: 组合分数 IC 的观测只取选股调仓日，相邻观测本身已相隔一个调仓周期，汇总时必须传 `already_non_overlapping=True`，否则 `effective_n` 被二次折算（实测 34 个非重叠观测折成 6 个、t 值低估约 2.4 倍）。单因子诊断取全交易日、仍是重叠窗口，保持默认 `False` 并配 `ic_decay_evidence(..., overlap_step=forward_days)`；两者日期集不同，不可直接比较。
- **IC 衰减门槛分两档**: 单因子 `IC_DECAY_MIN_HALF_N=20`（合计 40），组合序列 `SCORE_IC_DECAY_MIN_HALF_N=12`（合计 24）。非日频策略的观测数受调仓频率限制（174 个交易日下日频约 173、周频约 34、月频约 8），沿用单因子门槛会让 IC 腿在周/月策略上长期失效。`ic_evidence_shortfall()` 把缺口折算成"还需约 N 个调仓日 / X 个月"，由 `decay_shortfall_n` / `decay_shortfall_months` 透出前端。
- **IC 证据缺失要显式说**: `has_ic_decay_evidence()` 为 False 时 `assess_health()` 只降为 WATCH 但会在 `reasons` 写明"选股调仓日观测不足门槛，本次未采用 IC 证据"，快照带 `decay_evidence_sufficient` 透出前端——"未确认衰减"不能被读成"信号仍然有效"。
- **FilterRule.compare_to**: 过滤器支持跨因子比较（如 `ma_5d > ma_20d`）。`compare_to` 与 `value` 二选一，不能同时设置。`between` 操作符不支持 `compare_to`。
- **FactorProvider.collect_required_factor_ids() 必须收集 compare_to**: 遍历 filter rules 时不仅要收集 `rule.factor`，还要收集 `rule.compare_to`（若存在）。遗漏会导致被比较的因子值未加载，filter 始终失败 → 空仓。
- **FilterRuleValue 前端接口**: 定义在 `StrategyConfigForm.vue`（非共享 types 文件）。修改 FilterRule schema 时需同步更新：接口定义、表单模板、`initFilter()`、`buildConfig()`、校验逻辑，以及 `StrategyDetailPage.vue` 的只读展示。
- **后台任务状态流转**: `research_run` 状态链：pending → running → success/failed。`RunService.mark_running()` 在 bg 函数开始时调用，`mark_success(run_id, metrics)` / `mark_failed(run_id, error_message)` 在结束时调用。进程重启后 `recover_stuck_runs_on_startup()` 自动恢复卡死任务。
- **数据维护只有一条入口**: 指数/宏观/行业/个股的全部同步、补缺口、全量重拉与**质量检查**统一走 `POST /api/data-management/operations`（`DataManagementService` 编排，各数据服务只作为抓取/写入后端）。因此 `IngestService`、`IndustryDataService`、`StockDataService` 都不含质量规则实现与独立质量存储：`industry_universe` 的 5 个质量列已由迁移 0052 迁入 `data_health_snapshot` 后删除（个股同性质列早已由迁移 0042 删除），`domain/{market_data,industry,stocks}/quality.py` 三份平行 Python 规则实现已删除，字段合法性统一在 `DataManagementService._quality_conditions`（SQL）。并发互斥由 `DataManagementService._operation_lock` 与 `background_job` 队列 `job_key` 保证。**runs 路由只保留策略运行/重试与数据管理任务入口**；CLI 中与 DMS 重复的 `industry init-universe/backfill-bars/quality/fill/rebuild/backfill-membership`、`stock init-universe/quality/rebuild`、`index members refresh` 子命令已删除，仅保留 DMS 无法表达的操作（任意区间回填、指定交易日抓取、批量筛选器、破坏性 `prune-*`、成分覆盖状态、本地估值百分位重算）。历史 `research_run` 中的旧 `run_type`（daily_ingest/index_refresh/macro_refresh/cold_start/index_rebuild/index_incremental_fill/industry_*/stock_*）保留用于展示，但已不支持重试。
- **Run detail API**: `GET /runs/{run_id}` 返回 `ResearchRunDetail`（含 metrics、duration_seconds），`GET /runs/{run_id}/items` 返回 `ResearchRunItemSchema` 逐条明细，`POST /runs/{run_id}/retry` 重试失败任务（创建新 run 并提交到线程池）。
- **`GET /api/system/status` 禁止全表扫描**: 该接口是总览页首屏必调接口，`SystemService` 有两条性能契约：① 各表"记录数"先读 `pg_class.reltuples` 估算值，达到 `_ESTIMATED_COUNT_THRESHOLD`（50 万行）的表直接返回估算值，不再执行 `count(*)`（`stock_daily_close` 精确计数需并行全表扫描约 3.5 秒）；小表仍返回精确值。② "最近入库时间"依赖 `ingested_at`/`fetched_at` 上的 btree 索引做 `max()` 反向扫描（迁移 0050）——**新增数据表并纳入状态快照时必须同时补建该列索引**，否则接口会退化为秒级全表扫描。实测优化前后：15 秒 → 0.3 秒。

## Coding Standards

**Before generating or modifying any code, read [`docs/CODING_STANDARDS.md`](docs/CODING_STANDARDS.md).**

Key rules (details in the doc):
- All comments and docstrings must be in **Chinese**
- Every Python class/function/method must have a Chinese Google-style docstring
- Every TypeScript function must have a Chinese JSDoc comment
- When refactoring, **update** existing comments — never delete them
- No `any` types in TypeScript; use semantic HTTP status codes in routers
- **FactorSpec.lookback_days 与 `period_lookback_days()`**: 新增因子时必须设置合理的 `lookback_days`（自然日）。单周期技术指标统一用 `factors.base.period_lookback_days(period)`（交易日 × 1.6 + 10，至少 15 天）；月线类用 `monthly_lookback_days`（每月 60 天，至少 365）、RSRS 用 `rsrs_lookback_days(n, m)`、低振幅动量用 `low_amplitude_lookback_days`（1.75 倍）。**参数上界必须保证任一合法参数组合的回望 ≤ 注册表默认口径最大值（730 天）**，否则回测固定预热窗口下长窗口因子会静默算成 None（`test_no_legal_params_exceed_backtest_warmup` 守住该不变量）。
- **量比返回值语义**: 数据不足时返回 `None`（而非 1.0），区分"无数据"与"量比恰好为 1"。`calc_volume_ratio(code, trade_date, all_bars, period=20)` 返回 `float | None`，`calc_5d_return()` 仍返回 `float`（默认 0.0）。
- **BenchmarkIndexModel.is_active**: `ContextBuilder._build_live()` 只查询 `is_active=True` 的指数（实时只能用当日活跃集合）；`BacktestService._resolve_index_universe()` 用 point-in-time 口径（见下条）。新增指数默认 `is_active=True`。
- **TradingCalendar 缓存**: 首次调用时从 AkShare 加载（`tool_trade_date_hist_sina()`），TTL=1 天。`IngestService._drop_non_trading_bars`（剔除假期伪行情）与 `DataManagementService` 的缺口检查（`_latest_trading_day`）已接入，不再用 `weekday()>=5`。
- **rebalance.py 交易日历对齐**: `DefaultRebalanceScheduler` 接受 `TradingCalendar` 实例，weekly/biweekly/monthly 调仓的对齐语义是"**目标日（含）之后的第一个交易日**"：同周顺延、跨周顺延（目标周五休市 → 下周一）、跨月顺延（月末休市 → 下月首个交易日）都成立；`day_of_month` 超出当月天数时按当月最后一日处理；传入非交易日一律返回 False；日历不可用时异常上抛（不降级为按星期比较）。双周按 ISO 周序奇偶（`week_parity`）判定命中周期，未命中周期的目标日即使休市也不会在下一个周期补触发。`last_rebalance_date` 参数当前不参与判定。
- **RebalanceConfig 双腿语义**: `rebalance.selection`（重建成分）与 `rebalance.risk`（只把现有成分等比缩放到择时目标仓位）**两条腿必选**，JSON 中只给一条腿时另一条按相同日程归一化；旧平铺写法（`rebalance.frequency` 等）自动升级为**两腿同频**。两腿同频 = 每个调仓日按"新成分 + 择时目标仓位"重建（与改造前口径一致）；两腿异频 = 选股腿日只换成分、维持当前总仓位，总仓位只在风险腿日调整。腿判定统一走 `domain/strategies/rebalance.py::select_active_legs()`，回测与实时摘要共用；持仓合成走 `domain/portfolio/scaling.py::compose_two_leg_positions()`。空仓时风险腿不建仓（建仓由选股腿负责）。
- **RebalanceScheduleConfig 强校验**: `frequency` 为 `Literal[daily|weekly|biweekly|monthly]`，`day_of_week ∈ [0,4]`，`day_of_month ∈ [1,31]`（>28 时结构校验给 warning），`biweekly` 必须显式声明 `week_parity`（odd/even，无默认值）。历史行为是未知频率静默退化为每日调仓，新增频率分支时必须同步更新 `should_rebalance`、`_resolve_rebalance_calendar` 的"是否需要日历"判定与该枚举。
- **`StrategyConfig.frequency` 只是标注字段**: 不控制调仓（实际频率见 `rebalance.selection.frequency`），前端 chip 有 title 说明。
- **回测标的池是 point-in-time 口径**: `BacktestService._resolve_index_universe()` 用 `BenchmarkIndexRepository.find_for_period(start_date)`，即 `is_active OR delisting_date IS NULL OR delisting_date >= 区间起点`——退市日晚于区间起点的指数必须纳入（否则是幸存者偏差）；`IndexService.remove_index()` 会记录退市日（API 支持可选 `delisting_date`），重新激活时清空该列。`_ensure_market_scope_bars()` 用同一口径。
- **`t_plus_1_close` 执行模型**: T 日信号在 T+1 收盘成交，逐日收益必须用 `pending_positions`（今日收盘成交后的仓位），`positions`/`total_exposure` 同步反映实际持仓；用 `prev_positions` 会让新仓位首个收益区间被旧仓位吃掉（等价 T+2）。
- **验收清单默认强制**: `OptimizationService.finish(strict=True)` 是默认值（CLI `--no-strict` 才跳过）；"参数邻域"项的证据必须匹配当前配置（`robustness_run.baseline_config_hash` = 会话基线或候选哈希，或批次跑在候选策略上），promote 之后旧批次不再复用。
- **Deflated Sharpe 的试验次数台账**: `RobustnessService._trial_ledger()` = 稳健性批次 `trial_count` 累加 + 已评估（`evaluated`/`accepted`/`rejected`）优化会话数；手工不入批次的对比仍需 `--n-trials` 显式补充。拆分为 `statistics.n_trials_breakdown`。
- **候选池剔除原因码**: 当日缺行情记 `MISSING_CLOSE`/`MISSING_HIGH_LOW`，次日缺行情记 `MISSING_NEXT_BAR`/`MISSING_NEXT_HIGH_LOW`/`MISSING_OPEN`（后者仅 T+1 开盘执行）；判断"哪些资产不可交易"时必须区分这两类。
- **健康等级新增 `UNKNOWN`**: `assess_health()` 在所有监控窗口样本不足时返回 `UNKNOWN`（不再是 `HEALTHY`）；前端 `healthText`/`healthClass` 两处映射需同步维护。
- **StrategyConfig.index_codes**: 存储在 `config_json` 内部（非独立 DB 列），通过 `**row.config_json` 展开到 engine 的 `StrategyConfig` 模型。前端 API 请求中 `index_codes` 应在 `config_json` 内传递，非顶层字段。非空时 `_filter_by_scope()` 仅保留指定指数（实时和回测模式均生效）。
- **index_codes 回测强制应用**: `BacktestService.create_backtest()` 检查策略的 `config.index_codes`，非空时强制覆盖 `universe_filter` 为 subset 模式；`ContextBuilder._build_backtest()` 对传入的 index_codes 做交集过滤（双重保护）。
- **StrategyConfigForm 与 engine/config.py 的 StrategyConfig 同步**: 引擎新增配置模块时，需同步更新 `StrategyConfigForm.vue`（表单）、`StrategyDetailPage.vue`（详情展示）。目前已覆盖全部 7 个模块（score/timing/filters/rank/portfolio/risk/rebalance）+ 资产范围 index_codes。
- **`StrategySummary` 已有 `index_codes` 顶层字段**: 列表 API 直接返回 `index_codes`，前端无需额外调用 `fetchStrategyDetail()`。`StrategyConfigForm.vue` 读取 `modelValue.index_codes`（config_json 内），`StrategyDetailPage.vue` 和 `BacktestCreatePage.vue` 读取顶层 `store.current?.index_codes`。
- **index_daily_bar OHLC 字段**: `IndexDailyBarModel` 有 `open_price`、`high_price`、`low_price`、`close_price` 字段，技术指标因子（ATR/Donchian）通过 `ctx.index_bars` 直接访问。
- **`get_factor_template_registry()` vs `build_default_registry()`**: 进程级单例通过 `get_factor_template_registry()` 获取（首次构建后缓存），避免 `BacktestService` 每请求重建；`build_default_registry()` 每次返回全新注册表，供 CLI、模板单测与需要独立实例的场景使用。
- **`BatchFactorComputer` Protocol**: 定义在 `factors/base.py`，回测因子预计算时优先调用 `compute_batch()`（一次遍历 bar 数据覆盖所有日期）。已实现者：均线/收益率/波动率/区间宽度/回撤/ATR/Donchian/RSI/量比（成交量类仅逐点）/RSRS/日内位置/低振幅/市场宽度/成交额量比。新增回测频繁使用的因子时建议实现此协议。
- **`validate_config` 依赖 DB，`_structural_validation` 才是静态的**: `StrategyConfigService.validate_config()` / `validate_parsed()` 是实例方法，用 `self._db` 查 `factor_definition` 的 active 集合，并取进程级模板注册表；只有 `_structural_validation()` 是无状态的 `@staticmethod`。未知因子引用、别名指向未注册/停用模板、参数越界、未知变换函数都进 errors 快速失败。
- **AI 分析双调度器**: 数据摄取在 `schedule_time`（默认 17:30）执行，AI 舆情分析在 `ai_schedule_time`（默认 23:30）独立执行。两个调度器通过 `main.py` lifespan 分别启动，互不影响。`ai_analysis_enabled=False` 时 AI 调度器不启动。**所有数据源的定时摄取统一走全局数据同步调度器**（`data_sync_all` → `DataManagementService.sync_latest`，覆盖指数/宏观/行业/个股等全部受管数据集）：原独立的行业摄取调度器（`get_industry_scheduler` / `IndustryIngestScheduler`）与行业日频摄取链（`handle_industry_daily_ingest`、`IndustryDataService.run_daily_ingest()`）已删除，行业数据按 `industry_universe`/`industry_daily_bar`/`industry_membership` 数据集由全局同步增量补拉。数据同步不再串联因子计算任务：因子值按请求/按研究任务现算。行业与个股的**单对象手动入口**（`industry_universe_refresh`/`industry_bars_refresh`/`industry_quality_check`/`industry_data_fill`/`industry_data_rebuild`、`stock_quality_check`/`stock_data_fill`/`stock_data_rebuild`）与指数/宏观旧入口一样已全部删除，统一由数据管理操作承担。
- **关键词标签可配置化**: `keyword_tag_config` 表存储关键词→资产标签映射，替代硬编码的 `classifier._KEYWORD_TAG_MAP`。`TagClassifier._classify_via_keyword()` 优先使用 DB 映射，回退到静态默认值。CRUD 端点: `GET/POST/PUT/DELETE /keyword-tags`。
- **市场综合研判**: `market_synthesis` 表存储每日 AI 生成的市场概况（200-300 字中文研判）。在 `AIFactorService.run_full_pipeline()` 步骤 7 自动生成，LLM 不可用时静默跳过。API: `GET /ai-factors/synthesis/{date}`。
- **AI 因子不在策略引擎中**: AI 因子（ai_sentiment_*/ai_attention_*/ai_topic_momentum）已从策略引擎移除，策略配置引用它们会被校验拒绝；`daily_sentiment_aggregate` 只服务于 AI 舆情分析展示页（新闻采集/情绪聚合/市场研判），不再作为策略因子输入。
