# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Quant Index Asset Allocation System — an asset allocation decision system for A-share indexes (daily frequency only, no individual stocks, no ETF, no trading execution). Full-stack: FastAPI backend + PostgreSQL + Vue 3 frontend. Uses a **component-based, configuration-driven** strategy engine: new strategies are created via JSON config stored in the database, no Python code needed.

> **系统范围：本系统只研究 A 股指数，不研究 ETF。** 代码库中不含任何 ETF 数据源、数据表、接口或前端页面（`quant_etf_api` 等包名/环境变量为历史命名保留）。**策略资产原则：策略可配置资产只能是用户自行添加、且市场上存在实际 ETF 对应物的 A 股指数（`benchmark_index`）；申万 801xxx 行业与个股永不作为策略资产或回测标的，只作为因子输入与研究数据。**

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

### AI 优化 CLI（自动优化闭环）

面向 Codex 等 agent 的策略优化工具，新命令默认 JSON 输出（`--no-json` 转文本）：

```bash
python -m quant_etf_api.cli strategy list/show/validate/create/update/diff     # 策略配置读写与校验
python -m quant_etf_api.cli strategy prune-variants --batch <批次> [--apply --force]  # 清理稳健性变体草稿（D4，默认预演）
python -m quant_etf_api.cli strategy consume-validation <策略> [--note "..."]   # 标记验证期数据已被消费（D5）
python -m quant_etf_api.cli backtest run --strategy <id> [--start --end --async --priority N]  # 回测（默认同步执行）
python -m quant_etf_api.cli backtest status <id> --wait                         # 轮询等待回测终态
python -m quant_etf_api.cli backtest list [--status --purpose --calendar-source --limit]  # 回测列表过滤（B4/C1）
python -m quant_etf_api.cli backtest cancel <id>                                # 取消回测（运行中在安全检查点退出）
python -m quant_etf_api.cli backtest show <id> [--cost-bps N --cost-ladder 0,10,20,30,50]  # 详情 + 多档成本（C3）
python -m quant_etf_api.cli backtest pool <id>                                   # 有效候选池时间线（C6）
python -m quant_etf_api.cli backtest orphans [--limit N]                         # 悬挂引用审计（C5）
python -m quant_etf_api.cli backtest delete <id> [--force]                       # 受控删除回测（C5）
python -m quant_etf_api.cli backtest prune-dangling-refs [--apply]               # 清理 JSONB 悬挂引用（C5，默认预演）
python -m quant_etf_api.cli queue stats|jobs [--status --job-type --limit]      # 队列积压/吞吐/运行中任务（B3）
python -m quant_etf_api.cli queue worker                                        # 独立 worker 进程（前台阻塞，B1）
python -m quant_etf_api.cli robustness collect <batch> [--allow-partial]        # 部分汇总并标记 coverage（B7）
python -m quant_etf_api.cli robustness cancel|pause|resume|abandon <batch>      # 批次取消/暂停/恢复/作废（B2/F-4）
python -m quant_etf_api.cli robustness scan --strategy <id> --sync --parallel 4 # 本地多进程并行跑批次（F-13）
python -m quant_etf_api.cli research batch --strategy <id> --variants v.json --summary  # 变体排名表（F-9）
python -m quant_etf_api.cli robustness scan --strategy <id> [--preset quick|standard] [--knobs a,b] [--knobs-file f.json]  # 邻域扫描（D2）
python -m quant_etf_api.cli research batch --strategy <id> --variants v.json [--windows 5] [--cost-bps 10]  # 变体批量评估，不落库（D1）
python -m quant_etf_api.cli optimization start --strategy <基线> --candidate-file x.json --hypothesis "..."  # 建草稿候选+会话
python -m quant_etf_api.cli optimization evaluate <opt_id> [--folds 4]          # 全区间+滚动样本外回测
python -m quant_etf_api.cli optimization report <opt_id> --file report.md       # 生成报告骨架
python -m quant_etf_api.cli optimization finish <opt_id> --verdict accept --report-file report.md --promote [--strict]
```

回测创建时快照策略配置（`backtest_run.config_snapshot/config_hash`），执行时优先用快照重建配置，保证回测结果可复现。优化会话记录在 `strategy_optimization` 表（迁移 0028）。

> **回测区间下限（用户约定，2026-09-12 起）**：所有回测、对比与优化评估的区间**一律从 2016-01-01 开始**，不再使用 2016 年之前的数据（含 `--start` 缺省值、滚动分段的第一段、`optimization start --start`）。早于 2016 年的结果仅作历史参考，不作为验收依据。

> **研究期 / 验证期边界（硬约束，2026-09-13 起）**：研究期固定为 **2016-01-01 ~ 2025-12-31**，**2026-01-01 起为验证期**。边界定义在 `Settings.research_period_start/research_period_end/validation_period_start`，规则实现在 `domain/research/periods.py`。`backtest_run.purpose`（research/validation/monitor）标记用途：**研究类回测与优化会话越过研究期末端会被直接拒绝**，验证/监控用途允许使用验证期数据并进入留痕列表（`GET /api/backtests/validation-usage`）。`backtest run` 与 `optimization start` 的 `--end` 缺省为研究期末端。理论依据见 [`docs/architecture/策略稳健性评估与生命周期监控实施说明.md`](docs/architecture/策略稳健性评估与生命周期监控实施说明.md)。

> **稳健性与生命周期模块（2026-09-13 起）**：单次回测的稳健性指标（成本折算、收益集中度、分段一致性、回撤结构）在读取路径由 `domain/research/stability.py` 现算，暴露为 `BacktestDetail.stability`，不新增数据库列、不重跑历史回测；候选集级别的过拟合风险（CSCV-PBO / Deflated Sharpe / 块自助法 / 参数邻域 / 因子消融 / 池扰动）由 `services/robustness_service.py` 与 CLI `robustness` 命令组承担，结果落 `robustness_run` 表并兼作试验次数台账；上线后的监控与诊断由 `services/strategy_lifecycle_service.py` 承担（`strategy_lifecycle` / `strategy_health_snapshot` 表，`/lifecycle` 页面），**状态只人工变更、系统不自动调参**，LIVE 状态下禁止直接修改 `config_json`。

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

- **`api/routers/`** — 11 route groups: `health`, `system`, `indexes`, `market_data`, `strategies`, `factors`, `runs`, `backtests`, `robustness`, `queue`, `ai_factors`, `keyword_tags`
- **`api/middleware.py`** — `RequestIdMiddleware`：为每个请求注入唯一 request_id，写入响应头和日志 ContextVar
- **`services/`** — Business logic; `IngestService` uses read-through cache (DB → lock → external API → upsert). `ContextBuilder` shim re-exports from `engine/context_builder.py`. `DataFreshnessService` 独立负责数据新鲜度汇总，`IngestService` 只保留摄取编排门面。其他服务包括 `index_service.py`、`factor_admin_service.py`（因子定义同步，与 FactorService 计算编排分离）、`strategy_decision_service.py`（统一策略执行入口：加载配置→校验→构建上下文→补算触发→引擎执行→可选持久化）。基准收益、数据质量和绩效指标规则位于 `domain/`，`services/benchmark.py`、`services/data_quality.py`、`services/metrics.py` 仅保留历史导入兼容转发。
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
- **`infra/db/`** — SQLAlchemy 2 ORM models (`infra/db/models/core.py` has 22 tables) + repository files (`infra/db/repositories/`). Repositories own all DB queries; services delegate to them for read operations, own only write logic. 后续补充仓库：`index_valuation.py`、`macro_indicator.py`、`index_signal.py`；`index_factor_value` 的 builtin 因子与策略回填两类写入统一走 `IndexFactorValueRepository` 写入门禁（C4）.
- **`infra/clients/`** — 2 data source clients, all inherit from `base.py`:
  - `akshare_index.py` (index daily + PE/PB valuation), `akshare_macro.py` (CPI/PMI/LPR)
  - `retry_decorator.py` — `@with_retry()` 装饰器，指数退避重试，参数可通过环境变量 `AKSHARE_RETRY_MAX_ATTEMPTS` / `AKSHARE_RETRY_BASE_DELAY` 配置
- **`infra/trading_calendar.py`** — `TradingCalendar` 类，**严格口径（C1）**：上游按 Tushare Pro `trade_cal`（优先）→ AkShare `tool_trade_date_hist_sina()` 加载，内存缓存成功 TTL=1 天、失败负缓存仅 60 秒；取不到日历时 `is_trading_day/latest_trading_day/next_trading_day/trading_days_between` 抛 `TradingCalendarUnavailableError`，**不存在周末降级**。离线兜底走 `resolve_trading_calendar(db, required_range=...)`：上游 → 本地 `trading_calendar` 表快照（`DbTradingCalendar`）→ 报错。
- **`infra/job_queue/`** — **统一后台任务队列**：`background_job` 表（迁移 0023，迁移 0046 增加 `batch_id`/`heartbeat_at`/`cancel_requested` 并把时间戳改为 timestamptz）+ `JobRepository`（`FOR UPDATE SKIP LOCKED` 认领）+ `JobQueue`（**按 lane 划分**的 worker 线程池：回测 lane `job_queue_backtest_workers` 默认 1，通用 lane `job_queue_workers` 默认 2）+ `context.py`（协作取消上下文）+ `handlers.py`（`JOB_HANDLERS` / `JOB_ABANDON_HANDLERS` 分发表）。所有后台任务（摄取/因子/回测/对比/AI/日历预热/GET 补数）统一 `enqueue(job_type, payload, job_key=..., priority=..., batch_id=...)`，支持 `job_key` 幂等去重、`priority` 抢跑、`max_attempts` 重试与批次级取消/暂停。独立 worker 进程入口为 `python -m quant_etf_api.worker`（API 侧配 `QUANT_ETF_JOB_QUEUE_EMBEDDED=false`）。`recover_stuck_jobs()` 仍用于进程重启恢复；运行期由心跳 + 僵尸扫描回收异常任务。
- **`infra/scheduler/`** — `DailyIngestScheduler` / `AIAnalysisScheduler`: daemon `Thread` + `Event` 定时器，仅负责在预定时间将任务入队（`job_key="daily_ingest"` / `ai_analysis:{date}`），实际执行在任务队列 worker 中，调度线程不做任何同步外部调用。数据摄取调度器不做交易日判断：周末/节假日也会入队，由摄取任务按"最近交易日缺口"决定是否补拉。
- **`domain/`** — Pure domain logic (no SQLAlchemy/FastAPI imports):
  - `common/` — `bar_metrics.py` (BAR computation), `numeric.py`（NaN/Inf 和价格字段容错）、`enums.py` (SignalLevel, RunStatus, RunType, FactorCategory, BacktestStatus), `values.py` (DateRange), `constants.py`（信号等级阈值和标签常量）、`trading_calendar.py`（`TradingCalendarLike` 协议 + `TradingCalendarUnavailableError`；**不再有周末兜底实现**，C1）
  - `strategies/` — `models.py` (StrategyContextData, StrategyResult, TimingSignal, AssetRanking, UniverseAsset dataclasses)、`rebalance.py`（纯调仓规则，**日历必填**；engine/rebalance.py 为兼容转发层）
  - `portfolio/` — `turnover.py`（换手率，含 `TURNOVER_MODEL_DELTA_W`/`TURNOVER_MODEL_LEGACY` 口径常量）、`returns.py`（T+1 收益）、`benchmark.py`（回测基准收益）、`accounting.py`（`BacktestDayAccumulator` 累计/回撤记账）、`universe.py`（universe 构建与 subset 过滤）
  - `market_data/` — `quality.py`（日线、估值与连续性质量规则）
  - `research/` — 研究评估领域规则（绩效指标、walk-forward 窗口切分、`stability.py` 稳健性与多档成本并列 `compute_cost_ladder`）
- **`factors/`** — Single-factor computation layer: `base.py` (FactorSpec/FactorContext/FactorValue/FactorComputer Protocol), `registry.py` (FactorRegistry), `service.py` (FactorService orchestrates computation + persistence), `evaluation.py` (IC/IR analysis + factor correlation matrix), `normalization.py` (zscore/rank/minmax/winsorize/MAD 横截面标准化), `builtins/`（价格/动量/波动/估值/量能/技术/月线等指数因子 + `index_panel_factors.py` 的指数成分扩散与 RRG 行业匹配两类面板因子）。**指数因子值写入 `index_factor_value` 表；行业/个股数据只作为面板因子的内部输入，不出现在策略资产域**。
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
- **只读约束（C3）**：`ContextBuilder` 不产生任何写操作；`detect_missing_factors()` 只读返回三态缺失原因（`MissingReason`：FACTOR_UNKNOWN/NOT_COMPUTED/INSUFFICIENT_DATA），补算入队由 `StrategyDecisionService.ensure_live_factors` 在服务层触发
- `services/context_builder.py` 是向后兼容 shim，re-export 自 engine 版本

### Database schema (25 tables, migrations 0001–0023)

| Group | Tables |
|---|---|
| Reference | `benchmark_index` |
| Market data | `index_daily_bar`, `index_valuation`, `macro_indicator`, `source_payload_log` |
| Analytics | `factor_definition`, `index_factor_value`, `signal_definition`, `index_signal` |
| Runtime | `research_run`, `research_run_item` |
| Backtest | `backtest_run`（含 `progress` / `candidate_pool` 列）, `backtest_daily_result`, `backtest_index_result`, `backtest_comparison` |
| Strategy | `strategy_config` |
| Task queue | `background_job` |

Key migrations:
- 0001–0004: 基础表结构、回测表、指数/宏观表
- 0005–0006: 因子层表、因子定义增强
- 0007–0008: 指数因子回测、策略配置表
- 0009–0012: 回测模式字段、`index_signal` 表、回测日基准收益和换手率、回测指数原始得分
- 0013: `trading_calendar` 表、`benchmark_index` 增加 `is_active`/`delisting_date`、`macro_indicator` 增加 `period_date`
- 0016: `backtest_run` 增加 `progress` 列（回测执行进度 0-100）
- 0046: 队列容量与可观测性（`background_job` 批次/心跳/取消 + 队列与回测时间戳 timestamptz）
- 0047: `backtest_run.candidate_pool`（逐日有效候选池时间线，C6）
- 0048: 回测引用完整性（外键 `ON DELETE CASCADE`/`SET NULL` + 存量悬挂引用预清理，C5）

### Frontend

- **Pages** (`src/pages/`): 16 pages — Dashboard, Index list, Index detail, Macro, Strategy list, Strategy detail (config viewer + editor), Factors list, Factor detail, Runs, Backtest list, Backtest create, Backtest detail, Backtest comparison create/detail, AI factors, Keyword tags
- **State** (`src/stores/`): 3 Pinia stores — `strategies`, `signals`, `backtests`; stores are for mutable shared state only
- **API layer** (`src/api/`): 7 files — `client.ts` (Axios 实例) + 6 API wrapper modules (`strategies.ts`, `signals.ts`, `backtests.ts`, `runs.ts`, `market_data.ts`, `factors.ts`); all return typed `PaginatedResponse<T>` (`{ items, total, offset, limit }`)
- **Read-only data pages** (index/macro): Fetch data **inline** via `ref()` + `onMounted`, no Pinia store — lighter pattern for static data views
- Charts use ECharts 5 (dynamic `import('echarts')`, `watch` with `flush: 'post'`, `dispose()` in `onUnmounted`)
- **长任务状态轮询统一使用 `composables/usePolling.ts`**: 自动绑定组件生命周期（卸载即停止），禁止在 store/页面里再手写 `setInterval` 轮询循环。store 只提供单次刷新 action（如 `refreshOne`），由页面通过 `usePolling` 驱动。

## Current State

Services fully wired to PostgreSQL. Each data type has exactly **one** source: Index K-line→AkShare, Index valuation→AkShare, Macro→AkShare. Read-through cache pattern: GET endpoint → check DB → 未命中时入队 `data_fill` 后台任务并返回空列表（不再在请求线程同步抓取）。后台任务统一走 `background_job` 持久化队列（迁移 0023），调度器仅负责定时入队（周末/节假日也入队，由摄取侧按最近交易日缺口补拉）。`POST /api/runs/daily-ingest` 触发手动入队。Startup 时 lifespan 仅入队 `warm_calendar` 预热任务（启动补全已移除）。

**Strategy Engine**: `engine/` 包实现组件化策略执行管线。策略通过 `strategy_config` 表的 JSON 配置驱动，`StrategyConfigService` 管理 CRUD，`StrategyEngine` 执行管线。`FactorProvider` 桥接因子层与引擎层，`ContextBuilder` 统一构建实时和回测上下文。`BacktestService` 和 `StrategyExecutionService` 统一使用引擎执行。

**因子系统**: 内置指数因子通过 `FactorRegistry` 注册，`FactorService` 编排计算和持久化（`index_factor_value` 表）；指数级面板因子（`index_diffusion_ratio`、`rrg_industry_match_score`）由 `IndexFactorPanelService` 按 required_data 组装行业面板/成分数据后计算。`FactorSpec` 增加 `lookback_days` 字段，`FactorService._load_context()` 动态使用所有因子的最大 lookback。`FactorContext` 增加 `macro_indicators` 与 `panels` 字段。`normalization.py` 提供 zscore/rank/minmax/winsorize/MAD 横截面标准化。`evaluation.py` 提供 IC/IR 分析和因子相关性矩阵。

> 因子元数据轴（value_shape/usage/default_params）与“因子中心=正式因子、研究页=独立实验”的研发流程见 [`docs/architecture/因子研发与集成指引.md`](docs/architecture/因子研发与集成指引.md)；行业 RRG/扩散面板仅作为指数级因子的内部数据输入（`rrg_industry_match_score` / `index_diffusion_ratio`），策略配置不含 rotation 模块或行业资产域。

**Backtesting**: `BacktestService` 使用统一 `_run_backtest_loop`。集成 `FactorProvider` 预计算因子、`ContextBuilder` 构建上下文、专业绩效指标（`metrics.py`）、基准对比（`benchmark.py`）。回测收益为**毛收益**：系统当前阶段不考虑实盘交易与交易成本，仅研究策略理想效果。支持调仓频率控制和换手率计算（C2 起含清仓/建仓腿）。回测仅支持配置模式（策略需配置 portfolio 模块）。**口径可信度（C1~C6）**：调仓日历严格解析并写入口径指纹（`_calendar_source`）、换手口径有指纹（`_turnover_model`）、净口径多档成本在读取路径并列现算（`stability.cost_ladder`）、任意跨度可直接回测（无强制分段）、删除回测有外键级联/置空 + 悬挂引用审计、候选池逐日时间线落 `backtest_run.candidate_pool`。

**Asset allocation API**: `GET /strategies/{strategy_id}/allocation` runs the full decision pipeline and returns timing signal, asset rankings, and allocation plan.

**Strategy config API**:
- `GET /strategies` — 列表
- `GET /strategies/{id}` — 详情
- `POST /strategies` — 创建配置
- `PUT /strategies/{id}` — 更新配置
- `DELETE /strategies/{id}` — 删除配置
- `POST /strategies/validate` — 校验配置（含因子 ID 与变换函数校验，见 P4 修复）

## Gotchas

- **时间与日期统一规则**: 时间戳统一按 UTC 生成、存储和 API 传输；后端使用 `utcnow()`，API 使用 `UtcDatetime`；交易日/回测日期等业务日期统一按北京时间 `Asia/Shanghai` 计算，使用 `today_cn()`。调度器配置时间也解释为北京时间。前端时间戳展示显式指定北京时间，日期字符串使用 `src/utils/date.ts`，禁止直接使用 `date.today()`、`datetime.now()` 或 `toISOString().slice(0, 10)` 处理业务日期。**时间戳列分两类（B5 起）**：队列与回测相关列（`background_job.*`、`backtest_run.created_at/started_at/finished_at`、`backtest_comparison.*`）已是 `timestamptz`，写入必须用 `infra.time.utcnow_aware()`、查询过滤也要传 aware datetime；其余历史列仍是 naive timestamp，继续用 `utcnow()`。新增时间戳列一律用 `DateTime(timezone=True)` + `utcnow_aware()`。

- **Alembic**: `alembic/versions/` was empty on init — autogenerate requires a live DB connection. Hand-write the first migration if the DB is blank.
- **SQLAlchemy**: Stack is fully **sync** (`create_engine`, `sessionmaker`). Do not introduce async.
- **DB session injection**: Services take `db: Session` in `__init__`. Routers use `Depends(get_db)` from `api/deps.py` and construct services per-request (no module-level singletons).
- **`DailyBar.code` 语义**: 指数日线转换时 `IndexDailyBarModel.index_code → DailyBar(code=...)`。
- **Sync blocking in uvicorn**: Services use synchronous `urlopen` for external APIs. FastAPI runs sync routes in a thread pool (default 40 threads). Concurrent cold-start requests can exhaust the pool and cause timeouts — use a per-resource `threading.Lock` to serialize first-fetch, then read from DB on subsequent requests.
- **ECharts + TypeScript**: `echarts/index.d.ts` triggers TS1203 with `vue-tsc`. Fix: add `"skipLibCheck": true` to `apps/web/tsconfig.json`.
- **Backend venv on Windows**: Executables are at `apps/api/.venv/Scripts/` (e.g. `.venv/Scripts/alembic`, `.venv/Scripts/python`). Source code is at `apps/api/src/quant_etf_api/`.
- **`universe` 字典 key**: `build_universe_items()` 输出的 universe 字典以 `index_code` 为资产主键，引擎层统一通过 `item["index_code"]` 读取。
- **AkShare index valuation**: Only 沪深300(000300), 上证50(000016), 中证500(000905) return PE/PB from legulegu.com. Other indexes (000688/399001/399006) return empty — must handle gracefully in frontend.
- **Backend GET endpoints never return 500**: External API failures are caught/logged, returning `[]`. A 200 OK with empty array can mean either "no data yet" or "upstream error". **唯一例外是交易日历不可用**：此时返回 503（`main.py` 的 `TradingCalendarUnavailableError` 全局处理器），因为按星期近似的日期/缺口结论比直接报错更危险（C1）。
- **AkShare API instability**: Upstream network errors (ConnectionResetError, AttributeError) are common. Tests use `_retry_fetch()` with 3 attempts. Frontend pages catch errors silently and show "暂无数据".
- **数据源架构为单源（P6 已收敛）**: `infra/data_sources/` 多源管理框架（DataSourceManager / CircuitBreaker / 适配器 / Tushare-YFinance 占位）已删除，摄取链路统一走 `infra/clients/`（AkShareFund / AkShareIndex / AkShareMacro / ExchangeReference）。容灾边界 = 单客户端内多端点降级 + `@with_retry`。不要再引入平行数据源抽象层；若未来接入 Tushare 等第二源，需重新设计统一抽象。
- **多源日线量/额字段单位不统一（暂不处理）**: 五源降级链的 OHLC 点位一致（指数点位无复权概念），但 `volume`/`turnover` 单位混用：腾讯源无 volume（补 0）、成交额单位"元"；中证源成交量"股"、成交额"亿元"（差 1e8 倍）；新浪无成交额；东财通常返回"手"。`_build_index_bars` 只做缺失补 0，无单位归一化；`volume_ratio_*` 对腾讯源指数恒为 None。详见 `docs/架构问题分析.md` 7.2 第 5 条。
- **PostgreSQL NULL uniqueness in `index_factor_value`**: `NULL != NULL` means `(trade_date, index_code, factor_id, strategy_id=NULL)` won't prevent duplicates via the composite unique constraint. Solved by partial unique index `uq_index_factor_value_builtin` on `(trade_date, index_code, factor_id) WHERE strategy_id IS NULL` (migration 0007). SQLAlchemy upsert uses `index_where=IndexFactorValueModel.strategy_id.is_(None)` to reference it.
- **`main.py` circular import via `factor_registry`**: `api/deps.py::get_factor_registry()` and `infra/scheduler/__init__.py` both import `factor_registry` from `main.py` using deferred `from quant_etf_api.main import factor_registry` inside the function body — never at module level, or a circular import will occur.
- **`FactorRow` (schemas/signal.py) is reused for factor API responses** — no separate factor value schema exists. `schemas/factor.py` only defines `FactorSpecResponse`.
- **`from __future__ import annotations` + `dict[str, Any]` requires explicit `from typing import Any`**: When a file has `from __future__ import annotations`, ruff (F821) treats `Any` as undefined even though it's only used in stringified type hints. Always add `from typing import Any` alongside the future import when using `dict[str, Any]` or similar generic types.
- **Ruff on Windows**: Installed at `.venv/Scripts/ruff.exe` (inside the project venv, not globally). Use `.venv/Scripts/ruff.exe check .` from `apps/api`.
- **Engine transform 函数**: 内置变换函数在 `engine/score.py` 的 `_TRANSFORM_REGISTRY` 中注册。新增 transform 只需在该注册表中添加。
- **FactorProvider 依赖注入**: `FactorProvider` 需要 `db: Session`（实时模式）和 `registry: FactorRegistry`（回测模式）。回测服务在 `__init__` 中构建 `FactorRegistry` 和 `FactorProvider`，通过 `ContextBuilder` 注入。
- **回测仅支持配置模式**: 策略必须配置 `portfolio` 模块，`create_backtest` 会校验并拒绝无 portfolio 的策略。`backtest_mode` 和 `weighting` 字段已移除。
- **回测日收益基准（benchmark_return）和换手率（turnover）**: 存储在 `backtest_daily_result` 表中（migration 0011），前端 `BacktestDailyResult` 接口包含这两个可选字段。**换手口径（C2）**：统一按 `Σ|Δw|/2` 计算，清仓腿（旧仓位 → 空仓）与建仓腿（空仓 → 新仓位）全额计入；新建回测写 `params["_turnover_model"]="delta_w_v2"`，缺该键的存量回测按 `legacy_v1` 标注（`stability.turnover_model`），两者净口径指标**不可直接比较**。回归测试：`tests/unit/test_backtest_turnover.py`。
- **index_signal 表** (migration 0010): 存储策略引擎对指数的信号计算结果，以 `index_code` 关联指数。
- **信号等级判定常量**: 定义在 `domain/common/constants.py`（`SIGNAL_THRESHOLD_HIGH=70`、`SIGNAL_THRESHOLD_MID=50`），引擎和回测服务统一引用，避免硬编码散落。
- **`backtest_index_result.signal_score` / `target_weight`** (migration 0024): 回测信号口径与实时一致 —— `signal_score` 为综合得分（0-100），`target_weight` 为信号目标仓位权重（0-1，与实时 `payload.target_weight` 同义）；原 `original_score` 列已删除（语义与新 `signal_score` 重复）。
- **`backtest_run.warnings`** (migration 0025): 回测执行期收集的结构化提示（`BacktestWarning`：level/code/message/trade_date/index_code），覆盖 `WARMUP`（预热期）/ `MISSING_FACTOR`（因子缺失）/ `DATA_GAP`（行情断档）/ `BENCHMARK_MISSING` / `PARTIAL_RESULT`（失败部分结果）；`BacktestDetail.warnings` 返回给前端，轮询时按 key 去重弹提示。
- **回测候选池口径与 `data_quality_mode` 解耦（B16）**：回测每日候选池统一走 `BacktestService._build_candidate_pool()`（当日收盘价 + 策略引用的全部资产级因子 + 需要时的最高/最低价 + 次日收盘价（t_plus_1_open 还需次日开盘价）），**两种口径共用同一候选池**，选股、持仓与收益序列完全一致。`data_quality_mode` 只影响缺口提示详细程度：`strict` 逐指数输出 `DATA_EXCLUDED`，`warn` 输出一条汇总 `DATA_EXCLUDED` + 逐指数 `DATA_GAP`。历史缺陷：`warn` 曾把全部标的（含无法交易/因子缺失）塞进横截面 z-score 池，同一配置在 warn/strict 下得到 351% vs 210% 两套结果，且 warn 会买入次日无开盘价的标的、把该段收益记为 0（隐性乐观偏差）。回归测试：`tests/unit/test_backtest_candidate_pool.py`。
- **FilterRule.compare_to**: 过滤器支持跨因子比较（如 `ma_5d > ma_20d`）。`compare_to` 与 `value` 二选一，不能同时设置。`between` 操作符不支持 `compare_to`。
- **FilterRule.missing_strategy** (B6): 过滤规则因子值（或 compare_to 参照值）为 `None` 时的处理策略，取值 `pass` / `fail` / `exclude`，默认 `fail`（与历史行为一致，仅显式化）。`FilterRuleResult` 带 `missing` / `missing_strategy` 调试字段；非法值在配置校验期（P4）报 422。前端需同步 `StrategyConfigForm.vue` 的 `FilterRuleValue` 接口与 `StrategyDetailPage.vue` 只读展示。
- **FactorProvider.collect_required_factor_ids() 必须收集 compare_to**: 遍历 filter rules 时不仅要收集 `rule.factor`，还要收集 `rule.compare_to`（若存在）。遗漏会导致被比较的因子值未加载，filter 始终失败 → 空仓。
- **FilterRuleValue 前端接口**: 定义在 `StrategyConfigForm.vue`（非共享 types 文件）。修改 FilterRule schema 时需同步更新：接口定义、表单模板、`initFilter()`、`buildConfig()`、校验逻辑，以及 `StrategyDetailPage.vue` 的只读展示。
- **后台任务状态流转**: `research_run` 状态链：pending → running → success/skipped/failed。`skipped` 表示"未执行"：daily_ingest/index_refresh/macro_refresh 等摄取入口共享同一把摄取互斥锁，并发冲突时标记 skipped（metrics.reason=concurrent_skip）；daily_ingest/index_refresh 已不再因非交易日跳过（改为按最近交易日缺口补拉，周末/节假日触发时补齐缺失数据），macro_refresh 与单指数增量补数仍保留非交易日跳过（reason=holiday）。任务通过 `get_job_queue().enqueue(...)` 入队 `background_job`，由 worker 认领执行；处理器内 `RunService.mark_running()` / `mark_success` / `mark_failed` 维护 run 状态。进程重启后 `recover_stuck_runs_on_startup()` 与 `get_job_queue().recover_stuck_jobs()` 分别恢复卡死的 run 与 job。
- **回测 checkpoint 提交（P7）**: `_run_backtest_loop` 每 100 天 flush+commit 一次（进度随 checkpoint 可见）；中途失败仅回滚当前未提交分段，已提交部分结果保留，失败信息通过 `BacktestRepository.find_latest_daily_date()` 附带"已保存部分结果至 {date}"。若未来实现回测重试，必须先清理该 backtest_id 的 daily/index 结果再重跑。
- **队列 lane 与并发预算（B1/B6）**: `JobQueue` 按 lane 认领任务——`backtest`/`comparison` 属回测 lane（`job_queue_backtest_workers`，默认 1，串行），其余全部属通用 lane（`job_queue_workers`，默认 2，认领时用 `exclude_job_types` 排除回测类型）。**不要把回测任务挪进通用 lane**：回测是 CPU+数据库混合任务，实测单进程内 4 线程并发比串行慢约 20 倍。需要更高吞吐时优先按 `QUANT_ETF_JOB_QUEUE_EMBEDDED=false` + `python -m quant_etf_api.worker` 拆进程，而不是加线程。
- **队列任务心跳与僵尸回收（B7）**: worker 执行期由 `_HeartbeatWorker` 每 `job_heartbeat_interval_seconds`（默认 15 秒）更新 `background_job.heartbeat_at`；`JobQueue` 的僵尸扫描线程把心跳超时（默认 1800 秒）或运行超过 `job_max_runtime_seconds`（默认 7200 秒）的任务回收为 pending（还有重试次数）或 failed，并调用 `JOB_ABANDON_HANDLERS[job_type]` 清理业务侧记录。**新增长任务类型时**：若它会在 `backtest_run` 之类的主表留下 running 记录，必须注册对应的 abandon 回调，否则回收后主表会永远停在 running。
- **回测协作取消（B2）**: 队列取消运行中的任务只是把 `background_job.cancel_requested` 置真；回测主循环每个交易日调用 `infra.job_queue.context.ensure_not_cancelled()`（取消标记有 5 秒 TTL 缓存）并在 checkpoint 抛出 `JobCancelledError`。`BacktestService.run_backtest` 必须**先**捕获 `JobCancelledError`（落 `cancelled` 状态并 re-raise），否则会被通用 `except Exception` 吞掉、把取消误记成失败。任何新增的长循环也应插入同类安全检查点。
- **回测任务去重键（B3）**: 回测入队统一用 `backtest_job_key(backtest_id)`（即 `backtest:{id}`）作为 `job_key`——既做幂等去重，也让 `BacktestSummary.queued_seconds/elapsed_seconds/queue_position` 能反查队列任务。新增回测入队点时不要自造键名。
- **稳健性批次收口（B2/B7）**: 批次内回测任务以 `robustness_id` 作为 `background_job.batch_id`（优化会话用 `optimization_id`），`robustness cancel/pause/resume` 与 `collect --allow-partial` 都依赖该批次号；`allow_partial` 汇总会把 `coverage`（expected/completed/pending/failed/is_partial）写进 summary 并把批次状态落为 `partial`，人工复核时**必须先看 coverage**再下结论。
- **稳健性汇总按共同窗口比较（F-1 已修）**: `_summarize` 只用"所有变体都有数据"的窗口求均值，并在 `summary.coverage` 给出 `common_windows` / `comparable`；`variants[].windows` 是参与比较的窗口数、`windows_available` 是该变体自己跑成功的窗口数。`comparable=false` 时**不要读 delta**。历史批次（2026-09-14 之前汇总的）仍可能是旧口径，重新 `collect` 一次即可按新口径覆盖。
- **消融变体标签带规则下标（F-2 已修）**: 标签形如 `ablate_filter_rules1_close_price`；`_create_variant_strategy` 命中既有变体时**先比对配置哈希**，不一致直接报错（不再静默复用）。`e72997a1` 这类历史批次里"两条 knob 不同但指标完全相同"的行是旧代码产物，不可用。
- **非交易日行情（F-7 已修）**: 摄取侧 `IngestService._drop_non_trading_bars` 按 `trading_calendar` 拦掉假期日期；回测侧 `_filter_non_trading_dates` 再兜一层并输出 `NON_TRADING_BAR` 警告。日历不可用时两侧都保持原行为（不静默改变历史口径）。库内仍留有 2018-06-18 的 7 条历史污染数据，回测已忽略。
- **预热期与缺失因子口径（F-6 已修）**: `_warmup_trading_days` 按**各指数自身首根 K 线**起算（单只后上市指数不再把整段回测标成预热）；`MISSING_FACTOR` 警告按指数排序给出"代码 N 天"明细。`EXECUTION_PRICE_MISSING`（F-8）单列"因缺开盘价被排除在候选池之外"的资产与天数——T+1 开盘执行下，配置 21 只指数在 2016-2024 实际只有 10~14 只可交易。
- **稳健性批次控制与并行（F-4/F-5/F-13）**: 批次行在创建时先落库（执行期即可见、可取消）；`robustness abandon <batch> [--reason]` 把长期 running 的批次显式收口（保留证据与试验台账）；`robustness list/show` 的 `is_stale` 提示疑似停滞；同步模式可用 `--parallel N` 走进程池本地并行（回测是 CPU+DB 混合任务，线程会被 GIL 限制），入队模式禁止该参数（并发由 worker 数决定）。`collect` 现在把"回测已删除"归入 `missing_windows`、执行失败归入 `failed_windows`。
- **PBO 分块下限（F-14）**: CSCV 分块数 <4 时 `statistics.pbo.value=null` 并给出 `reason`（2 个窗口只有 1 种对称切分，PBO 恒为 0，直接输出会被读成"没有过拟合"）。
- **research batch 摘要视图（F-9/F-10）**: `research batch --summary` 输出「变体 × 指标」排名表（毛/净年化、净夏普、换手、回撤、Δ、逐窗口 Δ）；完整 JSON 在几十个变体时会超输出上限。`caliber.aggregate_mode=window_stitched` 表明 aggregate 是窗口拼接结果，**不可与 `backtest show` 的整段数字直接比较**。
- **列表与会话的净口径（F-12/F-16）**: `GET /backtests`（CLI `backtest list`）默认现算净口径（`net_sharpe_ratio`/`net_annualized_return_pct`/`annualized_turnover`）；`optimization show` 同时返回 `acceptance_checklist`（7 项）与带净口径的 `metrics_full`/`metrics_folds`；`optimization start` 的 `--start` 缺省为**研究期起点**（F-18），`finish --promote` 会把版本历史追加进策略描述（F-17）。
- **数据刷新按类型拆分**: `IngestService` 提供 `refresh_index_data()`、`refresh_macro_data()` 两个公共方法，各有独立 run 生命周期。对应 API 端点：`POST /runs/index-refresh`、`/runs/macro-refresh`。各数据页面（指数/宏观）有自己的"刷新数据"按钮，RunsPage 纯做监控。
- **Run detail API**: `GET /runs/{run_id}` 返回 `ResearchRunDetail`（含 metrics、duration_seconds），`GET /runs/{run_id}/items` 返回 `ResearchRunItemSchema` 逐条明细，`POST /runs/{run_id}/retry` 重试失败任务（创建新 run 并入队对应后台任务）。

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
- **交易日历严格口径与调用点分级（C1）**: 交易日历只接受**真实日历**（上游 Tushare/AkShare 或本地 `trading_calendar` 表快照），`WeekendFallbackCalendar` 已彻底删除。解析入口是 `infra.trading_calendar.resolve_trading_calendar(db, required_range=...)`；"只想探测上游是否可用"用 `TradingCalendar().get_trading_days_set()`（返回 `None` 不抛错），"必须有日历"用 `TradingCalendar().require_trading_days()`（不可用时抛 `TradingCalendarUnavailableError`）。调用点按三类语义处理：① 后台任务/CLI **上抛落 failed**（调用方已有 `mark_failed`/`_fail` 兜底）；② daemon 调度线程 **catch + 记 error + 跳过本轮**（不产出错误结果、不拖死线程）；③ HTTP 读端点返回 **503**（全局异常处理器，`GET /system/data-quality`、`GET /ai-factors/previous-trading-day`、`GET /strategies/{id}/allocation` 等；显式传 `trade_date` 可绕过日历）。回测侧：周度/月度调仓解析失败 → 回测落 `failed`；每日调仓与日历无关，指纹记 `not_required`。来源写 `params["_calendar_source"]`（upstream/database/not_required）并透出为 `stability.calendar_source`，可用 `GET /backtests?calendar_source=` / `cli backtest list --calendar-source` 审计。回归测试：`tests/unit/test_trading_calendar_strict.py`、`test_rebalance_calendar.py`、`test_calendar_http_errors.py`。
- **rebalance.py 交易日历必填**: `DefaultRebalanceScheduler(trading_calendar)` 的日历是**必填位置参数**，未注入直接 `ValueError`；`_is_nearest_in_window` 不再有"日历异常 → 按星期比较"的降级分支。`BacktestService._run_backtest_loop` 在循环开始前按策略频率解析并注入日历（`_resolve_rebalance_calendar`），`StrategyDecisionService` 同样注入。
- **回测口径指纹（C1/C2）**: 口径相关信息统一放在 `backtest_run.params` 的 `_` 前缀键：`_execution_model`/`_data_quality_mode`/`_benchmark_index_code`/`_cost_bps`（创建时写入）、`_calendar_source`（执行前解析后立即提交，即使回测随后失败也留痕）、`_turnover_model`（创建时写入）。`BacktestStability` 把 `calendar_source` / `turnover_model` 与 `cost_ladder` / `candidate_pool` 一并返回，前端在详情页展示"口径指纹"。
- **净口径多档成本在读取路径现算（C3）**: `stability.cost_ladder` 由 `domain/research/stability.py::compute_cost_ladder` 计算（只重算与成本有关的量，O(档位×交易日)），档位来自 `QUANT_ETF_STABILITY_COST_LADDER`（默认 `[0,10,20,30,50]`，0=毛口径）。`GET /backtests/{id}?cost_bps=`、`cli backtest show --cost-bps/--cost-ladder` 只覆盖展示口径，**不落库、不重跑**。
- **回测跨度不受限制（C4）**: 研究期内任意跨度（1 个月 ~ 研究期全段 10 年）都可单次回测，**不存在**跨度上限、也**不再要求**"> 5 年必须分段"。时间分段只是可选的观察视角（分年度绩效 + 三段一致性）。研究/验证期边界规则不变：研究类回测不得越过 2025-12-31，跨界需 `purpose=validation`。回归测试：`tests/unit/test_backtest_span.py`。
- **回测删除的引用完整性（C5）**: 迁移 `0048` 后 `backtest_daily_result`/`backtest_index_result`/`backtest_comparison` 对 `backtest_run` 是 `ON DELETE CASCADE`，`strategy_optimization.{baseline,candidate}_backtest_id` 与 `strategy_lifecycle.{research,validation}_backtest_id` 是 `ON DELETE SET NULL`。**JSONB 引用无法加外键**（`robustness_run.variants[*].backtest_ids`、`strategy_optimization.fold_backtests[*].*`），只能靠 `GET /backtests/orphans`（`cli backtest orphans`）审计、`cli backtest prune-dangling-refs [--apply]`（默认预演）清理。`robustness collect` 必须把"回测已删除"计入 `coverage.missing_windows` 而不是 `pending`，否则批次会永远停在 running。删除接口：`DELETE /backtests/{id}?force=` / `cli backtest delete <id> [--force]`（运行中先取消；存在 JSONB 引用默认 409）。
- **回测有效候选池时间线（C6）**: 主循环对逐日候选池规模做游程编码，随 `mark_success` 写入 `backtest_run.candidate_pool`（迁移 `0047`），读取路径透出为 `stability.candidate_pool`（含 `base_size/min_size/median_size/pool_coverage_ratio/segments/exclusions/truncated_exclusions`），池缩水时追加 `CANDIDATE_POOL_SHRINK` 信息级告警。剔除明细条数上限由 `QUANT_ETF_CANDIDATE_POOL_EXCLUSION_LIMIT`（默认 50）控制；CLI 用 `backtest pool <id>` 查看。
- **研究批量评估不落库（D1）**: `cli research batch`（`services/research_batch_service.py`）复用 `BacktestService._run_backtest_loop(persist=False)` **同一条执行路径**，只关闭落库/提交/进度，并按窗口共享行情快照与因子预计算（`BacktestRunCaches`）。因此它的指标口径与落库回测一致（回归测试 `test_metrics_match_persisted_path` 锁定逐日收益/换手/仓位相等），但**不构成验收凭证**——输出里 `caliber.persisted=false`，验收一律走 `backtest run`。变体文件支持完整 `config` 或 `patch`（路径支持 `filters.rules[1].value` 列表下标），未知路径/未知字段直接报错而不是静默跳过。改主循环时不要破坏 `persist=False` 分支与缓存键（窗口 + 所需因子 + 参与计算的指数）。
- **稳健性扫描口径（D2）**: `robustness scan` 的旋钮截断按**业务重要性**（`_KNOB_PRIORITY`：择时阈值 → 过滤阈值 → 评分权重 → 风险/仓位 → 调仓），不再按路径字母序；支持 `--preset quick`（2 窗口/8 旋钮的轻量体检，写进 strategy-optimizer 验收清单第 6 项）、`--preset standard`、`--knobs a,b`、`--knobs-file f.json`；实际扫描口径落在 `robustness_run.scan_params`（前端批次详情可见）。改动 `_KNOB_PRIORITY` 或预设会改变"同一批次扫了什么"，属于口径变更。
- **变体策略清理与试验台账（D4）**: 稳健性变体草稿带 `strategy_config.is_variant` + `source_batch_id`（迁移 `0049` 按 description 回填存量），清理用 `cli strategy prune-variants --batch <批次> [--apply --force]`（默认预演；仍被回测引用时跳过，`--force` 才连带删回测）。**不要为了清理变体而删除 `robustness_run` 批次行**：它是 Deflated Sharpe 的"试验次数 N"台账。
- **验证期消费留痕（D5）**: `strategy_config.validation_consumed_at/note` 记录"该策略的验证期（2026-01-01 起）数据已被消费"；创建 `purpose=validation|monitor` 回测时自动首次留痕（再次命中只追加说明），人工可用 `cli strategy consume-validation <策略> --note "..."`。`GET /backtests/validation-usage` 每条记录并列输出策略级留痕字段。验证期数据只能用于否决，不能作为确认依据。
- **StrategyConfig.index_codes**: 存储在 `config_json` 内部（非独立 DB 列），通过 `**row.config_json` 展开到 engine 的 `StrategyConfig` 模型。前端 API 请求中 `index_codes` 应在 `config_json` 内传递，非顶层字段。非空时 `_filter_by_scope()` 仅保留指定指数（实时和回测模式均生效）。
- **index_codes 回测强制应用**: `BacktestService.create_backtest()` 检查策略的 `config.index_codes`，非空时强制覆盖 `universe_filter` 为 subset 模式；`ContextBuilder._build_backtest()` 对传入的 index_codes 做交集过滤（双重保护）。
- **StrategyConfigForm 与 engine/config.py 的 StrategyConfig 同步**: 引擎新增配置模块时，需同步更新 `StrategyConfigForm.vue`（表单）、`StrategyDetailPage.vue`（详情展示）。目前已覆盖全部 7 个模块（score/timing/filters/rank/portfolio/risk/rebalance）+ 资产范围 index_codes。
- **`StrategySummary` 已有 `index_codes` 顶层字段**: 列表 API 直接返回 `index_codes`，前端无需额外调用 `fetchStrategyDetail()`。`StrategyConfigForm.vue` 读取 `modelValue.index_codes`（config_json 内），`StrategyDetailPage.vue` 和 `BacktestCreatePage.vue` 读取顶层 `store.current?.index_codes`。
- **index_daily_bar OHLC 字段**: `IndexDailyBarModel` 有 `open_price`、`high_price`、`low_price`、`close_price` 字段，技术指标因子（ATR/Donchian）通过 `ctx.index_bars` 直接访问。
- **`get_default_factor_registry()` vs `build_default_factor_registry()`**: 进程级单例通过 `get_default_factor_registry()` 获取（首次构建后缓存），避免 `BacktestService` 每请求重建。只有 `cli.py` 和 `registry.py` 内部使用 `build_default_factor_registry()`。
- **`BatchFactorComputer` Protocol**: 定义在 `factors/base.py`，回测因子预计算时优先调用 `compute_batch()`（一次遍历 bar 数据覆盖所有日期）。已在 momentum.py（return_5d/20d/60d/120d）实现。新增回测频繁使用的因子时建议实现此协议。
- **配置校验含因子 ID 校验（P4）**: `StrategyConfigService.validate_config()` / `validate_parsed()` 依赖 DB 会话（查询 `factor_definition` active 集）与进程级因子注册表，不再是无状态静态方法。未知因子、停用/未同步因子、未知变换函数均进入 errors 快速失败。`run_allocation` 与 `create_backtest`/`create_comparison` 在运行期复用该校验（422），星标摘要聚合接口跳过坏策略并记日志。
- **AI 分析双调度器**: 数据摄取在 `schedule_time`（默认 17:30）执行、AI 舆情分析在 `ai_schedule_time`（默认 23:30）执行；两个调度器均为纯定时器，只入队任务。摄取完成后由 `handle_daily_ingest` 自动入队当日 `factor_computation`。`ai_analysis_enabled=False` 时 AI 调度器不启动。
- **AI 因子（已删除）**: 6 个 AI 因子（ai_sentiment_1d/5d/divergence、ai_attention_1d/5d、ai_topic_momentum）已从策略引擎移除（AI 情绪分析功能不完善）。策略配置引用这些因子会被校验拒绝。AI 舆情分析（新闻采集/情绪聚合/市场研判）作为独立展示功能保留。
- **关键词标签可配置化**: `keyword_tag_config` 表存储关键词→资产标签映射，替代硬编码的 `classifier._KEYWORD_TAG_MAP`。`TagClassifier._classify_via_keyword()` 优先使用 DB 映射，回退到静态默认值。CRUD 端点: `GET/POST/PUT/DELETE /keyword-tags`。
- **市场综合研判**: `market_synthesis` 表存储每日 AI 生成的市场概况（200-300 字中文研判）。在 `AIFactorService.run_full_pipeline()` 步骤 7 自动生成，LLM 不可用时静默跳过。API: `GET /ai-factors/synthesis/{date}`。
- **前端 Toast 弹窗体系**: 自研零依赖（`stores/toast.ts` + `components/ToastHost.vue`），三态 info/warning/error，带 key 会话内去重。`api/client.ts` 拦截器只对主动操作（POST/PUT/PATCH/DELETE）自动弹错误，GET 保持页面内联状态；单请求可用配置 `{ toast: false }` 关闭（如 AI 舆情页已有自有提示）。`usePolling` 支持 `onMaxErrors` 回调（连续失败停止时弹一次），回测轮询按 warnings key 去重弹提示；`useRunSkipToast` 监听用户触发的运行任务，skipped（holiday/concurrent）时弹信息/警告。
