# 因子模板目录

系统共 42 个因子模板，以 `apps/api/src/quant_etf_api/factors/catalog.py`（或 `GET /api/factors/`）为准。因子值不落库，
按策略实际参数从原始数据现算。

## 引用方式

策略里引用的是**引用名**：

- 直接写模板 ID → 用默认参数，如 `"pe_percentile"`、`"return"`（= 20 日收益）；
- 需要别的参数 → 在 `factor_aliases` 声明别名：

```json
"factor_aliases": {
  "mom20": {"template_id": "return", "params": {"period": 20}},
  "mom60": {"template_id": "return", "params": {"period": 60}},
  "sma5":  {"template_id": "sma", "params": {"period": 5}},
  "sma20": {"template_id": "sma", "params": {"period": 20}}
}
```

同一模板配不同参数必须用别名区分；同模板同参数只算一次。**不存在 `return_20d`、`ma_5d`、`volatility_20d` 这类带周期后缀的
因子 ID**，写了会被校验拒绝。

## 适用位置（usage）

每个模板只能放进它声明的模块，校验按此拒绝错位引用。下表"位置"一列：T = 择时、S = 评分、F = 过滤、R = 排名。

- 价格位置类 `sma`、`close_price`、`atr`、`donchian_high`、`donchian_low`、`monthly_ma`、`pv_resonance` **只能用于 T / F**
  （它们的原值是价格水平或开仓信号，不适合横截面评分）；
- 市场级模板 `breadth_ma_pct`、`macro_series`、`market_series` 只能用于 T / F，同一交易日所有指数取值相同；
- `amount_volatility`、`price_volume_corr`、`index_diffusion_ratio`、`rrg_industry_match_score` 只能用于 S / F / R；
- 其余模板 T / S / F / R 均可。

## 模板表

参数括号内为默认值；取值范围见因子详情页（越界在保存前被拒绝）。

### 动量（momentum）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `return` | `period`（20） | TSFR | N 日累计收益率：(今收 / N 日前收 − 1) × 100 |
| `sharpe` | `period`（60）、`volatility_period`（20） | TSFR | 风险调整动量：N 日收益 / 年化波动率 |
| `price_position_ir` | `period`（60，≥ 25） | TSFR | 日内位置 (收−开)/(高−低) 的均值 / 标准差，需完整 OHLC |
| `low_amplitude_momentum` | `period`（160）、`low_amplitude_ratio`（0.70） | TSFR | 振幅最低的那部分交易日的收益之和 |
| `rsrs` | `n`（18）、`m`（250） | TSFR | 高低价 OLS 斜率 × R² 的滚动标准分，正数表示支撑强于阻力；放择时时配 `rsrs_score` 变换 |
| `monthly_return` | `months`（2） | TSFR | 近 N 月收益率（月线由日线聚合） |
| `monthly_up_streak` | — | TSFR | 连续收阳月数，正 = 连阳、负 = 连阴，不足 2 个月为空 |
| `days_down_up` | — | TSFR | \|连涨天数 − 连跌天数\| − 1，衡量趋势持续性 |

### 波动率（volatility）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `volatility` | `period`（20） | TSFR | N 日年化波动率：std(日收益, ddof=1) × √252 × 100 |
| `return_std` | `period`（20） | TSFR | N 日收益率标准差 × 100，**不年化** |
| `high_low` | `period`（63） | TSFR | N 日区间宽度：(最高收盘 − 最低收盘) / 最低收盘 × 100 |

### 量能（volume）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `volume_ratio` | `period`（20） | TSFR | 当日成交量 / 此前 N 日均量，> 1 放量，历史不足为空 |
| `amount_ratio` | `period`（20） | TSFR | 当日成交额 / 此前 N 日均额 |
| `amount_volatility` | `period`（20） | SFR | −Std(成交额, N) / Mean(成交额, N) |
| `price_volume_corr` | `period`（20） | SFR | −Corr(收盘, 成交量, N) |
| `pv_resonance` | `bma_window`（50）、`ama_window`（100）、`lag`（3）、`fast_window`（5）、`slow_window`（90）、`bull_threshold`（1.125）、`bear_threshold`（1.275） | TF | 价量共振比，> 1 为开仓信号；配 `resonance_score` |

### 估值（valuation）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `pe_percentile` / `pb_percentile` | — | TSFR | PE / PB 全历史百分位（0-100），越低越便宜 |
| `valuation_percentile` | `field`（pe / pb / mean）、`window`（1250）、`min_periods`（250） | TSFR | 最近 N 个交易日的滚动估值分位；全历史分位被早期样本压住时用它让高分位阈值可触发 |
| `erp` | `rate_source`（lpr / cgb10y） | TSFR | 股权风险溢价：1/PE × 100 − 无风险利率 |
| `erp_percentile` | `rate_source`（lpr / cgb10y） | TSFR | ERP 在前 2 年分布中的百分位，> 80 表示股票相对债券极具吸引力 |

### 技术（technical）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `sma` | `period`（20） | TF | N 日简单均线（价格水平，用于跨因子比较如 `close_price > sma`） |
| `ma_deviation` | `period`（60） | TSFR | 均线乖离率：(今收 − MA) / MA × 100，配 `trend_score` |
| `drawdown` | `period`（60） | TSFR | 相对 N 日最高收盘的回撤（%），配 `drawdown_score` |
| `rsi` | `period`（14） | TSFR | 简单平均 RSI（0-100），> 70 超买、< 30 超卖 |
| `atr` | `period`（14） | TF | 平均真实波幅（价格单位） |
| `donchian_high` / `donchian_low` | `period`（20） | TF | 当日之前 N 日最高 / 最低价（不含当日） |
| `monthly_ma` | `months`（10） | TF | 近 N 个月线收盘均值 |
| `days_beyond_upper_lower` | `period`（21） | TSFR | 高于 mean+std 天数 − 低于 mean−std 天数，可作过热 / 过冷过滤 |
| `breadth_ma_pct` | `period`（20） | TF（市场级） | 全市场活跃指数中站上自身 N 日均线的占比（0-100） |

### 价格（price）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `close_price` | — | TF | 当日收盘价 |
| `change_pct` | — | TSFR | 当日涨跌幅（%） |

### 宏观与市场（macro / market，市场级）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `pmi_momentum` | `months`（3） | TSFR | 已公布最新 PMI − 约 N 月前 PMI（参考月次月 1 日起可见） |
| `macro_series` | `code`（m1_yoy / m2_yoy / sf_stock / pmi / cpi / lpr1y / lpr5y）、`mode`（level / change / pct_change）、`months`（3） | TF | 已公布最新一期，或相对 N 月前的差值 / 变化率；按发布滞后可见 |
| `market_series` | `code`（cgb10y、margin_*、fut_*_basis(_ann)、opt_*_pcr_*）、`mode`、`window`（20） | TF | 日频市场指标的 N 日均值或变化；期权 2019-12 起、IM / MO 2022 年起才有数据 |

### 成分股与行业（constituent / breadth / relative_strength）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `constituent_turnover` | `field`（turnover_rate_f / turnover_rate）、`window`（20）、`min_coverage`（0.8） | TSFR | 成分股换手率按指数权重加权的 N 日均值（%） |
| `constituent_mainflow` | `window`（5）、`min_coverage`（0.8） | TSFR | 成分股主力（大单 + 特大单）净流入 / 全部买入额（%） |
| `constituent_valuation` | `field`（pe_ttm / pb / dv_ttm）、`stat`（median / harmonic）、`min_coverage`（0.8） | TSFR | 成分股估值中位数或权重调和平均；可配 `expr` 的 `ts_rank` 得到估值分位 |
| `index_diffusion_ratio` | 固定口径 | SFR | 指数成分股站上长期均线的加权占比 |
| `rrg_industry_match_score` | 固定口径 | SFR | 指数行业暴露与 RRG 选中行业集合的重合度 |

### 派生（derived）

| 模板 ID | 参数 | 位置 | 含义 |
|---------|------|------|------|
| `expr` | `expr`（表达式文本） | TSFR | 在已有因子上做组合，见下 |

## 派生因子（`expr`）

```json
"factor_aliases": {
  "dev250": {"template_id": "ma_deviation", "params": {"period": 250}},
  "dev60":  {"template_id": "ma_deviation", "params": {"period": 60}},
  "dev_spread": {"template_id": "expr", "params": {"expr": "dev250 - dev60"}},
  "mom20": {"template_id": "return", "params": {"period": 20}},
  "mom_z": {"template_id": "expr", "params": {"expr": "ts_zscore(mom20, 60)"}}
}
```

- 语法白名单（`factors/expression.py`）：数值常量、因子引用（别名或模板 ID）、`+ - * / **`、一元正负号、`abs / min / max / log /
  sqrt / sign`，时序函数 `delay(x, n)` / `delta(x, n)`（n ≥ 1）与 `ts_mean / ts_std / ts_zscore / ts_rank(x, n)`（n ≥ 2，窗口含当日），
  窗口为 ≤ 250 的整数字面量；表达式 ≤ 200 字符、≤ 60 个语法节点；可引用其他派生因子，最多 4 层，循环引用被拒绝。
- 不提供截面函数（cs_rank 等）：截面标准化用评分 `scoring_mode: rank|zscore`。
- 任一依赖缺失、除零或定义域错误时值为空。派生实例回望 = 依赖回望 + 窗口折算，超过回测预加载在校验期拒绝。
- **诊断前置门槛**：派生因子进入 `research batch` 变体或 `optimization start` 候选前，必须先用 `research factor-ic` 或
  `research buckets` 诊断（按"表达式 + 依赖口径"的指纹记入台账，与别名无关）。尚未进入策略的组合用
  `--strategy <提供别名的策略> --factor expr --params '{"expr": "..."}' [--aliases '<新增别名 JSON>']` 诊断。

## 注意事项

- 因子以 `index_code` 为计算单位，不是 ETF 代码。
- **市场级因子**同一交易日所有指数取值相同：放 `timing`（按择时代理取值）或 `filters`（全市场开关），不要放进 `score`
  配 `rank` / `zscore`，横截面标准化后没有区分度。
- **估值覆盖**：乐咕乐股覆盖 12 个指数（沪深300、上证50、中证500、中证1000、中证800、上证380、上证180、深证100、创业板50、
  深证红利、上证红利、中证100），PE 与 PB 都有；其他中证系指数只有 PE；深证系等无估值源的指数为空。行业 / 主题指数的估值因子
  常为空，用 `missing_factor_strategy` 与过滤的 `missing_strategy` 明确缺失语义。
- **市场宽度**：引用 `breadth_ma_pct` 时回测会加载全市场活跃指数行情，与实时口径一致。
- **OHLC 依赖**：`rsrs`、`price_position_ir`、`atr`、`donchian_*`、`low_amplitude_momentum` 需要最高 / 最低价（`price_position_ir`
  还需开盘价）。行情缺 OHLC 时这些因子为空，回测候选池会剔除该标的；在 `score` 中引用且这类标的多时会显著压缩横截面。
- **`rsrs` 预热期**：需要 n + m − 1（默认 267）个交易日历史，早期样本为空；放在择时时按有值因子重新归一化权重。
- **成分聚合因子**首次装配需按日期区间扫描个股表，实时决策引用时注意延迟。
