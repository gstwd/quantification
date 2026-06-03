# Plugin System

## 三层关系

策略相关代码分为三层，依赖方向为 `domain ← factors ← plugins`：

```
┌─────────────┐
│   Plugin     │  编排层：组合多个因子结果，产出信号
│  (策略)      │  "量能50% + 方向20% + 份额30% → 三因子策略"
└──────┬───────┘
       │ 调用
┌──────▼───────┐
│   Factor      │  计算层：单一维度的数值计算
│  (因子)      │  "量比 → 量能概率 0~100"
└──────┬───────┘
       │ 使用
┌──────▼───────┐
│   Domain      │  规则层：纯业务规则、公式、阈值、枚举
│  (领域)      │  "量比>1.5 属于显著放量"、"得分≥70 为 HIGH"
└──────────────┘
```

| 层 | 职责 | 特征 |
|---|---|---|
| **Domain** | 纯业务规则、计算公式、阈值、值对象、枚举 | 无外部依赖（不 import SQLAlchemy/FastAPI） |
| **Factor** | 单因子计算（FactorComputer Protocol 实现） | 依赖 domain，被 FactorService 编排 |
| **Plugin** | 策略编排（StrategyPlugin Protocol 实现） | 依赖 domain，组合多个因子产出信号 |

## Domain 层

`domain/strategies/` 包含：

- **`models.py`** — `StrategyContextData`（策略执行上下文）和 `StrategyResult`（单 ETF 策略结果）数据类
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
- **`builtins/`** — 6 个内置因子计算器（volume_ratio_20d、return_5d/20d/60d、volatility_20d、share_delta_pct）

## Plugin 层

`plugins/` 包含：

- **`base.py`** — StrategyPlugin Protocol（re-exports domain models）
- **`registry.py`** — StrategyRegistry 注册表
- **`builtins/`** — 3 个内置策略插件

Plugins implement the `StrategyPlugin` Protocol (structural subtyping — no inheritance required). Required interface:

- **Metadata attributes:** `strategy_id`, `display_name`, `version`, `frequency`, `asset_scope`, `description`
- **Methods:** `parameter_schema()`, `required_inputs()`, `factor_definitions()`, `signal_definition()`, `prepare_context()`, `run_for_universe()`, `explain_result()`

Built-in plugins:

| 插件 | 策略逻辑 | 依赖的 domain 函数 |
|------|---------|-------------------|
| `three_factor_guard` | 三因子综合守卫 | volume_probability, direction_probability, share_probability, composite_probability, signal_level |
| `share_flow_monitor` | 份额流向监控 | share_probability, signal_level |
| `volume_breakout_daily` | 放量突破基线 | volume_probability, signal_level |

To add a strategy: create a plugin file in `plugins/builtins/`, implement the Protocol, register in `plugins/registry.py`.
