# 常见策略模式与配置模板

把自然语言策略描述映射到引擎配置的标准模式。下面每个模板都是完整的 `config_json`，可直接用
`strategy validate --file` 校验；使用时替换指数代码与参数，字段说明见 `config_model.md`，因子见 `factors.md`。

## 模式一：横截面轮动（如二八轮动）

**特征**：比较多个资产的相对强弱，持有最强者。关键：`scoring_mode: rank`、`top_n`、`winner_take_all` 或 `equal_weight`，
满仓需显式 `default_exposure: 1.0`。

```json
{
  "index_codes": ["000300", "000905"],
  "factor_aliases": {"mom20": {"template_id": "return", "params": {"period": 20}}},
  "score": {"factors": {"mom20": 1.0}, "scoring_mode": "rank"},
  "filters": {"logic": "AND", "rules": [{"factor": "mom20", "op": "gte", "value": 0}]},
  "rank": {"top_n": 1},
  "portfolio": {"method": "winner_take_all", "default_exposure": 1.0},
  "risk": {"max_asset_weight": 1.0}
}
```

过滤 `mom20 >= 0` 实现"都跌就空仓"：全部被过滤时没有入选资产。`winner_take_all` 满仓单一资产时要把 `risk.max_asset_weight`
放到 1.0，否则被单资产上限（缺省 0.3）裁剪。

## 模式二：多因子打分

**特征**：综合动量、估值、低波等维度给每个资产打分。absolute 模式要用变换统一量纲；方向只表达一次。

```json
{
  "index_codes": ["000300", "000905", "000852", "399006", "000688"],
  "factor_aliases": {
    "mom20": {"template_id": "return", "params": {"period": 20}},
    "vol20": {"template_id": "volatility", "params": {"period": 20}}
  },
  "score": {
    "factors": {"mom20": 1.0, "pe_percentile": 0.5, "vol20": -0.5},
    "transforms": {
      "mom20": "momentum_score",
      "pe_percentile": "invert_percentile",
      "vol20": "linear(5,40)"
    },
    "scoring_mode": "absolute",
    "missing_factor_strategy": "ignore"
  },
  "rank": {"top_n": 3},
  "portfolio": {"method": "score_weight", "default_exposure": 1.0},
  "risk": {"max_asset_weight": 0.4}
}
```

- `pe_percentile` 用 `invert_percentile` 后"便宜 = 高分"，配**正**权重；
- 波动率原值不是 0-100，先 `linear(5,40)` 映射，再用**负**权重表示"低波动高分"；
- 估值因子在部分指数上为空，`missing_factor_strategy: ignore` 让缺失因子不参与该资产的加权。
- 换成 `scoring_mode: rank` 时不需要变换，负权重同样表示"越小越好"。

## 模式三：趋势跟踪 + 空仓

**特征**：价格在均线之上才持有，否则空仓。用跨因子比较 `compare_to`。

```json
{
  "index_codes": ["000300", "000905"],
  "factor_aliases": {
    "mom20": {"template_id": "return", "params": {"period": 20}},
    "sma60": {"template_id": "sma", "params": {"period": 60}}
  },
  "score": {"factors": {"mom20": 1.0}, "scoring_mode": "rank"},
  "filters": {
    "logic": "AND",
    "rules": [{"factor": "close_price", "op": "gt", "compare_to": "sma60"}]
  },
  "rank": {"top_n": 1},
  "portfolio": {"method": "equal_weight", "default_exposure": 1.0},
  "risk": {"max_asset_weight": 1.0}
}
```

## 模式四：择时 + 仓位控制

**特征**：根据市场环境（估值、趋势）调节总仓位。加权模式按综合分过阈值判定 regime，`timing_exposure` 给出各 regime 仓位。

```json
{
  "index_codes": ["000300", "000905", "000852"],
  "factor_aliases": {
    "mom20": {"template_id": "return", "params": {"period": 20}},
    "mom60": {"template_id": "return", "params": {"period": 60}}
  },
  "timing": {
    "factors": {"pe_percentile": 1.0, "mom60": 1.0},
    "transforms": {"pe_percentile": "invert_percentile", "mom60": "momentum_score"},
    "thresholds": {"offensive": 60, "defensive": 35},
    "proxy_index_codes": ["000300"],
    "state": {"hysteresis": 5, "confirm_days": 2}
  },
  "score": {"factors": {"mom20": 1.0}, "scoring_mode": "rank"},
  "rank": {"top_n": 2},
  "portfolio": {
    "method": "equal_weight",
    "timing_exposure": {"offensive": 0.9, "neutral": 0.5, "defensive": 0.1}
  }
}
```

状态层 `hysteresis` / `confirm_days` 抑制阈值附近的来回切换；`timing_exposure` 应三档写全。

## 模式五：多档择时仓位（规则分档）

**特征**："跌得越深仓位越高"这类按指标区间加减仓。规则模式每档一组规则并声明 regime，首个命中的档位决定总仓位。

```json
{
  "index_codes": ["000852"],
  "factor_aliases": {"dev250": {"template_id": "ma_deviation", "params": {"period": 250}}},
  "timing": {
    "combine": "rules",
    "proxy_index_codes": ["000852"],
    "tiers": [
      {"label": "深度超跌", "regime": "offensive", "exposure": 1.0,
       "rules": [{"factor": "dev250", "op": "lte", "value": -20}]},
      {"label": "超跌", "regime": "offensive", "exposure": 0.75,
       "rules": [{"factor": "dev250", "op": "lte", "value": -15}]},
      {"label": "过热", "regime": "defensive", "exposure": 0.0,
       "rules": [{"factor": "dev250", "op": "gte", "value": 25}]},
      {"label": "偏热", "regime": "defensive", "exposure": 0.25,
       "rules": [{"factor": "dev250", "op": "gte", "value": 15}]}
    ],
    "state": {"min_hold_days": 5}
  },
  "score": {"factors": {"dev250": 1.0}},
  "rank": {"top_n": 1},
  "portfolio": {
    "method": "equal_weight",
    "timing_exposure": {"offensive": 1.0, "neutral": 0.5, "defensive": 0.0}
  },
  "risk": {"max_asset_weight": 1.0}
}
```

- 都未命中时按 regime 仓位表（这里中性 0.5）；
- "灰区维持上次仓位"写成 `hold: true` 的档位，"恐慌日加一档"写成 `exposure_delta: 0.25` 的档位（含这两类动态档位时必须设
  `timing.tier_base_exposure`），"每次最多动一档"用 `state.max_exposure_step: 0.25`；
- "估值过高时总仓位不超过 50%"用 `timing.exposure_caps`。

## 模式六：双均线交叉

**特征**：短均线在长均线之上（金叉）时持有。

```json
{
  "index_codes": ["000300", "000905"],
  "factor_aliases": {
    "mom20": {"template_id": "return", "params": {"period": 20}},
    "sma5": {"template_id": "sma", "params": {"period": 5}},
    "sma20": {"template_id": "sma", "params": {"period": 20}}
  },
  "score": {"factors": {"mom20": 1.0}, "scoring_mode": "rank"},
  "filters": {
    "logic": "AND",
    "rules": [
      {"factor": "sma5", "op": "gt", "compare_to": "sma20"},
      {"factor": "mom20", "op": "gte", "value": 0}
    ]
  },
  "rank": {"top_n": 1},
  "portfolio": {"method": "winner_take_all", "default_exposure": 1.0},
  "risk": {"max_asset_weight": 1.0}
}
```

变体：通道突破 `{"factor": "close_price", "op": "gt", "compare_to": "<donchian_high 别名>"}`（通道取当日之前 N 日极值，不含当日）。
跨因子比较的两个因子要同一量纲（价格对价格、百分比对百分比）。

## 模式七：风控叠加与低换手

**特征**：控制组合波动与单资产回撤，减少名次边缘的来回换仓。

```json
{
  "index_codes": ["000300", "000905", "000852", "399006"],
  "factor_aliases": {
    "mom60": {"template_id": "return", "params": {"period": 60}},
    "vol60": {"template_id": "volatility", "params": {"period": 60}}
  },
  "score": {"factors": {"mom60": 1.0}, "scoring_mode": "rank"},
  "rank": {"top_n": 2, "buffer_n": 1},
  "portfolio": {"method": "inverse_vol", "vol_factor": "vol60", "default_exposure": 1.0},
  "risk": {
    "max_asset_weight": 0.6,
    "vol_target": {"target": 0.15, "window": 60},
    "trailing_stop": {"threshold": 0.12, "cooldown_days": 20}
  },
  "rebalance": {
    "selection": {"frequency": "weekly", "day_of_week": 4},
    "band": {"min_turnover": 0.05}
  }
}
```

`buffer_n`、`band`、`vol_target`、`drawdown_stop`、`trailing_stop` 都依赖持仓路径：止损触发后下一决策日强制成交，实时决策会回放
历史重建状态。

## 反模式与常见错误

1. **在过滤里检查变换后的得分**：过滤比较的是因子原值；`"factor": "score"` 不存在。
2. **方向反转两次**：`invert_percentile` 再配负权重（absolute 模式负权重会再取 `100 − 得分`），两次反转相互抵消。
3. **期望按单个因子排序**：`rank.sort_by` 只支持 `score`；枚举字段（`order`、`logic` 等）大小写敏感，写错在校验期被拒绝。
4. **winner_take_all 配 top_n > 1**：只有第一名拿仓位，排名逻辑浪费；同时记得放开 `max_asset_weight`。
5. **忘了无择时策略缺省半仓**：`default_exposure` 缺省 0.5，想满仓要显式写 1.0。
6. **absolute 模式不配变换**：动量（%）与估值（0-100）量纲差距大，会让某个因子主导评分；rank / zscore 模式已逐因子标准化。
7. **市场级因子放进横截面评分**：`breadth_ma_pct`、`macro_series`、`market_series` 对所有指数同值，用于评分没有区分度，校验也会拒绝。
8. **使用带周期后缀的旧因子 ID**（`return_20d`、`ma_5d`）：不存在，用模板 ID + `factor_aliases`。
