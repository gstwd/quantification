# 策略配置模型参考

> **维护状态：辅助示例，非 schema 契约。** 创建或修改配置前，必须以 `engine/config.py`、策略校验接口或 CLI `strategy validate` 的当前结果为准。

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

## ScoreConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `factors` | dict[str,float] | **是** | - | 因子权重，正=正向贡献，负=反向贡献 |
| `transforms` | dict[str,str] | 否 | `{}` | 每因子的变换函数名 |
| `missing_factor_strategy` | str | 否 | `"ignore"` | `ignore`(跳过)/`zero`(按最差处理)/`exclude`(评分前排除资产) |
| `scoring_mode` | str | 否 | `"absolute"` | 评分模式 |

### 评分模式详解

| 模式 | 行为 | 何时使用 |
|------|------|----------|
| `absolute` | 每资产独立评分，不进行比较 | 绝对评分策略，每资产有自己的入选门槛 |
| `rank` | 每个因子先做横截面百分位排名（并列取平均秩），加权合成后再排名 (0-100) | **轮动策略**：比较资产间相对强弱，如二八轮动 |
| `zscore` | 每个因子先做横截面 Z-Score，加权合成后再标准化并映射到 0-100 | 需要正态化处理的相对评分 |

## FilterConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `logic` | str | 否 | `"AND"` | `AND`(全满足)/`OR`(任一满足) |
| `rules` | list[FilterRule] | 否 | `[]` | 过滤规则列表 |
| `groups` | list[FilterConfig] | 否 | `[]` | 嵌套子规则组，与 `rules` 同级按 `logic` 组合，最多 3 层；如 `{"logic":"OR","rules":[乖离≤−15],"groups":[{"logic":"AND","rules":[RSI≤25, 乖离≤−10]}]}`。择时规则组、规则档位、条件仓位上限同样支持 |

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
| `sort_by` | str | 否 | `"score"` | 仅支持 `score`；要按某因子排序就把它放进 `score.factors` |
| `momentum_factor` / `valuation_factor` | str | 否 | null | 仅用于展示子排名，不参与排序 |
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

- 有 `timing_exposure`：按择时 regime（offensive/neutral/defensive）查表；**只写部分 regime 时缺失档位回退到 `default_exposure`**（不是默认三档表），校验给 warning，应三档写全
- 有择时信号但无 `timing_exposure`：使用默认三档表 `engine/portfolio._DEFAULT_TIMING_EXPOSURE`（进攻 0.80/观望 0.50/防守 0.20）
- 无择时：使用 `default_exposure`（默认 0.50）
- 配置 `timing.tiers` 且命中档位：直接取档位仓位（优先于以上查表），见 TimingTierConfig
- 配置 `timing.exposure_caps` 且命中：总仓位再取上限（多条命中取最小），在组合级风控之前施加

## TimingConfig

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `combine` | str | 否 | `weighted` | 综合方式：`weighted`=因子加权综合分过阈值；`rules`=按 `rules` 的进攻 / 防守规则组判定 |
| `factors` | dict[str,float] | 加权模式是 | `{}` | 择时因子权重（规则模式不使用，校验给 warning） |
| `transforms` | dict[str,str] | 否 | `{}` | 择时因子变换；加权模式下原值不在 0-100 的因子（收益率、RSRS、量比等，模板 `native_0_100=false`）**必须**配置，否则校验报错；规则模式比较原值、不使用变换 |
| `thresholds` | TimingThresholds | 否 | `TimingThresholds()` | 加权模式的择时阈值 |
| `rules` | TimingRulesConfig | 规则模式是 | `null` | `{offensive, defensive}` 两个规则组，结构同 FilterConfig（`logic` + `rules`）；判定顺序防守优先：防守组满足→defensive，否则进攻组满足→offensive，否则 neutral。规则比较市场级因子**原值**，缺失按 `missing_strategy`（pass=满足，fail/exclude=不满足） |
| `proxy_index_codes` | list[str] | 否 | `["000300"]` | 择时代理指数，按顺序取第一个让（同组）全部择时因子都有值的代理；无代理全覆盖时逐因子回退 |
| `factor_proxies` | dict[str,list[str]] | 否 | `{}` | 按因子指定代理（如 `{"pv_res": ["000852"]}`），未列出的因子用 `proxy_index_codes` |
| `on_degraded` | str | 否 | `neutral` | 择时因子全部缺失时的回退：`neutral` / `defensive` / `hold_previous`（沿用上一决策日有效 regime；实时由回放最近交易日得到）。降级回退不受状态层确认 / 最短持有约束 |
| `state` | TimingStateConfig | 否 | 恒等 | 状态层，见下表 |
| `tiers` | list[TimingTierConfig] | 否 | `[]` | 多档仓位，见下文；空列表=按 regime 查 `portfolio.timing_exposure` |
| `tier_base_exposure` | float | 动态档位时是 | `null` | `hold` / `exposure_delta` 档位在没有上一仓位（首日、上一日未命中档位）时的起点 [0, 1] |
| `exposure_caps` | list[TimingExposureCapConfig] | 否 | `[]` | 条件仓位上限：`{label, max_exposure, logic, rules, groups}`，规则比较择时代理上的因子原值（引用的因子与择时因子一起按代理现算），命中时总仓位不超过 `max_exposure`，多条命中取最小值。例：5 年 PE 分位 ≥ 80 → 不超过 0.5 |

### TimingStateConfig（择时状态层）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `hysteresis` | float | 0 | 滞回带（综合分点数，仅加权模式）：已处于进攻时综合分 ≥ `offensive − hysteresis` 就保持进攻，防守对称 |
| `hold_on_neutral` | bool | false | 候选 regime 为中性（无信号）时沿用上一有效 regime |
| `confirm_days` | int | 1 | 新 regime 需连续出现的决策日数才切换，1=当日切换 |
| `min_hold_days` | int | 0 | 切换后最短持有的决策日数 |
| `max_exposure_step` | float | null | 档位决定的仓位每个决策日最多变动的幅度（如 0.25=每次最多一档）；只约束 `tiers` 决定的仓位 |

施加顺序：滞回 → 无信号维持 → 确认 → 最短持有。默认值下为恒等变换。回测按决策日逐日推进；实时回放最近 250 个交易日重建状态（`engine/timing.TIMING_STATE_REPLAY_DAYS`）。

### TimingTierConfig（多档仓位）

按列表顺序匹配，**首个命中**的档位决定目标总仓位（取代按 regime 查表，含 `regime_rules` 覆盖的 `timing_exposure`），再经组合级风控上限裁剪；都未命中或择时降级时回退 regime 仓位表。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `label` | str | `""` | 档位展示名 |
| `exposure` | float | null | 命中时的固定目标总仓位 [0, 1]；与 `hold`、`exposure_delta` **三选一** |
| `hold` | bool | false | 命中时维持上一仓位（灰区不动、中枢重置：灰区档 `hold`，中枢档固定 0.5） |
| `exposure_delta` | float | null | 命中时在上一仓位上增减 [−1, 1]（如 +0.25 加一档），结果夹在 `[min_exposure, max_exposure]`（默认 0~1） |
| `min_score` | float | null | 加权模式：综合分 ≥ 该值命中；各档必须严格降序。regime 仍由 `thresholds` 判定 |
| `regime` | str | null | 规则模式必填：命中该档时的 regime |
| `logic` / `rules` / `groups` | — | `AND` / `[]` / `[]` | 规则模式的命中规则组（同 FilterConfig，比较原值，可嵌套）；与 `rules.offensive/defensive` 互斥 |

状态层只约束 regime：沿用前一 regime（滞回 / 确认中 / 无信号维持 / 最短持有）时同时沿用上一档位；同 regime 内换档当日生效。`hold_on_neutral` 对 neutral 档位同样生效（命中中性档位也会沿用前值，校验给 warning）。例：中证1000 年线乖离 5 档 = 规则模式 `≤−20→100%`、`≤−15→75%`、`≥25→0%`、`≥15→25%`、`−5~5→50%`（neutral），灰区不命中按中性仓位。
需要"灰区维持上次仓位、回到中枢才重置"时，把灰区写成 `hold: true` 的档位（而不是全局 `hold_on_neutral`，后者也会吞掉中性中枢档）；"恐慌日加一档"写成 `exposure_delta: 0.25` 的档位；"每周最多动一档"用 `state.max_exposure_step: 0.25`。动态档位依赖上一仓位，实时路径会回放历史重建。

### TimingThresholds

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `offensive` | float | 65.0 | ≥此值 → 进攻 |
| `defensive` | float | 35.0 | ≤此值 → 防守（中间 → 观望） |

择时使用市场级因子（默认取沪深300 的因子值作为市场代理）。原值即 0-100 的模板（RSI、市场宽度、PE/PB/ERP 百分位）可不配变换。

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
