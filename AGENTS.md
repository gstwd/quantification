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
- 数据质量唯一口径为 `data_health_snapshot`。外部数据同步、检查、补缺口和重拉统一从 `POST /api/data-management/operations` 发起；不要新增平行质量规则或旧同步入口，GET 查询未命中只返回空，不得入队补数。
- 外部客户端只负责拉取、归一化和幂等写入；编排放在 `DataManagementService`。上游失败不得覆盖已有有效数据。
- 日频市场指标（10 年国债、两融、股指期货主力与基差、股指期权 PCR）入 `market_indicator`（数据集键同名，交易日新鲜度），因子计算时与宏观指标并入同一时点映射（交易日当日可见）；月度宏观新指标须登记 `factors/macro_period._MONTHLY_RELEASE_DAY` 发布滞后与 `domain/market_data/indicators.MACRO_INDICATOR_CODES`，否则实时路径不加载或产生前视。

### 策略、因子与回测

- 策略配置存于 `strategy_config.config_json`，以 `StrategyConfig` 校验。新增配置能力时，同步更新 schema、服务校验、`StrategyConfigForm.vue`、详情展示、API 类型和测试。
- `StrategyConfigForm.vue` 从表单状态重建 `config_json`：表单不渲染的字段（嵌套子规则组、`regime_rules` 等顶层字段、仅被这些字段引用的因子别名）由 `preserveUnmanaged` 与不透明的 `groups` 原样保留。新增配置字段时要么在表单中渲染，要么确认它会被保留，不得让一次表单保存静默丢掉配置。
- 因子值**不落库**。一个计算逻辑只建一个模板，周期/阈值等用 `parameter_schema` 表达；同模板同参数在一次执行中复用。不要恢复 `index_factor_value`、因子值补算队列或缓存失效链路。
- 派生因子用 `expr` 模板在 `factor_aliases` 中声明（安全表达式白名单见 `factors/expression.py`，含时序函数 delay/delta/ts_*；含时序函数时 `compute_matrix` 向前扩展求值轴、实例回望 = 依赖回望 + 窗口，超过回测预加载在校验期拒绝；不提供截面函数），依赖在注册表解析时绑定、由 `FactorComputeService.compute_matrix` 先算依赖再求值；`FactorProvider.resolve_instances` 一并返回依赖（`with_dependencies`），面板、估值预加载、全市场行情与口径指纹都按依赖计算。派生因子进入 `research batch` 变体或 `optimization start` 候选前必须有诊断记录（`research factor-ic|buckets` 写入台账 `kind=diagnostic`，按与别名无关的指纹匹配）；不得绕过该门槛。
- `factor_aliases` 是“别名 → 模板 + 参数”；未声明引用名时按模板 ID 和默认参数解析。新增模板后运行 `init-factors` 同步目录。
- 实时 `ContextBuilder` 只读；因子数据不足或计算失败必须结构化呈现，不能在 GET 链路写库或入队。
- 路径依赖功能（`rank.buffer_n`、`rebalance.band`、`risk.vol_target|drawdown_stop|trailing_stop`）只在回测主循环合成当日目标后由 `domain/strategies/risk_overlay.py` 施加，缺省 None 时口径逐位不变；实时决策按 `StrategyConfig.needs_path_state` 用 `persist=False` 主循环回放重建状态，不得在引擎里另写一套路径状态。
- 组合策略（`config_json.kind=composite`）引擎配置解析为 None，回测快照必须内嵌成员快照与哈希（`build_composite_snapshot`）；稳健性、优化、研究批量、信号诊断与战役模式拒绝组合策略（`require_single` / 战役守卫）。
- 回测要求 `portfolio`。创建时快照配置和哈希，执行时优先使用快照；不要改写历史回测所依据的配置。
- 研究期固定为 `2016-01-01` 至 `2025-12-31`；`2026-01-01` 起是验证期。研究用途不得跨界；validation/monitor 可以使用但必须留痕，且不能反向用来调参。
- 研究与优化统一按 `t_plus_1_close` 口径：CLI 的 `backtest run`、`research batch`、`research buckets`（`--basis`）、`robustness scan|ablate|pool` 与 `optimization start` 缺省都是 `t_plus_1_close`（`RESEARCH_DEFAULT_EXECUTION_MODEL`）。系统级缺省（API 请求体、存量回测 / 会话行缺失执行模型时的解释）仍为 `t_plus_1_open`（`DEFAULT_EXECUTION_MODEL`），不得改动以免改变历史记录的口径含义。不同执行模型、日历来源或候选池口径的结果不可直接比较。
- T+1 收盘下先成交昨日指令，再以成交后持仓合成今日双腿目标；仅风险腿不得恢复旧成分，明确清仓与无指令必须区分。
- 回测记账口径为 `drift_v1`（`params._accounting_model`，见 `domain/portfolio/execution.py`）：持仓在调仓间随行情漂移、换手按漂移后的实际持仓计；T+1 开盘调仓日隔夜段与日内段复利衔接；收益结算用前向填充行情（缺口后补回涨跌，候选池/因子/缺口统计仍用原始行情）；最后一个交易日无收益窗口、不落逐日行。存量回测按 `constant_mix_v0` 标注，两种口径不可直接比较。
- 回测 `params` 是普通 JSON 列，不追踪原地修改：写口径元数据必须拷贝成新 dict 后整体赋值。
- 研究台账：假设记在 `research_hypothesis`（先登记判定标准再检验，关闭后不再接收试验），检验动作记在 `research_trial`。Deflated Sharpe 的试验次数 N = 稳健性批次变体 + 已评估优化会话 + `research batch` 变体（每次运行自动记录）+ 人工 / 补录试验；不得绕过台账统计 N。N 统计失败时必须失败即不通过（异常上抛、验收项判不通过），不得退回兜底值放行。
- 研究线：`strategy_config.lineage_id` 把基线与其优化候选、稳健性变体、手工归入的派生策略归为一组；N、试验预算（`research_trial_budget`）与验证期消费留痕按研究线汇总。派生新策略时必须归入原研究线，不得用另建策略的方式重置 N；仍有成员的研究线根、以及自身已有试验的成员不能改归其他研究线（试验按策略归属，移出等于拿走 N）。
- 优化验收清单共 10 项（含 Deflated Sharpe、分年度一致性、差异事件数）；第 8 项 Deflated Sharpe 检验的是「候选 − 基线」逐日超额净收益，不是候选绝对夏普；门槛在 `config/settings.py` 的 `acceptance_*`，第 8 项门槛的假阳性率与功效用只读的 `research dsr-calibrate` 校准，调整门槛由人决定；`--no-strict` 接受必须给理由并写入研究台账。
- 回测汇总是毛收益；成本阶梯在读取路径按换手率折算，不重跑、不篡改历史结果。`cost_bps` 是按单边换手折算的往返交易成本。
- 每日数据同步完成后入队 `lifecycle_refresh_all`（`lifecycle_auto_refresh_enabled` 控制）：只刷新 LIVE 策略健康快照（`trigger=scheduled`），等级恶化 / 刷新失败 / 同步失败写站内告警 `system_alert`（按 `dedupe_key` 去重），每条策略只保留最近 `lifecycle_scheduled_keep` 条定时监控回测（手动刷新的不清理）；告警不触发任何调参、状态变更或交易动作。
- LIVE 策略禁止直接更新 `config_json`；生命周期状态仅人工变更，系统不得自动调参、自动 promote 或自动上线。

### 研究战役（自动研究循环）

- `research campaign run` 逐步启动全新的 headless Claude Code 会话（研究员 / 评审员交替），状态记在 `research_campaign` / `research_campaign_step`，战役内假设带 `campaign_id`。规则在 `domain/research/campaign.py`，驱动器在 `services/campaign_driver.py`，角色手册在 `.claude/skills/research-campaign/`（只支持 Claude Code）。
- 驱动器给 agent 进程设置 `QUANT_CAMPAIGN_ID`，CLI 据此进入战役模式（`_guard_campaign_mode`）：只放行研究所需命令；拒绝 `--promote`、`--no-strict`、`--over-budget`、生命周期、验证期消费、非 research 用途回测、研究线外策略 / 批次 / 回测、其他战役的假设、改为 active、改归研究线、修改基线或启用策略、验收门槛校准（`research dsr-calibrate`）；`strategy create` 必须 `--draft --lineage <战役研究线成员>`；会写文件的命令（`optimization report`、`research campaign report`）只能写到 `candidates/<战役>/`；诊断命令（`research factor-ic|buckets|signal-lab`）按战役 `max_diagnostics` 限次（放行即计数）。新增 CLI 命令时必须同步战役模式白名单与测试。
- 驱动器以 `--setting-sources user --strict-mcp-config` 启动 agent，不合并仓库 `.claude/settings*.json`；权限只放行平台 CLI、白名单路径读取（AGENTS/README/docs/技能/源码/本战役目录，不含 `candidates/` 下其他目录）与本战役目录写入。超时或中断时结束 agent 进程树并把步骤收口为失败。
- 桌面编排模式（不需要 CLI 登录）：Claude Code 桌面会话作为编排者，`research campaign begin-step` 登记步骤并记下自身 `CLAUDE_CODE_SESSION_ID`（`research_campaign_step.orchestrator_session_id`），每步新开桌面会话执行，结束后 `finalize-step` 收口；流程见技能「桌面编排模式」。步骤运行期间除编排会话外的所有 CLI 调用都按数据库进入战役模式（`ResearchCampaignService.resolve_mode`，判定失败即拒绝执行）；子会话不能 `begin-step|finalize-step|stop`，不在 Claude Code 会话里的终端可以 `stop` 收口。CLI 之外的边界只能靠提示约束，长时间无人值守用 headless 驱动器。
- `Settings` 总是先读 `apps/api/.env`（再读工作目录 `.env`），从仓库根目录运行 CLI 也连同一个数据库。
- 战役内接受优化会话的唯一路径：评审员 `research campaign review --verdict recommend_accept` 后，以严格模式 `optimization finish --verdict accept`（不 promote）。promote、另建策略与上线仍由人工执行。
- 停止条件（试验增量、研究线预算、时长、轮数、连续否决、连续失败）由平台判定；同一时间只允许一个战役有运行中步骤。

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
