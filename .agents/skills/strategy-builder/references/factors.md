# 因子目录

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
| `volume_ratio_20d` | 20日量比 | 当日成交量 / 近20日均量，>1 = 放量，数据不足时返回 None | 40 |

## 估值类 (valuation)

| factor_id | 名称 | 含义 | lookback_days |
|-----------|------|------|---------------|
| `pe_percentile` | PE百分位 | PE(TTM) 历史百分位 (0-100)，越低越便宜。仅宽基指数有数据 | 730 |
| `pb_percentile` | PB百分位 | PB 历史百分位 (0-100)，越低越便宜 | 730 |

## 技术指标类 (technical)

| factor_id | 名称 | 含义 | lookback_days |
|-----------|------|------|---------------|
| `ma_5d` | 5日均线 | 近5日收盘价简单移动平均 | 15 |
| `ma_10d` | 10日均线 | 近10日收盘价简单移动平均 | 20 |
| `ma_20d` | 20日均线 | 近20日收盘价简单移动平均 | 35 |
| `ma_60d` | 60日均线 | 近60日收盘价简单移动平均 | 95 |
| `atr_14d` | 14日ATR | 14日平均真实波幅（海龟交易），TR=max(H-L, | H-prevC|, | L-prevC|) | 26 |
| `donchian_20d_high` | 20日通道上轨 | 近20日最高价 | 35 |
| `donchian_20d_low` | 20日通道下轨 | 近20日最低价 | 35 |
| `rsi_14d` | 14日RSI | Wilder RSI (0-100)，>70 超买，<30 超卖 | 26 |

## 隐式因子（非注册 FactorComputer，由 ContextBuilder 直接注入）

| factor_id | 含义 | 来源 |
|-----------|------|------|
| `change_pct` | 当日涨跌幅 (%) | `index_daily_bar.change_pct` |
| `close_price` | 当日收盘价 | `index_daily_bar.close_price` |

## 注意事项

- 所有因子以 `index_code` 为计算单位（如 `000300`、`000905`），不是 ETF 代码
- 估值因子（pe_percentile、pb_percentile）仅宽基指数有数据，行业/主题指数通常返回 None
- `return_*d` 因子数据不足时返回 None（与返回 0.0 不同），过滤规则遇到 None 会自动失败
