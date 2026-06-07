# 策略配置模型参考

策略配置 JSON 的完整结构。创建时 `strategy_id`、`display_name` 等元数据在 API 请求顶层，引擎配置在 `config_json` 字段内。

## 顶层 StrategyConfig 字段 (config_json 内)

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `index_codes` | list[str] | 否 | `[]` | 限定指数范围，如 `["000300","000905"]`。非空时仅在这些指数上运行 |
| `timing` | TimingConfig | 否 | null | 择时配置，null = 不择时 |
| `score` | ScoreConfig | **是** | - | 评分配置 |
| `filters` | FilterConfig | 否 | null | 过滤配置，null = 不过滤 |
| `rank` | RankConfig | 否 | `RankConfig()` | 排名配置 |
| `portfolio` | PortfolioConfig | 否 | null | 组合配置，null = 仅信号模式 |
| `risk` | RiskConfig | 否 | null | 风控配置 |
| `rebalance` | RebalanceConfig | 否 | null | 调仓频率配置 |
| `benchmark` | BenchmarkConfig | 否 | null | 基准对比配置 |
| `transaction_cost` | TransactionCostConfig | 否 | null | 交易成本配置 |

## ScoreConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `factors` | dict[str,float] | **是** | - | 因子权重，正=正向贡献，负=反向贡献 |
| `transforms` | dict[str,str] | 否 | `{}` | 每因子的变换函数名 |
| `missing_factor_strategy` | str | 否 | `"ignore"` | `ignore`(跳过)/`zero`(视为0)/`exclude`(排除资产) |
| `scoring_mode` | str | 否 | `"absolute"` | 评分模式 |

### 评分模式详解

| 模式 | 行为 | 何时使用 |
|------|------|----------|
| `absolute` | 每资产独立评分，不进行比较 | 绝对评分策略，每资产有自己的入选门槛 |
| `rank` | 横截面百分位排名 (0-100) | **轮动策略**：比较资产间相对强弱，如二八轮动 |
| `zscore` | 横截面 Z-Score 标准化后映射到 0-100 | 需要正态化处理的相对评分 |

## FilterConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `logic` | str | 否 | `"AND"` | `AND`(全满足)/`OR`(任一满足) |
| `rules` | list[FilterRule] | 否 | `[]` | 过滤规则列表 |

### FilterRule

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `factor` | str | **是** | 因子 ID |
| `op` | str | **是** | 操作符: `gt`/`lt`/`gte`/`lte`/`eq`/`neq`/`between` |
| `value` | float 或 [float,float] | 条件* | 阈值，`between` 时传 `[min,max]`。与 `compare_to` 二选一 |
| `compare_to` | str | 条件* | 被比较因子 ID，用于跨因子比较（如 `ma_5d > ma_20d`）。与 `value` 二选一 |

> *`value` 和 `compare_to` 必须恰好提供一个，不能同时设置也不能同时为空。

#### 跨因子比较

`compare_to` 字段允许将同一资产的两个因子值直接比较，无需固定阈值：

```json
// 金叉：短均线 > 长均线
{"factor": "ma_5d", "op": "gt", "compare_to": "ma_20d"}

// 价格在均线之上
{"factor": "close_price", "op": "gt", "compare_to": "ma_60d"}
```

**注意事项**：
- `between` 操作符不支持跨因子比较
- 比较的两个因子应属于同一维度（如同为价格类、同为百分比类），否则结果无意义
- 任一因子值为 None 时规则自动失败

### 过滤器行为注意

- 过滤器在**评分之后、排名之前**执行
- 过滤的是**因子原始值**（非变换后的得分），因为直接从 `asset_factors` 取值
- 因子值为 None 时自动失败（返回 False）
- 所有资产都被过滤后 → 空字典 → 排名为空 → 仓位为空 → 空仓

## RankConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `sort_by` | str | 否 | `"score"` | 排序字段 |
| `order` | str | 否 | `"desc"` | `desc`(降序)/`asc`(升序) |
| `top_n` | int | 否 | null | 取前 N 名，null = 全选 |
| `bottom_n` | int | 否 | null | 取后 N 名（与 top_n 互斥） |

## PortfolioConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `method` | str | **是** | - | 权重方法 |
| `timing_exposure` | dict[str,float] | 否 | null | 择时 regime → 总仓位映射 |
| `default_exposure` | float | 否 | 0.50 | 无择时时的默认总仓位 |

### 权重方法

| 方法 | 行为 |
|------|------|
| `equal_weight` | 等权分配：总仓位 / 资产数 |
| `score_weight` | 得分加权：权重 ∝ 得分，过滤掉 ≤0 分的资产 |
| `winner_take_all` | 赢家通吃：全部仓位给第 1 名，适用于集中持仓策略 |

### 择时仓位控制

- 有 `timing_exposure`：按择时 regime（offensive/neutral/defensive）查表
- 有择时信号但无 `timing_exposure`：使用硬编码默认值（进攻 0.80/观望 0.50/防守 0.20）
- 无择时：使用 `default_exposure`（默认 0.50）

## TimingConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `factors` | dict[str,float] | **是** | - | 择时因子权重 |
| `transforms` | dict[str,str] | 否 | `{}` | 择时因子变换 |
| `thresholds` | TimingThresholds | 否 | `TimingThresholds()` | 择时阈值 |

### TimingThresholds

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `offensive` | float | 65.0 | ≥此值 → 进攻 |
| `defensive` | float | 35.0 | ≤此值 → 防守（中间 → 观望） |

择时使用市场级因子（默认取沪深300 的因子值作为市场代理）。

## RiskConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_asset_weight` | float | 0.30 | 单资产仓位上限 (0-1] |
| `max_portfolio_exposure` | float | 1.0 | 组合总仓位上限 |
| `min_cash_ratio` | float | 0.0 | 最低现金比例 [0-1) |

## RebalanceConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `frequency` | str | `"daily"` | `daily`/`weekly`/`monthly` |
| `day_of_week` | int | null | 周度：目标星期 (0=周一)。默认周五(4) |
| `day_of_month` | int | null | 月度：目标日 (1-31)。默认 1 号 |

非交易日自动顺延至下一交易日。

## BenchmarkConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `index_code` | str | `"000300"` | 基准指数代码 |
| `enable_equal_weight` | bool | true | 是否同时计算等权基准 |

## TransactionCostConfig

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `commission_rate` | float | 0.0003 | 佣金费率（默认万三） |
| `slippage_rate` | float | 0.001 | 滑点费率（默认千一） |
| `apply_to_turnover` | bool | true | 仅对换仓部分收费 |
