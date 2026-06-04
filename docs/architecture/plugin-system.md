# Plugin System

## 四层关系

策略相关代码分为四层，依赖方向为 `domain ← factors ← plugins`，evaluation 模块平行于 factors 层：

```
┌─────────────┐
│   Plugin     │  编排层：对因子做标准化/加权组合，产出信号或决策
│  (策略)      │  "信号模式: Z-Score → 量能50% + 方向20% + 份额30% → HIGH/MID/LOW"
│              │  "配置模式: 择时 → 轮动排名 → 仓位分配 → 调仓建议"
└──────┬───────┘
       │ 调用
┌──────▼───────┐
│   Factor      │  计算层：原始因子值计算 + 因子评估（IC/相关性）
│  (因子)      │  "量比 1.92"、"PE 百分位 35.2"、"Rank IC 0.12"
└──────┬───────┘
       │ 使用
┌──────▼───────┐
│   Domain      │  规则层：纯业务规则、公式、阈值、枚举
│  (领域)      │  "量比>1.5 属于显著放量"、"得分≥70 为 HIGH"
└──────────────┘
```

## 因子层与策略层的职责边界

| 职责 | 归属层 | 说明 |
|------|-------|------|
| 原始因子值计算 | **因子层** | 回答"ETF X 在日期 T 的原始因子值是多少？" |
| 因子定义管理 | **因子层** | FactorSpec 元数据、DB 同步、启停控制 |
| 因子 IC/IR 评估 | **因子层** | Rank IC、IC_IR、因子效力分析 |
| 因子相关性矩阵 | **因子层** | 截面 Spearman 相关，判断因子冗余度 |
| 数据质量监控 | **因子层** | 覆盖率、时效性、缺失检测 |
| 因子标准化 | **策略层** | 去极值（MAD/3σ）、Z-Score，使不同因子可比 |
| 因子加权组合 | **策略层** | 等权/IC 加权/最优化加权，产出综合得分 |
| 信号生成 | **策略层** | 综合得分 → 信号等级（HIGH/MID/LOW） |
| 因子衰减管理 | **策略层** | 半衰期、换手率，影响持仓周期 |

**原则：因子层只产出原始值和评估指标，不做标准化/加权；策略层消费因子原始值，负责组合和信号生成。**

## Domain 层

`domain/strategies/` 包含：

- **`models.py`** — `StrategyContextData`（策略执行上下文）、`StrategyResult`（单 ETF 策略结果）、`TimingSignal`（择时信号）、`AssetRanking`（资产排名项）、`AllocationPlan`（仓位分配方案）数据类
- **`scoring.py`** — 信号评分规则：`volume_probability()`、`direction_probability()`、`share_probability()`、`composite_probability()`、`signal_level()`

`domain/common/` 包含：

- **`bar_metrics.py`** — K 线衍生指标计算（量比、收益率）
- **`enums.py`** — 领域枚举（SignalLevel、RunStatus、FactorCategory 等）
- **`values.py`** — 值对象（DateRange）

## Factor 层

`factors/` 包含：

- **`base.py`** — FactorSpec、FactorContext、FactorValue 数据类 + FactorComputer Protocol
- **`registry.py`** — FactorRegistry 注册表
- **`service.py`** — FactorService 编排因子计算和持久化
- **`evaluation.py`** — 因子评估模块：Rank IC 计算、IC 汇总统计、因子相关性矩阵
- **`builtins/`** — 8 个内置因子计算器

### 内置因子列表

| factor_id | 名称 | 类别 | 数据依赖 |
|-----------|------|------|---------|
| `volume_ratio_20d` | 20日量比 | volume | etf_bars |
| `return_5d` | 5日收益率 | momentum | etf_bars |
| `return_20d` | 20日收益率 | momentum | etf_bars |
| `return_60d` | 60日收益率 | momentum | etf_bars |
| `volatility_20d` | 20日年化波动率 | volatility | etf_bars |
| `share_delta_pct` | 份额日变化率 | flow | etf_shares |
| `pe_percentile` | PE百分位 | valuation | index_valuation |
| `pb_percentile` | PB百分位 | valuation | index_valuation |

### 因子类别

| 类别 | 说明 |
|------|------|
| volume | 量能类因子，衡量成交活跃度 |
| momentum | 动量类因子，衡量价格趋势 |
| volatility | 波动率类因子，衡量风险水平 |
| flow | 资金流类因子，衡量申赎动向 |
| valuation | 估值类因子，衡量指数估值水平 |

## Plugin 层

`plugins/` 包含：

- **`base.py`** — StrategyPlugin Protocol（re-exports domain models）
- **`registry.py`** — StrategyRegistry 注册表，包含 `has_decision_pipeline()` 方法
- **`builtins/`** — 4 个内置策略插件

Plugins implement the `StrategyPlugin` Protocol (structural subtyping — no inheritance required). Required interface:

- **Metadata attributes:** `strategy_id`, `display_name`, `version`, `frequency`, `asset_scope`, `description`
- **Methods:** `parameter_schema()`, `required_inputs()`, `factor_definitions()`, `signal_definition()`, `prepare_context()`, `run_for_universe()`, `explain_result()`
- **Optional decision pipeline methods** (checked via `hasattr`): `assess_market_timing()`, `rank_assets()`, `allocate_positions()`

Built-in plugins:

| 插件 | 策略逻辑 | 依赖的 domain 函数 |
|------|---------|-------------------|
| `three_factor_guard` | 三因子综合守卫 | volume_probability, direction_probability, share_probability, composite_probability, signal_level |
| `share_flow_monitor` | 份额流向监控 | share_probability, signal_level |
| `volume_breakout_daily` | 放量突破基线 | volume_probability, signal_level |
| `etf_allocation` | 资产配置策略（择时→轮动→仓位） | TimingSignal, AssetRanking, AllocationPlan |

### 决策管线（Decision Pipeline）

`etf_allocation` 插件实现了完整的资产配置决策管线：

```
assess_market_timing()  →  TimingSignal (regime: 进攻/防守/观望)
        ↓
rank_assets()           →  list[AssetRanking] (按综合得分排序)
        ↓
allocate_positions()    →  AllocationPlan (目标仓位比例)
```

- **择时评分**：估值(40%) + 趋势(40%) + 量能(20%) → 综合分 ≥65 进攻，≤35 防守，否则观望
- **轮动排名**：动量(60%) + 估值吸引力(40%) → 板块选择
- **仓位分配**：进攻 80%、中性 50%、防守 20% 总仓位，单只上限 30%，最多持 5 只

To add a strategy: create a plugin file in `plugins/builtins/`, implement the Protocol, register in `plugins/registry.py`.
