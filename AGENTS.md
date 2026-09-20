# AI 开发协作指南

本文件是本仓库所有 AI 编程助手的唯一项目级指令来源。`CLAUDE.md` 只作入口并要求读取本文件；不要在多个助手文件复制架构事实。

## 先读什么

1. 修改代码前必须阅读 [docs/CODING_STANDARDS.md](docs/CODING_STANDARDS.md)。
2. 以当前代码、Pydantic schema、Alembic 迁移和测试为事实来源；README 和架构文档用于导航，不替代运行时校验。
3. 修改策略、因子、数据管理或回测前，先读对应模块及其测试；必要时再读 `docs/architecture/` 的专题规范。

## 项目边界

- 系统研究 **A 股指数**，仅支持日频；不研究 ETF，不做个股选股、交易执行、账户管理或实盘风控。
- 策略资产只能是用户维护、且有实际 ETF 对应物的 `benchmark_index` 指数。申万行业和个股只可作为研究及指数级因子的内部输入，不能进入 `index_codes` 或作为回测标的。
- `quant_etf_api` 包名、环境变量和部分数据库历史名称是兼容遗留，不能据此恢复 ETF 功能。
- 本系统只输出研究与配置结果；不得把回测或 AI 舆情描述为投资建议、实盘信号或自动交易决策。

## 常用命令

### 后端

```bash
cd apps/api
pip install -e ".[dev]"
alembic upgrade head
python -m quant_etf_api.cli init-factors
uvicorn quant_etf_api.main:app --reload --port 8000
pytest
ruff check .
ruff format .
```

Windows 虚拟环境可执行文件在 `apps/api/.venv/Scripts/`。

### 前端

```bash
cd apps/web
npm install
npm run dev
npm run build
npm run lint
npx vue-tsc --noEmit
```

### 数据库与 CLI

```bash
cd apps/api
alembic revision --autogenerate -m "description"
alembic upgrade head
python -m quant_etf_api.cli strategy list
python -m quant_etf_api.cli backtest run --strategy <id>
python -m quant_etf_api.cli robustness scan --strategy <id> --preset quick
```

使用 CLI 或 API 的完整参数和默认值前先运行 `--help` 或读取当前 schema；不要从旧文档猜参数。

## 架构与职责

```text
HTTP → api/routers → services → engine
                           ├→ factors       # 模板解析与原始数据现算
                           ├→ domain        # 纯业务规则
                           ├→ infra/db      # ORM、repository、迁移
                           └→ infra/job_queue
```

- `api/routers/`：只做 HTTP 参数、依赖注入和响应转换；服务异常映射为语义化 HTTP 状态码。
- `services/`：编排 repository、客户端、领域规则、事务和后台任务；不复制领域计算。
- `domain/`：纯规则，不得导入 SQLAlchemy、FastAPI 或服务层。
- `engine/`：配置驱动策略管线 `Timing → Score → Filter → Rank → Portfolio → Risk`。
- `factors/`：`FactorTemplateRegistry` 解析模板和别名，`FactorComputeService` 由原始数据现算因子值。
- `infra/db/repositories/`：承载数据库查询；服务层避免散落直接查询。
- `infra/job_queue/`：全部后台任务的持久化队列；调度器只负责入队。

详细口径见 [策略架构](docs/architecture/指数量化系统策略层架构设计.md)、[数据管理规范](docs/architecture/数据管理与外部数据接入规范.md) 和 [稳健性/生命周期说明](docs/architecture/策略稳健性评估与生命周期监控实施说明.md)。

## 关键不变量

### 时间、日历与数据质量

- 时间戳使用 UTC；新增时间戳列使用 `DateTime(timezone=True)` 与 `utcnow_aware()`。交易日和业务日期使用 `Asia/Shanghai` 与 `today_cn()`；前端必须用 `src/utils/date.ts`。
- 交易日历必须是真实上游日历或本地 `trading_calendar` 快照。不可用时抛 `TradingCalendarUnavailableError`，HTTP 返回 503；禁止按周末近似。
- 数据质量唯一口径为 `data_health_snapshot`。外部数据同步、检查、补缺口和重拉统一从 `POST /api/data-management/operations` 发起；不要新增平行质量规则或旧同步入口。
- 外部客户端只负责拉取、归一化和幂等写入；编排放在 `DataManagementService`。上游失败不得覆盖已有有效数据。

### 策略、因子与回测

- 策略配置存于 `strategy_config.config_json`，以 `StrategyConfig` 校验。新增配置能力时，同步更新 schema、服务校验、`StrategyConfigForm.vue`、详情展示、API 类型和测试。
- 因子值**不落库**。一个计算逻辑只建一个模板，周期/阈值等用 `parameter_schema` 表达；同模板同参数在一次执行中复用。不要恢复 `index_factor_value`、因子值补算队列或缓存失效链路。
- `factor_aliases` 是“别名 → 模板 + 参数”；未声明引用名时按模板 ID 和默认参数解析。新增模板后运行 `init-factors` 同步目录。
- 实时 `ContextBuilder` 只读；因子数据不足或计算失败必须结构化呈现，不能在 GET 链路写库或入队。
- 回测要求 `portfolio`。创建时快照配置和哈希，执行时优先使用快照；不要改写历史回测所依据的配置。
- 研究期固定为 `2016-01-01` 至 `2025-12-31`；`2026-01-01` 起是验证期。研究用途不得跨界；validation/monitor 可以使用但必须留痕，且不能反向用来调参。
- 默认执行模型为 `t_plus_1_open`，可选 `t_plus_1_close`；不同执行模型、日历来源或候选池口径的结果不可直接比较。
- 回测汇总是毛收益；成本阶梯在读取路径按换手率折算，不重跑、不篡改历史结果。
- LIVE 策略禁止直接更新 `config_json`；生命周期状态仅人工变更，系统不得自动调参、自动 promote 或自动上线。

### AI 舆情模块

- AI 舆情是独立展示与研究辅助功能，不是策略因子；`ai_sentiment_*`、`ai_attention_*` 和 `ai_topic_momentum` 已从策略引擎移除，配置引用必须被拒绝。
- 链路为新闻采集 → 情绪分析 → 标签分类 → 按标签聚合 → 可选市场综合研判。持久化表为 `news_item`、`ai_sentiment_result`、`daily_sentiment_aggregate`、`market_synthesis`。
- AI 调度器只入队；`ai_analysis_enabled=False` 时不得启动。LLM 不可用时保留可用的采集/聚合结果，并将研判视为缺失，不能伪造成功结果。
- `keyword_tag_config` 是关键词到资产标签的可配置映射，DB 映射优先于静态默认值。修改标签语义须同步前端说明与测试。

## 编码与测试要求

- 所有注释、docstring 和前端 JSDoc 使用中文；修改逻辑时同步更新说明，避免遗留描述旧行为。
- Python 使用 Python 3.10+ 类型写法，公开函数和私有辅助函数都标注类型；有 `dict[str, Any]` 时显式导入 `Any`。
- TypeScript 禁止 `any`，不确定值使用 `unknown` 后收窄；接口类型放在 `src/types/api.ts`，纯类型使用 `import type`。
- 新增或修改后端行为时，在相应 `tests/unit/` 或 `tests/integration/` 添加回归测试。外部 HTTP 可 mock；不要为方便而跳过关键日历、时期或回测口径测试。
- 长任务状态轮询统一用 `apps/web/src/composables/usePolling.ts`；页面和 store 不得手写 `setInterval` 轮询。
- 修改 API 时同步 router、schema、前端 API wrapper、类型和页面错误态。GET 读路径应保持只读。

## 变更卫生

- 开始前先看 `git status`；工作树可能包含用户未提交变更，绝不重置、覆盖或格式化无关文件。
- 不执行 `git reset --hard`、无授权的删除或大范围格式化。迁移和数据库破坏性操作必须先说明影响。
- 文档只保留当前规则和可执行入口；完成的排障过程不要作为现状规范。架构或运行口径变更时同步 README、相关专题文档和本文件。
- 策略构建、优化和研究工作遵循 `.agents/skills/`（Codex）或 `.claude/skills/`（Claude）中的对应技能；共同约束以本文件和当前 schema/CLI 为准，修改任一同名技能时应同步其关键边界与触发条件。
