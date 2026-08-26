# A股指数日频量化研究平台

A-share index daily-frequency quantitative research platform — 后端 FastAPI + PostgreSQL，前端 Vue 3，策略以配置驱动方式接入。

> **系统范围说明：本系统仅研究 A 股指数（宽基/行业指数），不研究 ETF。** 系统不提供任何 ETF 数据源、ETF 数据表、ETF 接口或 ETF 前端页面。

---

## 项目定位

| 维度 | 说明 |
|------|------|
| 资产范围 | 仅 A 股指数（宽基/行业指数），不涉及 ETF 与个股 |
| 频率 | 日频，不做日内或实时 |
| 用途 | 研究平台，不做交易执行、账户管理、实盘风控 |
| 数据 | 通过公开 API（AkShare）抓取，统一存入 PostgreSQL |
| 策略 | 多策略配置化，每个策略独立配置、独立运行 |
| 前端 | Vue 3 研究工作台，展示指数信号、因子、运行记录 |

---

## 目录结构

```
quantification/
├── apps/
│   ├── api/                    # FastAPI 后端
│   │   ├── pyproject.toml      # Python 依赖与项目配置
│   │   ├── alembic.ini         # 数据库迁移配置
│   │   ├── alembic/            # 迁移脚本目录
│   │   ├── .env.example        # 环境变量模板
│   │   ├── src/quant_etf_api/  # 主包（历史命名保留）
│   │   └── tests/              # 单元测试
│   └── web/                    # Vue 3 前端
│       ├── package.json
│       └── src/
│           ├── main.ts         # 应用入口
│           ├── App.vue         # 根组件（含侧边栏导航）
│           ├── router/         # Vue Router 路由配置
│           ├── stores/         # Pinia 状态管理
│           ├── api/            # Axios API 调用封装
│           ├── pages/          # 页面组件
│           ├── components/     # 可复用组件
│           └── types/          # TypeScript 类型定义
├── docs/                       # 文档
└── deployment/                 # 部署脚本与配置
```

---

## 后端架构

### 技术栈

| 库 | 版本约束 | 用途 |
|----|---------|------|
| **FastAPI** | `>=0.115` | Web 框架，自动生成 OpenAPI 文档 |
| **Uvicorn** | `>=0.30` | ASGI 服务器 |
| **SQLAlchemy 2** | `>=2.0` | ORM，定义数据库模型，执行查询 |
| **psycopg 3** | `>=3.2` | PostgreSQL 驱动 |
| **Alembic** | `>=1.13` | 数据库迁移工具 |
| **Pydantic v2** | `>=2.8` | 数据验证与序列化 |
| **pydantic-settings** | `>=2.4` | 从环境变量读取配置 |
| **akshare** | `>=1.16` | A 股指数/宏观数据源 |

### 分层说明

```
HTTP 请求
    │
    ▼
api/routers/        ← 路由层：解析请求参数，调用 service，返回 schema
    │
    ▼
services/           ← 业务逻辑层：编排数据摄取、策略执行、回测
    │
    ├── infra/db/       ← 数据库层：SQLAlchemy ORM 模型，repositories 封装查询
    ├── infra/clients/  ← 外部 API 客户端：AkShare 指数/宏观
    ├── infra/job_queue/← 统一后台任务队列（持久化 + 幂等去重）
    │
    ├── domain/         ← 领域层：纯业务规则、计算公式、值对象（无外部依赖）
    ├── factors/        ← 因子层：单因子计算（全部基于指数数据）
    └── engine/         ← 引擎层：组件化、配置驱动的策略执行管线
```

**引擎管线：** `StrategyEngine.run(config, context)` 依次执行
`[可选] Timing → Score → [可选] Filter → Rank → [可选] Portfolio → [可选] Risk`，
资产主键统一为 `index_code`（指数代码）。

### 数据库模型

核心表（迁移 0001–0027）：

| 分组 | 表 |
|------|------|
| 参考 | `benchmark_index` |
| 市场数据 | `index_daily_bar`、`index_valuation`、`macro_indicator`、`source_payload_log` |
| 分析 | `factor_definition`、`index_factor_value`、`signal_definition`、`index_signal` |
| 运行 | `research_run`、`research_run_item` |
| 回测 | `backtest_run`、`backtest_daily_result`、`backtest_index_result`、`backtest_comparison` |
| 策略 | `strategy_config` |
| 任务队列 | `background_job` |
| AI 舆情 | `news_item`、`ai_sentiment_result`、`daily_sentiment_aggregate`、`market_synthesis`、`keyword_tag_config` |

迁移 `0027_remove_etf` 已删除全部 ETF 表（`etf_universe`、`etf_daily_bar`、`etf_daily_share`、`etf_factor_value`、`etf_signal`、`backtest_etf_result`）。

### API 路由

统一前缀 `/api`：

| 路由文件 | 路径前缀 | 主要端点 |
|---------|---------|---------|
| `health.py` | `/api/health` | 健康检查 |
| `system.py` | `/api/system` | 系统状态与数据质量 |
| `indexes.py` | `/api/indexes` | 基准指数 CRUD |
| `market_data.py` | `/api/market-data` | 指数日线、估值、宏观指标 |
| `strategies.py` | `/api/strategies` | 策略 CRUD、校验、配置模式决策 |
| `factors.py` | `/api/factors` | 因子定义、IC/IR、相关性矩阵 |
| `runs.py` | `/api/runs` | 运行记录与数据刷新触发 |
| `backtests.py` | `/api/backtests` | 回测与策略对比 |
| `ai_factors.py` | `/api/ai-factors` | AI 舆情分析 |
| `keyword_tags.py` | `/api/keyword-tags` | 关键词标签映射 |

访问 `http://localhost:8000/docs` 查看 Swagger UI。

---

## 前端架构

| 路径 | 页面组件 | 说明 |
|------|---------|------|
| `/` | `DashboardPage.vue` | 总览：指数数量、数据质量、最近运行 |
| `/indexes` | `IndexListPage.vue` | 指数列表（行情 + 估值快照） |
| `/indexes/:indexCode` | `IndexDetailPage.vue` | 单只指数详情 |
| `/strategies` | `StrategiesPage.vue` | 策略列表 |
| `/strategies/:strategyId` | `StrategyDetailPage.vue` | 策略详情与决策调试 |
| `/factors` | `FactorsPage.vue` | 因子列表 |
| `/factors/:factorId` | `FactorDetailPage.vue` | 因子 IC/相关性/时间序列 |
| `/ai-factors` | `AIFactorsPage.vue` | AI 舆情分析 |
| `/keyword-tags` | `KeywordTagsPage.vue` | 关键词标签映射 |
| `/runs` | `RunsPage.vue` | 运行记录 |
| `/backtests` | `BacktestListPage.vue` 等 | 回测中心 |
| `/macro` | `MacroPage.vue` | 宏观指标 |

Pinia store：`strategies`、`signals`、`backtests`（只读数据页内联请求）。

---

## 快速启动

### 后端

```bash
cd apps/api
python -m venv .venv
pip install -e ".[dev]"
cp .env.example .env
# 编辑 .env，填写 PostgreSQL 连接串
alembic upgrade head
uvicorn quant_etf_api.main:app --reload --port 8000
```

首次部署后同步因子与指数种子数据：

```bash
python -m quant_etf_api.cli init-factors
python -m quant_etf_api.cli init-indexes
```

### 前端

```bash
cd apps/web
npm install
npm run dev
```

### 运行测试

```bash
cd apps/api
pytest
ruff check .
```

---

## 开发指南

### 新增策略

策略通过 JSON 配置驱动引擎执行，无需编写代码：

1. 编写策略 JSON 配置（含 score、timing、filters、rank、portfolio、risk、rebalance 模块）
2. 通过 `POST /api/strategies` 创建策略配置
3. 通过 `GET /api/strategies/{id}/allocation` 查看决策管线输出
4. 通过 `POST /api/backtests` 对策略进行历史回测

### 数据库迁移

```bash
cd apps/api
alembic revision --autogenerate -m "描述变更内容"
alembic upgrade head
alembic downgrade -1
```

### 环境变量说明

| 变量名 | 示例值 | 说明 |
|--------|--------|------|
| `QUANT_ETF_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/quant_etf` | PostgreSQL 连接串（历史命名保留） |
| `QUANT_ETF_APP_ENV` | `development` | 运行环境 |
| `QUANT_ETF_APP_HOST` | `0.0.0.0` | 监听地址 |
| `QUANT_ETF_APP_PORT` | `8000` | 监听端口 |
| `QUANT_ETF_CORS_ORIGINS` | `["http://localhost:5173"]` | 允许跨域的前端地址 |

---

## 参考资料

- `AGENTS.md` — 项目开发指南（架构、命令、Gotchas）
- `docs/architecture/指数量化系统策略层架构设计.md` — 策略层架构
- `docs/用户手册.md` — 使用手册
- [FastAPI 官方文档](https://fastapi.tiangolo.com/)
- [SQLAlchemy 2 文档](https://docs.sqlalchemy.org/en/20/)
- [Alembic 文档](https://alembic.sqlalchemy.org/)
- [Vue 3 文档](https://vuejs.org/)
- [Pinia 文档](https://pinia.vuejs.org/)
