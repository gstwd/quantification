# A股 ETF 日频量化研究平台

A-share ETF daily-frequency quantitative research platform — 后端 FastAPI + PostgreSQL，前端 Vue 3，策略以插件形式接入。

---

## 目录

- [项目定位](#项目定位)
- [目录结构](#目录结构)
- [后端架构](#后端架构)
  - [技术栈](#后端技术栈)
  - [分层说明](#分层说明)
  - [数据库模型](#数据库模型)
  - [插件系统](#插件系统)
  - [三因子模型](#三因子模型)
  - [API 路由](#api-路由)
- [前端架构](#前端架构)
  - [技术栈](#前端技术栈)
  - [页面与路由](#页面与路由)
  - [状态管理](#状态管理)
- [快速启动](#快速启动)
- [开发指南](#开发指南)
- [参考资料](#参考资料)

---

## 项目定位

| 维度 | 说明 |
|------|------|
| 资产范围 | 仅 A 股 ETF，不涉及个股 |
| 频率 | 日频，不做日内或实时 |
| 用途 | 研究平台，不做交易执行、账户管理、实盘风控 |
| 数据 | 通过公开 API 抓取，统一存入 PostgreSQL |
| 策略 | 多策略插件化，每个策略独立注册、独立运行 |
| 前端 | Vue 3 研究工作台，展示信号、因子、运行记录 |

`etf-three-factor-org/` 目录保留原始脚本作为领域逻辑参考，不再直接运行。

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
│   │   ├── src/quant_etf_api/  # 主包
│   │   │   ├── main.py         # FastAPI 应用入口
│   │   │   ├── config/         # 配置（读取环境变量）
│   │   │   ├── api/routers/    # HTTP 路由层
│   │   │   ├── schemas/        # Pydantic 请求/响应模型
│   │   │   ├── services/       # 业务逻辑层
│   │   │   ├── plugins/        # 策略插件系统
│   │   │   ├── infra/
│   │   │   │   ├── db/         # SQLAlchemy ORM 模型与数据库连接
│   │   │   │   └── clients/    # 外部 API 客户端（腾讯、东方财富）
│   │   │   └── domain/         # 领域对象（预留扩展）
│   │   └── tests/              # 单元测试 / 集成测试
│   └── web/                    # Vue 3 前端
│       ├── package.json
│       ├── vite.config.ts
│       └── src/
│           ├── main.ts         # 应用入口
│           ├── App.vue         # 根组件（含侧边栏导航）
│           ├── router/         # Vue Router 路由配置
│           ├── stores/         # Pinia 状态管理
│           ├── api/            # Axios API 调用封装
│           ├── pages/          # 页面组件
│           ├── components/     # 可复用组件
│           └── types/          # TypeScript 类型定义
├── docs/architecture/          # 架构文档
├── etf-three-factor-org/       # 原始参考脚本（只读）
├── packages/contracts/         # 前后端接口契约示例
└── tests/smoke/                # 冒烟测试说明
```

---

## 后端架构

### 后端技术栈

| 库 | 版本约束 | 用途 |
|----|---------|------|
| **FastAPI** | `>=0.115` | Web 框架，自动生成 OpenAPI 文档 |
| **Uvicorn** | `>=0.30` | ASGI 服务器，运行 FastAPI |
| **SQLAlchemy 2** | `>=2.0` | ORM，定义数据库模型，执行查询 |
| **psycopg 3** | `>=3.2` | PostgreSQL 驱动（`psycopg[binary]`） |
| **Alembic** | `>=1.13` | 数据库迁移工具，管理表结构变更 |
| **Pydantic v2** | `>=2.8` | 数据验证与序列化，定义 API 的请求/响应结构 |
| **pydantic-settings** | `>=2.4` | 从环境变量读取配置，支持 `.env` 文件 |
| **httpx** | `>=0.27` | 异步 HTTP 客户端，用于抓取外部 API |
| **pytest** | `>=8.3` | 测试框架 |
| **ruff** | `>=0.6` | 代码格式化与 lint |

**为什么选 FastAPI 而不是 Django/Flask？**
FastAPI 原生支持 async/await、自动生成 OpenAPI 文档、Pydantic 集成开箱即用，适合数据密集型研究 API。

**为什么选 SQLAlchemy 2 而不是直接写 SQL？**
SQLAlchemy 2 的 `Mapped` 类型注解让模型定义与 Python 类型系统完全对齐，IDE 可以做类型检查；同时保留了直接执行原生 SQL 的能力。

**为什么选 psycopg 3 而不是 psycopg2？**
psycopg 3 是 psycopg2 的继任者，原生支持 asyncio，与 SQLAlchemy 2 的异步引擎配合更好。

### 分层说明

```
HTTP 请求
    │
    ▼
api/routers/        ← 路由层：解析请求参数，调用 service，返回 schema
    │
    ▼
services/           ← 业务逻辑层：编排数据获取、策略运行、结果存储
    │
    ├── infra/db/   ← 数据库层：SQLAlchemy ORM 模型，repositories 封装查询
    │
    ├── infra/clients/  ← 外部 API 客户端：腾讯 K 线、东方财富份额
    │
    └── plugins/    ← 策略插件：每个策略独立实现，通过 registry 统一管理
```

**路由层（`api/routers/`）** 只做参数解析和响应格式化，不包含业务逻辑。

**服务层（`services/`）** 是业务逻辑的核心，当前使用 stub（桩）数据，后续替换为真实数据库查询和 API 调用。

**基础设施层（`infra/`）** 分两部分：
- `db/` — 数据库连接、ORM 模型、查询封装
- `clients/` — 外部 HTTP API 的封装，只负责抓取和初步解析，不做业务判断

### 数据库模型

数据库按 4 组组织，共 13 张表：

#### 参考数据（Reference Data）

| 表名 | 主键 | 说明 |
|------|------|------|
| `etf_universe` | `etf_code` | ETF 基础信息：名称、交易所、跟踪指数、上市日期等 |
| `benchmark_index` | `index_code` | 基准指数：沪深 300、上证 50、中证 500 等 |

#### 原始市场数据（Market Data）

| 表名 | 唯一约束 | 说明 |
|------|---------|------|
| `etf_daily_bar` | `(trade_date, etf_code)` | ETF 日线：开高低收、涨跌幅、成交量、换手率 |
| `index_daily_bar` | `(trade_date, index_code)` | 指数日线 |
| `etf_daily_share` | `(trade_date, etf_code)` | ETF 份额：总份额、份额变化量、份额变化率、净值、规模 |
| `source_payload_log` | — | 原始 API 响应存档，用于排查字段变动 |

#### 派生因子与信号（Analytics）

| 表名 | 唯一约束 | 说明 |
|------|---------|------|
| `factor_definition` | `factor_id` | 因子元数据：名称、描述、归属插件 |
| `etf_factor_value` | `(trade_date, etf_code, factor_id, strategy_id)` | 每只 ETF 每日的因子计算结果 |
| `signal_definition` | `signal_id` | 信号元数据 |
| `etf_signal` | `(trade_date, etf_code, strategy_id)` | 每只 ETF 每日的综合信号：分数、等级、标签 |

#### 运行记录（Runtime）

| 表名 | 主键 | 说明 |
|------|------|------|
| `strategy_plugin` | `strategy_id` | 已注册策略的元数据持久化 |
| `research_run` | `run_id` | 每次数据采集或策略运行的记录：状态、参数、耗时、错误 |
| `research_run_item` | `id` | 运行记录的明细，每只 ETF 一行 |

**设计原则：** 因子和信号不按策略单独建表，而是共用 `etf_factor_value` / `etf_signal`，通过 `strategy_id` 字段区分。这样新增策略不需要改表结构。

### 插件系统

插件系统的核心是 `plugins/base.py` 中定义的 `StrategyPlugin` Protocol（协议）。

**什么是 Protocol？**
Python 的 `Protocol` 是结构化子类型（鸭子类型）的正式化。只要一个类实现了 Protocol 要求的所有属性和方法，它就被认为是该 Protocol 的实现，不需要显式继承。

```python
# plugins/base.py — 插件必须实现的接口
class StrategyPlugin(Protocol):
    strategy_id: str        # 唯一标识，如 "three_factor_guard"
    display_name: str       # 展示名称
    version: str            # 版本号
    frequency: str          # 频率，固定为 "daily"
    asset_scope: str        # 资产范围，固定为 "a_share_etf"
    description: str        # 策略描述

    def parameter_schema(self) -> dict: ...         # 参数 JSON Schema
    def required_inputs(self) -> list[str]: ...     # 需要哪些输入数据
    def factor_definitions(self) -> list[dict]: ... # 输出哪些因子
    def signal_definition(self) -> dict: ...        # 输出信号的定义
    def prepare_context(...) -> StrategyContextData: ...  # 准备运行上下文
    def run_for_universe(...) -> list[StrategyResult]: ... # 对全 ETF 池运行
    def explain_result(...) -> dict: ...            # 解释单只 ETF 的结果
```

**注册机制（`plugins/registry.py`）：**
```python
registry = StrategyRegistry()
registry.register(ThreeFactorGuardPlugin())
registry.register(ShareFlowMonitorPlugin())
registry.register(VolumeBreakoutDailyPlugin())
```

应用启动时（`main.py`）调用 `build_default_registry()` 完成注册。

**内置策略：**

| 策略 ID | 文件 | 说明 |
|---------|------|------|
| `three_factor_guard` | `builtins/three_factor/` | 三因子模型：量比 + 方向 + 份额 |
| `share_flow_monitor` | `builtins/share_flow_monitor/` | 单因子份额监控，用于数据验证 |
| `volume_breakout_daily` | `builtins/volume_breakout/` | 简单量能突破，作为基线对比 |

### 三因子模型

三因子模型的数学实现在 `plugins/builtins/three_factor/factors.py`，从原始脚本迁移而来。

#### 因子 1：量比概率（`volume_probability`）

输入：20 日量比（当日成交量 / 过去 20 日均量）

分段线性映射到 0–100 的概率分：

| 量比区间 | 概率范围 | 含义 |
|---------|---------|------|
| 0 – 0.5 | 0 – 5 | 缩量，无信号 |
| 0.5 – 1.0 | 5 – 17 | 正常量 |
| 1.0 – 1.3 | 17 – 35 | 温和放量 |
| 1.3 – 1.5 | 35 – 55 | 明显放量 |
| 1.5 – 2.0 | 55 – 80 | 显著放量 |
| 2.0 – 3.0 | 80 – 95 | 大幅放量 |
| ≥ 3.0 | 95 – 100 | 极端放量，上限 100 |

#### 因子 2：方向概率（`direction_probability`）

输入：当日涨跌幅、ETF 5 日涨跌幅、指数 5 日涨跌幅、量比、指数当日涨跌幅

内部计算 3 个子分：
- `f1`（权重 40%）：当日价格行为 × 指数背景（逆势上涨得高分）
- `f2`（权重 30%）：ETF 相对指数的 5 日超额收益
- `f3`（权重 20%）：指数 5 日趋势（指数跌得越深，反弹概率越高）
- 基础分 35（权重 10%）

**大盘普涨折扣：** 当指数当日涨幅 > 0.5% 时，对结果打折（最低 0.6），避免把普涨误判为强势信号。

#### 因子 3：份额概率（`share_probability`）

输入：ETF 份额日变化率（%）

份额增加表示资金净流入，是看多信号；份额减少表示资金净流出。

| 份额变化率 | 概率范围 |
|-----------|---------|
| > 10% | 95（上限） |
| 5% – 10% | 80 – 95 |
| 3% – 5% | 65 – 80 |
| 1% – 3% | 45 – 65 |
| 0% – 1% | 30 – 45 |
| -1% – 0% | 15 – 30 |
| -5% – -1% | 5 – 15 |
| < -5% | 0 – 5（下限 0） |
| `None`（无数据） | 返回 `None` |

#### 综合概率（`composite_probability`）

有份额数据时（三因子）：
```
综合分 = 量比概率 × 50% + 方向概率 × 20% + 份额概率 × 30%
```

无份额数据时（两因子降级）：
```
综合分 = 量比概率 × 70% + 方向概率 × 30%
```

#### 信号等级（`signal_level`）

| 综合分 | 等级 | 标签 |
|--------|------|------|
| ≥ 70 | HIGH | 高确信 |
| 50 – 69 | MID | 中等关注 |
| < 50 | LOW | 正常 |

### API 路由

后端提供 7 组路由，统一前缀 `/api`：

| 路由文件 | 路径前缀 | 主要端点 |
|---------|---------|---------|
| `health.py` | `/api/health` | `GET /` — 健康检查 |
| `system.py` | `/api/system` | `GET /status` — 平台状态与数据新鲜度 |
| `etfs.py` | `/api/etfs` | `GET /` 列表，`GET /{etf_code}` 详情 |
| `market_data.py` | `/api/market-data` | ETF 日线、份额、指数日线 |
| `strategies.py` | `/api/strategies` | 策略列表、策略详情、触发运行 |
| `signals.py` | `/api/signals` | 最新信号、历史信号、因子查询 |
| `runs.py` | `/api/runs` | 运行记录列表与详情 |

访问 `http://localhost:8000/docs` 可查看自动生成的 Swagger UI。

---

## 前端架构

### 前端技术栈

| 库 | 版本约束 | 用途 |
|----|---------|------|
| **Vue 3** | `^3.4` | UI 框架，使用 Composition API + `<script setup>` 语法 |
| **Vite** | `^5.4` | 构建工具，开发时热更新极快 |
| **TypeScript** | `^5.5` | 类型安全，与后端 schema 对应 |
| **Pinia** | `^2.1` | 状态管理，替代 Vuex，API 更简洁 |
| **Vue Router 4** | `^4.4` | 客户端路由 |
| **ECharts** | `^5.5` | 图表库，用于 K 线、成交量、因子趋势图 |
| **Axios** | `^1.7` | HTTP 客户端，封装对后端 API 的调用 |

**为什么选 Pinia 而不是 Vuex？**
Pinia 是 Vue 官方推荐的状态管理库（Vuex 5 的实质继任者），TypeScript 支持更好，不需要 mutations，代码更简洁。

**为什么选 ECharts 而不是 Chart.js？**
ECharts 对金融图表（K 线图、成交量柱状图）的支持更完善，且在中文生态中文档更丰富。

### 页面与路由

| 路径 | 页面组件 | 说明 |
|------|---------|------|
| `/` | `DashboardPage.vue` | 研究总览：系统状态、最新高信号 ETF |
| `/etfs` | `EtfUniversePage.vue` | ETF 池列表，支持筛选搜索 |
| `/etfs/:etfCode` | `EtfDetailPage.vue` | 单只 ETF：元数据、日线、份额、历史信号 |
| `/strategies` | `StrategiesPage.vue` | 已注册策略列表 |
| `/strategies/:strategyId` | `StrategyDetailPage.vue` | 策略详情：信号分布、历史输出、参数说明 |
| `/runs` | `RunsPage.vue` | 数据采集与策略运行记录 |
| `/data-status` | `DataStatusPage.vue` | 数据源更新时间、覆盖率、错误情况 |

### 状态管理

Pinia store 按数据域划分：

| Store 文件 | 管理的数据 | 主要 action |
|-----------|-----------|------------|
| `stores/etfs.ts` | ETF 列表与详情缓存 | `loadList()`, `loadDetail(etfCode)` |
| `stores/strategies.ts` | 策略列表与详情缓存 | `loadList()`, `loadDetail(strategyId)` |
| `stores/signals.ts` | 信号数据 | `loadLatest(strategyId)` |

API 调用封装在 `api/` 目录下，store 调用 API 函数，页面组件只与 store 交互。

---

## 快速启动

### 前置条件

- Python 3.11+
- Node.js 18+
- PostgreSQL 15+（本地或 Docker）

### 后端

```bash
cd apps/api

# 1. 使用venv配置vscode，安装依赖（推荐用 uv 或 pip）
python -m venv .venv
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填写 PostgreSQL 连接串

# 3. 创建数据库表
alembic upgrade head

# 4. 启动开发服务器
uvicorn quant_etf_api.main:app --reload --host 0.0.0.0 --port 8000
```

访问 `http://localhost:8000/docs` 查看 API 文档。

### 前端

```bash
cd apps/web

# 1. 安装依赖
npm install

# 2. 启动开发服务器
npm run dev
```

访问 `http://localhost:5173`。

### 运行测试

```bash
cd apps/api
pytest
```

当前有 22 个单元测试，覆盖三因子模型的所有计算函数。

---

## 开发指南

### 新增策略插件

1. 在 `apps/api/src/quant_etf_api/plugins/builtins/` 下新建目录，如 `my_strategy/`
2. 创建 `__init__.py` 和 `plugin.py`
3. 在 `plugin.py` 中实现 `StrategyPlugin` Protocol 的所有方法
4. 在 `plugins/registry.py` 的 `build_default_registry()` 中注册

```python
# plugin.py 最小骨架
class MyStrategyPlugin:
    strategy_id = "my_strategy"
    display_name = "我的策略"
    version = "1.0.0"
    frequency = "daily"
    asset_scope = "a_share_etf"
    description = "策略描述"

    def parameter_schema(self) -> dict:
        return {}

    def required_inputs(self) -> list[str]:
        return ["etf_daily_bar"]

    def factor_definitions(self) -> list[dict]:
        return [{"factor_id": "my_factor", "name": "我的因子"}]

    def signal_definition(self) -> dict:
        return {"signal_id": "my_signal", "name": "我的信号"}

    def prepare_context(self, trade_date, params=None):
        from quant_etf_api.plugins.base import StrategyContextData
        return StrategyContextData()

    def run_for_universe(self, trade_date, universe, context, params=None):
        # 对每只 ETF 计算信号，返回 StrategyResult 列表
        ...

    def explain_result(self, result):
        return {"summary": "..."}
```

### 替换 stub 数据为真实数据

当前 `services/` 层返回硬编码的 stub 数据，便于前端开发。接入真实数据的步骤：

1. 在 `infra/clients/tencent.py` 中调用腾讯 K 线 API
2. 在 `infra/clients/eastmoney.py` 中调用东方财富份额 API
3. 在对应 service 中替换 stub 返回值，改为调用 client 并写入数据库

### 数据库迁移

修改 `infra/db/models/core.py` 中的模型后：

```bash
cd apps/api
# 自动生成迁移脚本
alembic revision --autogenerate -m "描述变更内容"
# 应用迁移
alembic upgrade head
# 回滚一步
alembic downgrade -1
```

### 环境变量说明

| 变量名 | 示例值 | 说明 |
|--------|--------|------|
| `QUANT_ETF_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/quant_etf` | PostgreSQL 连接串，驱动必须是 `psycopg`（psycopg3） |
| `QUANT_ETF_APP_ENV` | `development` | 运行环境 |
| `QUANT_ETF_APP_HOST` | `0.0.0.0` | 监听地址 |
| `QUANT_ETF_APP_PORT` | `8000` | 监听端口 |
| `QUANT_ETF_CORS_ORIGINS` | `["http://localhost:5173"]` | 允许跨域的前端地址，JSON 数组格式 |

---

## 参考资料

- `etf-three-factor-org/references/etf_model.md` — 三因子模型定义与阈值说明
- `etf-three-factor-org/references/config.md` — 数据源 API 来源与限制
- `docs/architecture/overview.md` — 系统架构总览
- `docs/architecture/data-model.md` — 数据库模型详细说明
- `docs/architecture/plugin-system.md` — 插件系统设计说明
- [FastAPI 官方文档](https://fastapi.tiangolo.com/)
- [SQLAlchemy 2 文档](https://docs.sqlalchemy.org/en/20/)
- [Alembic 文档](https://alembic.sqlalchemy.org/)
- [Vue 3 文档](https://vuejs.org/)
- [Pinia 文档](https://pinia.vuejs.org/)
