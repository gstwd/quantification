# 常见策略模式与配置模板

将自然语言策略描述映射到引擎配置的标准模式。

## 模式一：横截面轮动（如二八轮动）

**特征**：比较多个资产的相对强弱，选择最强的持有。

**关键配置**：
- `scoring_mode: "rank"` — 横截面排名比较
- `top_n: 1` 或 `top_n: N` — 选择前 N 名
- `winner_take_all` 或 `score_weight` — 仓位集中或分散

**模板**：
```json
{
  "index_codes": ["<候选指数1>", "<候选指数2>", ...],
  "score": {
    "factors": {"<动量因子>": 1.0},
    "scoring_mode": "rank"
  },
  "rank": {"sort_by": "score", "order": "desc", "top_n": 1},
  "portfolio": {
    "method": "winner_take_all",
    "default_exposure": 1.0
  }
}
```

**变体**："两者均下跌时空仓" → 加 filter 过滤掉负收益资产：
```json
"filters": {
  "logic": "AND",
  "rules": [{"factor": "return_20d", "op": "gte", "value": 0}]
}
```

## 模式二：多因子打分

**特征**：综合多个维度（动量+估值+波动率等）给每资产打分，选总分最高的。

**关键配置**：
- `scoring_mode: "absolute"` — 独立评分
- 多个因子，各有权重（正=加分，负=减分）
- 使用 transforms 统一量纲

**模板**：
```json
{
  "score": {
    "factors": {
      "return_20d": 0.4,
      "pe_percentile": -0.3,
      "volatility_20d": -0.3
    },
    "transforms": {
      "return_20d": "momentum_score",
      "pe_percentile": "invert_percentile",
      "volatility_20d": "invert_percentile"
    },
    "scoring_mode": "absolute"
  },
  "rank": {"top_n": 5},
  "portfolio": {"method": "score_weight", "default_exposure": 0.8}
}
```

**权重设计原则**：
- 正向因子（值越大越好）：正权重，如 `return_20d: 0.4`
- 反向因子（值越小越好）：负权重，如 `pe_percentile: -0.3`
- 但注意：如果用了 transform 把因子映射到"高分=好"，则统一使用正权重即可

## 模式三：趋势跟踪 + 空仓

**特征**：价格在均线之上则持有，之下则空仓。

**关键配置**：
- 使用 filter 剔除不符合趋势条件的资产
- 全被剔除 → 自动空仓

**模板**：
```json
{
  "score": {
    "factors": {"return_20d": 1.0},
    "scoring_mode": "rank"
  },
  "filters": {
    "logic": "AND",
    "rules": [
      {"factor": "close_price", "op": "gt", "value": 0},
      {"factor": "return_20d", "op": "gte", "value": 0}
    ]
  },
  "portfolio": {"method": "winner_take_all", "default_exposure": 1.0}
}
```

**注意**：需要用 `close_price > ma_60d` 判断均线位置，但 filter 只能检查因子原始值，不能算差值。解决方案是：在 conditions 里用 `close_price` 和 `ma_60d` 做 `between` 判断，或改用 `trend_score` 相关的择时信号。

## 模式四：择时 + 仓位控制

**特征**：根据市场环境（估值、趋势）调节总仓位。

**关键配置**：
- `timing` 配置市场评估因子
- `portfolio.timing_exposure` 映射 regime → 仓位

**模板**：
```json
{
  "timing": {
    "factors": {"pe_percentile": 1.0, "return_60d": 1.0},
    "transforms": {
      "pe_percentile": "invert_percentile",
      "return_60d": "momentum_score"
    },
    "thresholds": {"offensive": 60, "defensive": 30}
  },
  "score": { ... },
  "portfolio": {
    "method": "equal_weight",
    "timing_exposure": {
      "offensive": 0.90,
      "neutral": 0.50,
      "defensive": 0.10
    }
  }
}
```

## 模式五：仅输出信号（不分配仓位）

**特征**：只需要知道哪些资产值得关注，不需要具体仓位。

**关键配置**：
- **不设置** `portfolio`（设为 null 或不传）

**模板**：
```json
{
  "score": {
    "factors": {"return_20d": 0.5, "return_60d": 0.5},
    "transforms": {"return_20d": "momentum_score", "return_60d": "momentum_score"},
    "scoring_mode": "absolute"
  },
  "rank": {"top_n": 10}
}
```

输出结果中 `positions` 为空，`rankings` 正常填充。

## 反模式与常见错误

1. **filter 中检查变换后的得分**：filter 检查的是因子原始值，不是变换后的得分。`"factor": "score"` 不会工作。
2. **用负权重 + rank 模式**：rank 模式先算加权原始值再做排名，负权重会导致低值排名高，通常不符合直觉。建议统一用正权重 + 合适的 transform。
3. **winner_take_all 但 top_n > 1**：浪费了排名逻辑，winner_take_all 只用第一名。
4. **遗漏 transforms 导致量纲不统一**：动量和估值原始值量纲差距大（% vs 0-100），不统一会导致某一因子主导评分。
