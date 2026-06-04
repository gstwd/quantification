# ETF资产配置决策系统架构设计

## 一、平台定位

本平台定位为 **ETF资产配置决策系统**，核心目标：

- 对A股ETF及其底层指数进行系统化研究（仅日频数据）
- 提供资产配置决策支持：择时判断 → 资产轮动 → 仓位管理
- 构建可解释、可回测、可迭代的策略研究体系
- 支持双模式回测：信号评分模式 + 资产配置模式
- **不涉及实盘交易**，仅用于研究和决策辅助
- **不涉及个股**，研究对象限定为ETF和指数

### 核心决策流程

```
宏观指标 → 择时信号(进攻/防守/观望)
    + 板块指标 → 资产轮动排名(买什么)
    + 风控指标 → 仓位分配(买多少)
    = 最终调仓建议
```

## 二、核心研究对象

### 1. ETF

| 研究维度 | 当前状态 | 说明 |
|---------|---------|------|
| 日线行情（OHLCV） | ✅ 已实现 | 数据源：腾讯财经，18只ETF |
| 份额与规模 | ✅ 已实现 | 数据源：东方财富，覆盖7只 |
| 资金流向 | 📋 规划中 | 可通过份额变化间接推算净流入 |
| 折溢价 | 📋 规划中 | 需接入净值数据后计算 |
| 跟踪误差 | 📋 规划中 | 需同时具备ETF净值和指数行情 |

### 2. 底层指数

| 研究维度 | 当前状态 | 说明 |
|---------|---------|------|
| 指数日线行情 | ✅ 已实现 | 数据源：AkShare |
| 指数估值（PE/PB） | ✅ 已实现 | AkShare，仅沪深300/上证50/中证500有数据 |
| 成分股与权重 | 📋 规划中 | 需新增数据源 |
| 行业分布 | 📋 规划中 | 依赖成分股数据 |
| 风格暴露 | 📋 规划中 | 依赖因子体系建立 |

### 3. 宏观环境

| 研究维度 | 当前状态 | 说明 |
|---------|---------|------|
| CPI | ✅ 已实现 | 数据源：AkShare |
| PMI | ✅ 已实现 | 数据源：AkShare |
| LPR | ✅ 已实现 | 数据源：AkShare |
| 社融/M2 | 📋 规划中 | |
| 北向资金 | 📋 规划中 | |

## 三、系统架构

### 架构总览

```
┌─────────────────────────────────────────────────────────┐
│                   可视化层（Vue 3 + ECharts）              │
│                   Dashboard + 资产配置面板                 │
├─────────────────────────────────────────────────────────┤
│                   API层（FastAPI REST）                   │
├──────────┬──────────┬──────────┬──────────┬─────────────┤
│  回测层   │  风险层   │  策略层   │  因子层   │  数据层     │
│  ✅ 双模式 │  📋 规划  │  ✅ 决策管线│  ✅ 已实现 │  ✅ 已实现  │
├──────────┴──────────┴──────────┴──────────┴─────────────┤
│                   PostgreSQL + 数据源客户端                │
└─────────────────────────────────────────────────────────┘
```

### 当前技术栈

| 层级 | 技术选型 |
|------|---------|
| 后端框架 | FastAPI（同步模式，线程池处理并发） |
| 数据库 | PostgreSQL + SQLAlchemy 2 ORM |
| 迁移 | Alembic |
| 前端 | Vue 3 + TypeScript + Pinia + ECharts 5 |
| 调度 | 自研 DailyIngestScheduler（守护线程，工作日17:30触发） |
| 策略 | 自研 Plugin Protocol（结构子类型，无需继承） |

### 后端分层

```
HTTP请求 → api/routers/ → services/ → infra/ → PostgreSQL
                          ↓           ↑
                      plugins/ ← domain/ (纯业务规则)
                          ↓
                      factors/ (单因子计算)
```

- **`api/routers/`** — 9个路由组：health、system、etfs、market_data、strategies、signals、factors、runs、backtests
- **`services/`** — 业务逻辑层：IngestService（数据采集）、StrategyService（策略执行 + 资产配置决策）、BacktestService（双模式回测）、SignalService（信号查询）等
- **`infra/db/`** — ORM模型（18张表）与数据访问
- **`infra/clients/`** — 4个外部数据源客户端，统一继承 `base.py`
- **`domain/`** — 纯领域逻辑（无外部依赖）：`common/`（bar_metrics、enums、values）、`strategies/`（models、scoring、TimingSignal、AssetRanking、AllocationPlan）
- **`factors/`** — 单因子计算层：FactorComputer Protocol + 8个内置因子计算器，依赖 domain
- **`plugins/`** — 策略插件系统：StrategyPlugin Protocol + 4个内置策略（含决策管线），依赖 domain 的评分规则

## 四、数据层（Data Layer）✅ 已实现

数据层负责采集、存储和统一管理所有研究数据。

### 当前数据源

| 数据源 | 客户端 | 提供数据 | 覆盖范围 |
|--------|--------|---------|---------|
| AkShare（新浪后端） | `akshare_fund.py` | ETF日线行情（前复权） | etf_universe 全部活跃ETF |
| AkShare（东财行情缓存） | `akshare_fund.py` | ETF份额/净值/规模 | 全量（10分钟缓存）|
| AkShare | `akshare_index.py` | 指数日线 + PE/PB估值 | 估值仅3只指数 |
| AkShare | `akshare_macro.py` | 宏观指标（CPI/PMI/LPR） | 全量 |
| 交易所 | `exchange_reference.py` | 静态参考数据 | — |

### 数据库模型（18张表）

| 分组 | 表 |
|------|---|
| 基础 | `etf_universe`（18只ETF）、`benchmark_index` |
| 行情 | `etf_daily_bar`、`index_daily_bar`、`etf_daily_share`、`index_valuation`、`macro_indicator` |
| 因子/信号 | `factor_definition`、`etf_factor_value`、`signal_definition`、`etf_signal` |
| 策略/运行 | `strategy_plugin`、`research_run`、`research_run_item` |
| 回测 | `backtest_run`、`backtest_daily_result`、`backtest_etf_result` |
| 审计 | `source_payload_log` |

### 数据采集机制

采用 **Read-Through Cache** 模式：

```
GET请求 → 查DB → 有数据则返回
                → 无数据则加锁 → 调用外部API → Upsert入库 → 返回
```

定时任务：`DailyIngestScheduler` 工作日17:30自动执行全量刷新，也可通过 `POST /api/runs/daily-ingest` 手动触发。

### 数据层演进方向

| 能力 | 当前 | 目标 |
|------|-----|------|
| 数据清洗 | 基础去重（ON CONFLICT DO NOTHING） | 停牌填充、异常值检测、复权统一 |
| 数据标准化 | 代码/时间已统一 | 增加指标字段标准化、周期格式统一 |
| 数据源覆盖 | 5个客户端、有限覆盖 | 扩展ETF份额覆盖、增加成分股/北向/社融数据源 |
| 数据质量监控 | 无 | 增加完整性检查、源数据对账 |

## 五、因子层（Factor Layer）✅ 已实现

因子层将原始数据转化为可量化分析的投资因子，**仅负责原始值计算和因子评估，不做标准化/加权**。

### 因子层与策略层的分工

| 职责 | 归属层 | 说明 |
|------|-------|------|
| 原始因子值计算 | 因子层 | 回答"ETF X 在日期 T 的原始因子值是多少？" |
| 因子 IC/IR 评估 | 因子层 | Rank IC、IC_IR、因子效力分析 |
| 因子相关性矩阵 | 因子层 | 截面 Spearman 相关，判断因子冗余度 |
| 因子标准化 | 策略层 | 去极值、Z-Score，使不同因子可比 |
| 因子加权组合 | 策略层 | 等权/IC 加权/最优化加权 |
| 信号生成 | 策略层 | 综合得分 → 信号等级 |

### 当前实现

因子通过独立的 `FactorComputer` Protocol 计算，策略插件通过 `factor_definitions()` 声明依赖的因子：

| 因子 | 类别 | 说明 |
|------|------|------|
| volume_ratio_20d | volume | 20日量比 |
| return_5d / return_20d / return_60d | momentum | 短中长期收益率 |
| volatility_20d | volatility | 20日年化波动率 |
| share_delta_pct | flow | 份额日变化率 |
| pe_percentile | valuation | PE 历史百分位（仅主要宽基指数） |
| pb_percentile | valuation | PB 历史百分位（仅主要宽基指数） |

因子计算结果存储在 `etf_factor_value` 表中，支持横截面和时间序列查询。

### 因子评估能力

- **Rank IC 分析**：计算因子值与下期收益率的 Spearman 秩相关系数，评估因子预测力
- **IC 汇总统计**：IC 均值、IC 标准差、IC_IR（>0.5 表示因子稳定）、IC>0 占比
- **因子相关性矩阵**：截面 Rank 相关热力图，识别冗余因子
- API：`GET /factors/{factor_id}/ic`、`GET /factors/correlation`
- 前端：因子详情页的"IC 分析"和"相关性"Tab

### 演进方向

| 能力 | 当前 | 目标 |
|------|-----|------|
| 因子类别 | 8 个内置因子（volume/momentum/volatility/flow/valuation） | 增加红利、质量、技术因子 |
| 因子评估 | IC/IR + 相关性矩阵 | 增加分组回测、因子衰减分析 |
| 估值覆盖 | 仅沪深300/上证50/中证500 | 扩展到更多指数 |
| 复合因子 | 无 | 表达式引擎（DSL 自定义因子） |
| 数据质量 | 无 | 覆盖率统计、异常检测 |

## 六、策略层（Strategy Layer）✅ 已实现

### Plugin Protocol 设计

策略通过 **结构子类型**（Structural Subtyping）实现，无需继承基类：

```python
class StrategyPlugin(Protocol):
    # 元信息
    strategy_id: str
    display_name: str
    version: str
    frequency: str        # 固定 "daily"
    asset_scope: str
    description: str

    # 必需接口方法
    def parameter_schema(self) -> dict: ...
    def required_inputs(self) -> list[str]: ...
    def factor_definitions(self) -> list[dict]: ...
    def signal_definition(self) -> dict: ...
    def prepare_context(self, trade_date, params) -> StrategyContextData: ...
    def run_for_universe(self, trade_date, universe, context, params) -> list[StrategyResult]: ...
    def explain_result(self, result) -> dict: ...

    # 可选决策管线方法（通过 hasattr 检查）
    def assess_market_timing(self, trade_date, context, params) -> TimingSignal | None: ...
    def rank_assets(self, trade_date, universe, context, params) -> list[AssetRanking] | None: ...
    def allocate_positions(self, timing, rankings, params) -> AllocationPlan | None: ...
```

### 信号输出

每次策略执行产出 `StrategyResult`：

- `signal_score`：0-100 综合评分
- `signal_level`：HIGH（≥70）/ MID（50-69）/ LOW（<50）
- `signal_label`：中文描述
- `factor_values`：各因子明细
- 存储于 `etf_signal` 和 `etf_factor_value` 表

### 已实现的策略插件

| 插件 | 策略逻辑 | 因子权重 | 模式 |
|------|---------|---------|------|
| `three_factor_guard` | 三因子综合守卫 | 成交量50% + 方向20% + 份额30% | 信号评分 |
| `share_flow_monitor` | 份额流向监控 | 单因子 | 信号评分 |
| `volume_breakout_daily` | 放量突破基线 | 量比 + 日收益率 | 信号评分 |
| `etf_allocation` | 资产配置策略 | 择时→轮动→仓位分配 | 资产配置 |

### 决策管线（Decision Pipeline）

`etf_allocation` 插件实现完整的资产配置决策管线：

```
assess_market_timing()  →  TimingSignal (regime: 进攻/防守/观望)
        ↓
rank_assets()           →  list[AssetRanking] (按综合得分排序)
        ↓
allocate_positions()    →  AllocationPlan (目标仓位比例)
```

**择时评分**：估值(40%) + 趋势(40%) + 量能(20%) → 综合分 ≥65 进攻，≤35 防守，否则观望

**轮动排名**：动量(60%) + 估值吸引力(40%) → 板块选择

**仓位分配**：进攻 80%、中性 50%、防守 20% 总仓位，单只上限 30%，最多持 5 只

### 策略层演进方向

| 能力 | 当前 | 目标 |
|------|-----|------|
| 策略类型 | 信号评分型 + 资产配置型 | 增加更多配置策略变体 |
| 参数管理 | `parameter_schema()` 定义 | 参数网格搜索、参数敏感性分析 |
| 多策略协同 | 独立运行 | 策略信号融合、投票机制 |
| 调仓逻辑 | 仅日频 | 增加周/月频调仓支持 |

## 七、回测层（Backtest Layer）✅ 双模式实现

### 当前回测能力

回测引擎支持双模式，通过 `backtest_mode` 参数选择：

**模式A：信号评分模式（signal）**

```
选择策略 + 时间范围 + ETF范围
    → 逐日执行策略计算信号
    → HIGH信号ETF纳入持仓
    → 计算T+1收益
    → 汇总组合指标
```

组合构建方式：
- `equal_weighted`：HIGH信号ETF等权
- `signal_weighted`：按 signal_score 加权

**模式B：资产配置模式（allocation）**

```
选择策略 + 时间范围 + ETF范围
    → 逐日执行决策管线
    → assess_market_timing() → 进攻/防守/观望
    → rank_assets() → 资产排名
    → allocate_positions() → 目标仓位
    → 按仓位比例计算组合收益
    → 汇总组合指标
```

**已实现指标：**
- 累计收益率、最大回撤、夏普比率（年化）
- 胜率、信号准确率
- 逐日组合收益曲线、回撤曲线
- 单ETF信号明细
- 资产配置模式：逐日择时信号、总仓位、现金比例、持仓明细

### 回测层演进方向

| 能力 | 当前 | 目标 |
|------|-----|------|
| 交易成本 | 无 | 佣金 + 印花税 + 滑点模拟 |
| 仓位管理 | 等权/信号加权/资产配置 | 增加风险平价、动态仓位 |
| 调仓频率 | 仅日频 | 周/月调仓 |
| 基准对比 | 无 | 与沪深300等基准对比 |
| 分年统计 | 无 | 年度/月度收益分解 |
| 未来函数检测 | 人工保证 | 自动检测机制 |

## 八、风险层（Risk Layer）📋 规划中

风险层用于评估策略稳健性和资产组合风险。

### 规划能力

**基础风险指标：**
- 最大回撤（回测层已有，需抽离复用）
- 年化波动率
- 下行风险（Downside Deviation）

**进阶风险分析：**
- ETF相关性矩阵（识别分散化机会）
- Beta（相对市场敏感度）
- 因子暴露分析（策略承担了哪些系统风险）
- 压力测试（极端行情下策略表现）

### 实施前提

- 因子层独立化完成后，才能做因子暴露分析
- 需要足够长的历史数据（至少3年日线）才有统计意义

## 九、组合层（Portfolio Layer）📋 规划中

组合层在策略信号之上，管理多ETF的资产配置。

### 规划能力

- 多策略信号融合（投票 / 加权 / 分层）
- 组合优化（均值-方差 / 风险平价）
- 再平衡规则（阈值触发 / 定期）
- 行业/风格暴露约束

### 实施前提

- 依赖风险层的相关性和波动率计算
- 需要回测层支持多仓位策略

## 十、可视化层（Visualization Layer）✅ 已实现

### 当前页面

| 页面 | 功能 |
|------|------|
| Dashboard | 统计概览（ETF数量、策略数、今日信号）+ 最新信号表 |
| ETF列表/详情 | ETF基础信息、日线行情 |
| 指数列表/详情 | 指数行情、PE/PB估值 |
| 宏观数据 | CPI/PMI/LPR趋势图 |
| 策略列表/详情 | 插件元信息、最新信号明细、因子值 |
| 运行记录 | 数据采集和策略执行历史 |
| 回测 | 创建/列表/详情，收益曲线、回撤曲线 |
| 数据状态 | 各数据源覆盖情况 |

### 可视化演进方向

| 能力 | 当前 | 目标 |
|------|-----|------|
| 图表类型 | K线、折线、表格、热力图 | 增加散点图、分布图 |
| 因子分析图 | IC 柱状图、相关性热力图 | 增加因子分组收益图 |
| 回测分析 | 收益/回撤曲线 | 增加月度热力图、滚动指标、基准对比 |
| 风险可视化 | 无 | 相关性矩阵图、因子暴露雷达图 |

## 十一、研究纪律

以下原则贯穿平台所有研究活动：

### 必须避免

| 问题 | 含义 | 防范措施 |
|------|-----|---------|
| 未来函数 | 使用了决策时点之后的数据 | 回测严格按时间序列，T日决策只能用T-1及之前数据 |
| 幸存者偏差 | 只研究当前存在的ETF | ETF Universe需包含已退市品种（长期目标） |
| 过拟合 | 无限调参拟合历史数据 | 样本内/外分割、参数稳定性检验 |
| 数据窥探 | 反复试验直到"有效" | 预设假设再验证，记录所有实验 |

## 十二、发展路线图

### 第一阶段：数据与基础策略 ✅ 已完成

- [x] 数据采集体系（5个数据源、18张表、定时刷新）
- [x] Plugin Protocol 策略框架
- [x] 3个内置策略插件（信号评分模式）
- [x] 基础回测引擎
- [x] 前端完整页面体系

### 第二阶段：因子体系独立化 ✅ 已完成

- [x] 将通用因子从插件中抽离为独立模块
- [x] 实现动量、波动率、估值等标准因子（8 个内置因子）
- [x] 因子 IC/IR 评估与相关性矩阵分析
- [ ] 扩展数据源覆盖（ETF份额全覆盖、更多指数估值）

### 第三阶段：资产配置决策系统 ✅ 已完成

- [x] 扩展 StrategyPlugin Protocol，新增 3 个可选决策管线方法
- [x] 新增领域模型：TimingSignal、AssetRanking、AllocationPlan
- [x] 实现 etf_allocation 插件（择时→轮动→仓位分配）
- [x] BacktestService 支持双模式回测（信号评分 + 资产配置）
- [x] StrategyService 支持运行资产配置决策管线
- [x] Dashboard 新增资产配置面板
- [x] 回测创建页支持选择回测模式
- [x] 123 个单元测试全部通过

### 第四阶段：回测与风险完善

- [ ] 回测增加交易成本模拟
- [ ] 回测增加基准对比和分期统计
- [ ] 建立风险分析模块（相关性、Beta、波动率分析）
- [ ] 多频率调仓支持

### 第五阶段：高级配置策略

- [ ] 新增 MA 趋势因子（ma_position）
- [ ] 新增最大回撤因子（max_drawdown_60d）
- [ ] 新增相对强弱因子（relative_strength_20d）
- [ ] 多策略信号融合
- [ ] 组合优化与再平衡

## 十三、平台目标

平台最终目标不是寻找"永远赚钱"的圣杯策略，而是建立一套：

- **可解释** — 每个决策都能追溯到具体因子和数据
- **可验证** — 任何策略都必须经过历史回测检验
- **可复现** — 相同参数、相同数据产出相同结果
- **可迭代** — Plugin Protocol 支持快速新增和替换策略
- **实用导向** — 直接回答"现在该买什么、买多少"的核心问题

的ETF资产配置决策系统。核心价值在于：**通过数据和回测验证投资逻辑，为个人ETF投资提供系统化的决策支持。**
