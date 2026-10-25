# 因子目录

> **维护状态：历史示例，非当前模板目录。** 当前因子以 `FactorTemplateRegistry`、`GET /api/factors` 或 CLI/API 校验结果为准。因子值不落库；使用参数化模板时应在 `factor_aliases` 显式声明模板与参数。

所有可用因子的 ID、含义和参数。因子值基于指数数据计算（`index_factor_value` 表）。

## 动量类 (momentum)

| factor_id | 名称 | 含义 | lookback_days |
|-----------|------|------|---------------|
| `return_5d` | 5日收益率 | (今收/5日前收 - 1) × 100，数据不足时返回 None | 15 |
| `return_20d` | 20日收益率 | (今收/20日前收 - 1) × 100 | 40 |
| `return_60d` | 60日收益率 | (今收/60日前收 - 1) × 100 | 90 |

## 波动率类 (volatility)

| factor_id | 名称 | 含义 | lookback_days |
|-----------|------|------|---------------|
| `volatility_20d` | 20日年化波动率 | std(日收益, ddof=1) × sqrt(252) × 100，需 21 个收盘价 | 40 |

## 成交量类 (volume)

| factor_id | 名称 | 含义 | lookback_days |
|-----------|------|------|---------------|
| `volume_ratio_20d` | 20日量比 | 当日成交量 / 此前20日均量，>1 = 放量，历史不足20日时返回 None | 40 |

## 估值类 (valuation)

| factor_id | 名称 | 含义 | lookback_days |
|-----------|------|------|---------------|
| `pe_percentile` | PE百分位 | PE(TTM) 历史百分位 (0-100)，越低越便宜。仅宽基指数有数据 | 730 |
| `pb_percentile` | PB百分位 | PB 历史百分位 (0-100)，越低越便宜 | 730 |
| `valuation_percentile` | 滚动估值分位 | 参数 `field`=pe/pb/mean、`window`（默认 1250 交易日）、`min_periods`（默认 250）。最近 N 日 PE/PB 滚动百分位 (0-100)，由原始估值现算；扩张窗口分位被早期样本压住（如中证1000 研究期最高仅 66）时用它让高分位阈值可触发 | ≈1.6×window |

## 技术指标类 (technical)

| factor_id | 名称 | 含义 | lookback_days |
|-----------|------|------|---------------|
| `ma_5d` | 5日均线 | 近5日收盘价简单移动平均 | 15 |
| `ma_10d` | 10日均线 | 近10日收盘价简单移动平均 | 20 |
| `ma_20d` | 20日均线 | 近20日收盘价简单移动平均 | 35 |
| `ma_60d` | 60日均线 | 近60日收盘价简单移动平均 | 95 |
| `atr_14d` | 14日ATR | 14日平均真实波幅（海龟交易），TR=max(H-L, | H-prevC|, | L-prevC|) | 26 |
| `donchian_20d_high` | 20日通道上轨 | 当日之前20日最高价（不含当日） | 35 |
| `donchian_20d_low` | 20日通道下轨 | 当日之前20日最低价（不含当日） | 35 |
| `rsi_14d` | 14日RSI | 简单平均 RSI (0-100)，>70 超买，<30 超卖，无涨跌为 50 | 26 |

## 隐式因子（非注册 FactorComputer，由 ContextBuilder 直接注入）

| factor_id | 含义 | 来源 |
|-----------|------|------|
| `change_pct` | 当日涨跌幅 (%) | `index_daily_bar.change_pct` |
| `close_price` | 当日收盘价 | `index_daily_bar.close_price` |

## 派生因子（`expr` 模板）

在已有因子之上做四则与函数组合，现算、不落库。在 `factor_aliases` 中声明：

```json
"factor_aliases": {
  "dev250": {"template_id": "ma_deviation", "params": {"period": 250}},
  "dev60":  {"template_id": "ma_deviation", "params": {"period": 60}},
  "dev_spread": {"template_id": "expr", "params": {"expr": "dev250 - dev60"}}
}
```

- 语法白名单：数值常量、因子引用（别名或模板 ID）、`+ - * / **`、一元正负号、`abs / min / max / log / sqrt / sign`；不允许比较、下标、属性等其他语法；最长 200 字符。可嵌套引用其他派生因子（最多 4 层，循环引用被拒绝）。
- 任一依赖缺失、除零或定义域错误时值为 None（与因子缺失同一语义）。依赖按各自模板的回望窗口现算；用于择时时同样按择时代理取值，原值不在 0-100 时要配变换。
- **诊断前置门槛**：派生因子进入 `research batch` 变体或 `optimization start` 候选前，必须先用 `research factor-ic` 或 `research buckets` 诊断过（按「表达式 + 依赖口径」的指纹记入研究台账，与别名名字无关），否则命令直接拒绝。尚未进入策略的组合用 `--strategy <提供别名的策略> --factor expr --params '{"expr": "..."}' [--aliases '<新增别名 JSON>']` 诊断。

## 注意事项

- 所有因子以 `index_code` 为计算单位（如 `000300`、`000905`），不是 ETF 代码
- 估值因子（pe_percentile、pb_percentile）仅宽基指数有数据，行业/主题指数通常返回 None
- `return_*d` 因子数据不足时返回 None（与返回 0.0 不同），过滤规则遇到 None 会自动失败

## 成分聚合、宏观与日频市场指标模板

| 模板 | 参数 | 说明 |
|---|---|---|
| `constituent_turnover` | field（turnover_rate_f / turnover_rate）、window、min_coverage | 成分股加权换手率 N 日均值（%），个股每日指标聚合，资产级 |
| `constituent_mainflow` | window、min_coverage | 成分股主力（大单+特大单）净流入 / 全部买入额（%），资产级 |
| `constituent_valuation` | field（pe_ttm / pb / dv_ttm）、stat（median / harmonic）、min_coverage | 成分股估值汇总，资产级；配合 expr 的 ts_rank 得到估值分位 |
| `macro_series` | code（m1_yoy / m2_yoy / sf_stock / pmi / cpi / lpr1y / lpr5y）、mode（level / change / pct_change）、months | 市场级，按发布滞后可见；只用于择时 / 过滤 |
| `market_series` | code（cgb10y、margin_*、fut_*_basis(_ann)、opt_*_pcr_*）、mode、window | 市场级日频序列；期权 2019-12 起、IM/MO 2022 年起才有数据，早期为缺失 |
| `erp` / `erp_percentile` | rate_source（lpr 缺省 / cgb10y） | 无风险利率可改为 10 年期国债收益率 |

成分聚合因子首次装配需按日期区间扫描个股表（数据库冷缓存时每张表约 20 秒），实时决策引用时注意延迟。
