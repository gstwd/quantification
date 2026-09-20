# A 股指数日频量化研究平台

面向 A 股指数的日频研究、策略配置和历史回测系统。后端为 FastAPI + PostgreSQL，前端为 Vue 3；策略由 JSON 配置驱动，不提供交易执行、账户管理或 ETF 数据能力。

> 系统只以 `benchmark_index` 中由用户维护、且有实际 ETF 对应物的 A 股指数作为策略资产。申万行业与个股数据只可作为研究和指数级因子的内部输入，不能成为策略资产或回测标的。

## 当前能力

- 配置化策略引擎：择时、评分、过滤、排序、调仓、仓位分配和风险约束。
- 参数化因子：32 个内置模板；策略可用 `factor_aliases` 将同一模板以不同参数重复引用。因子值按请求或任务从原始数据现算，不持久化。
- 可审计回测：策略配置快照、交易日历来源、执行模型、候选池、换手和基准等口径均随结果保存。
- 研究治理：研究期固定为 2016-01-01 至 2025-12-31，2026-01-01 起为验证期；稳健性扫描、优化会话与验证期消费留痕均受该边界约束。
- 生命周期监控：LIVE/SUSPENDED/RETIRED 仅人工变更；系统提供健康诊断，不自动调参或自动升级策略。
- 数据管理：非新闻外部数据通过统一数据管理入口、持久化任务队列和 `data_health_snapshot` 维护。

## 架构

```text
HTTP → api/routers → services → engine
                           ├→ factors        （模板解析与原始数据现算）
                           ├→ domain         （纯业务规则）
                           ├→ infra/db       （ORM 与 repositories）
                           └→ infra/job_queue（持久化后台任务）
```

策略管线为：

```text
[可选] Timing → Score → [可选] Filter → Rank → [可选] Portfolio → [可选] Risk
```

回测中由 Rebalance 控制选股和风险调整的执行日；默认执行模型为 `t_plus_1_open`（T 日信号、T+1 开盘成交），也可显式选择 `t_plus_1_close`。不同执行模型的结果不可直接比较。

## 数据与因子

原始数据包括交易日历、指数日线/估值/成分、宏观、申万行业和个股基础数据。数据管理的质量结论只有一个来源：`data_health_snapshot`；首次部署应在“数据管理”页主动执行检查或同步。

`factor_definition` 是模板元数据目录，而非因子值表。每个模板声明版本、所需原始数据、默认参数、参数模式和回望窗口。运行时由 `FactorTemplateRegistry` 解析策略引用，`FactorComputeService` 按规范化参数批量计算；相同模板与参数在同一执行中只计算一次。

## 主要接口与页面

所有 API 使用 `/api` 前缀，完整契约以运行中的 Swagger 为准：`http://localhost:8000/docs`。

| 范围 | 页面与 API |
| --- | --- |
| 数据维护 | `/data-management`、`/indexes`、`/industries`、`/stocks`、`/macro` |
| 策略与因子 | `/strategies`、`/factors`、`/lifecycle`、`/tools/rrg-lab` |
| 回测与研究 | 页面 `/backtests`；API `/api/robustness`、`/api/queue`；CLI 提供批量研究和优化命令 |
| 运营辅助 | `/runs`、`/ai-factors`、`/keyword-tags` |

## 快速启动

### 后端

```bash
cd apps/api
python -m venv .venv
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，填写 PostgreSQL 连接串
alembic upgrade head
python -m quant_etf_api.cli init-factors
uvicorn quant_etf_api.main:app --reload --port 8000
```

### 前端

```bash
cd apps/web
npm install
npm run dev
```

### 检查

```bash
cd apps/api
pytest
ruff check .
```

```bash
cd apps/web
npx vue-tsc --noEmit
```

## 常用 CLI

```bash
python -m quant_etf_api.cli strategy list
python -m quant_etf_api.cli backtest run --strategy <id>
python -m quant_etf_api.cli backtest show <id> --cost-ladder 0,10,20,30,50
python -m quant_etf_api.cli robustness scan --strategy <id> --preset quick
python -m quant_etf_api.cli optimization start --strategy <id> --candidate-file candidates.json --hypothesis "..."
```

默认回测和优化使用研究期边界；如需使用 2026 年起的数据，须明确以 validation 或 monitor 用途运行，系统会留痕。

## 文档导航

- [策略架构与运行口径](docs/architecture/指数量化系统策略层架构设计.md)
- [参数化因子与参数高原治理](docs/architecture/参数化因子与参数高原治理升级方案.md)
- [因子研发与集成指引](docs/architecture/因子研发与集成指引.md)
- [数据管理与外部数据接入规范](docs/architecture/数据管理与外部数据接入规范.md)
- [稳健性评估与生命周期监控](docs/architecture/策略稳健性评估与生命周期监控实施说明.md)
- [用户手册](docs/用户手册.md)
- [开发约定](AGENTS.md)

方法论文件解释研究原则，不替代接口或运行口径：

- [个人量化策略：过拟合与因子研究方法论](docs/architecture/个人量化策略_过拟合与因子研究方法论.md)
- [量化策略生命周期管理方法](docs/architecture/策略生命周期管理方法.md)
