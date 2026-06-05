# ETF量化系统策略层架构设计

## 系统背景

系统已完成策略层重构，采用**组件化 + 配置驱动**架构。

核心目标：

- ETF轮动
- ETF资产配置
- 趋势跟踪
- 动量策略
- 风险控制

主要调仓周期：周度、月度。

------

# 核心设计原则

不采用每个策略一个类的设计（如 `DualMomentumStrategy`、`TrendStrategy`），避免大量重复代码。

采用：

- **策略组件化（Component-Based）**：策略由独立模块组成管线
- **策略配置化（Configuration Driven）**：策略参数通过 JSON 配置，无需编写代码

新增策略时只需创建 JSON 配置，无需开发新代码。

------

# 实现架构

## 管线流程

```
StrategyEngine.run(config, context)
    │
    ├── [可选] TimingModule    → TimingSignal (regime: offensive/neutral/defensive)
    │
    ├── ScoreModule            → 每资产综合得分 (0-100)
    │
    ├── [可选] FilterModule    → 过滤不合格资产
    │
    ├── RankModule             → 排序 + TopN/BottomN
    │
    ├── [可选] PortfolioModule → 目标仓位权重
    │
    ├── [可选] RiskModule      → 风控裁剪
    │
    └── EngineResult           → 统一输出
```

**两种运行模式：**

- **信号模式**：无 `portfolio` 配置，只输出得分和排名
- **配置模式**：有 `portfolio` 配置，输出目标仓位权重

------

## 1. Score Module（评分模块）

**职责**：根据多个因子计算综合得分。

**评分公式**：

```
score = Σ(transform(factor_value) × weight) / Σ(|weight|)
```

仅对有值因子归一化权重，支持 `missing_factor_strategy` 控制缺失行为。

**内置变换函数**：

| 函数名 | 说明 | 来源 |
|---|---|---|
| `invert_percentile` | 百分位反转（越低越便宜得分越高） | timing.py |
| `momentum_score` | 收益率映射为动量得分 0-100 | rotation.py |
| `volume_score` | 量比映射为量能得分 0-100 | timing.py |
| `trend_score` | MA偏离度映射为趋势得分 0-100 | timing.py |
| `clamp_0_100` | 通用裁剪限制在 0-100 | 新增 |

**配置示例**：

```json
{
  "score": {
    "factors": {
      "return_20d": 0.6,
      "pe_percentile": 0.4
    },
    "transforms": {
      "return_20d": "momentum_score",
      "pe_percentile": "invert_percentile"
    },
    "missing_factor_strategy": "ignore"
  }
}
```

**接口**：`ScoreCalculator` Protocol

------

## 2. Filter Module（过滤模块）

**职责**：过滤不满足条件的资产。

**支持操作符**：`gt` / `lt` / `gte` / `lte` / `eq` / `neq` / `between`

**支持逻辑**：`AND`（全部满足）/ `OR`（任一满足）

**配置示例**：

```json
{
  "filters": {
    "logic": "AND",
    "rules": [
      {"factor": "pe_percentile", "op": "lt", "value": 70},
      {"factor": "return_20d", "op": "gt", "value": 0}
    ]
  }
}
```

**接口**：`FilterEngine` Protocol

------

## 3. Rank Module（排名模块）

**职责**：按综合得分排序，支持 TopN/BottomN 截取。

**子维度排名**：

- `momentum_rank`：按 `return_20d` 降序
- `valuation_rank`：按估值吸引力（100 - pe_percentile）降序

**配置示例**：

```json
{
  "rank": {
    "sort_by": "score",
    "order": "desc",
    "top_n": 5
  }
}
```

**接口**：`RankEngine` Protocol

------

## 4. Portfolio Module（组合模块）

**职责**：根据排名和择时信号分配目标仓位权重。

**支持方法**：

| 方法 | 说明 |
|---|---|
| `equal_weight` | 等权分配 |
| `score_weight` | 按得分加权分配 |

**择时仓位控制**：

```json
{
  "portfolio": {
    "method": "score_weight",
    "timing_exposure": {
      "offensive": 0.80,
      "neutral": 0.50,
      "defensive": 0.20
    }
  }
}
```

无 `timing_exposure` 时使用默认值。

**接口**：`WeightAllocator` Protocol

------

## 5. Risk Module（风控模块）

**职责**：对仓位施加约束限制。

**约束链**：

1. `max_asset_weight`：单资产仓位上限
2. `max_portfolio_exposure`：组合总仓位上限
3. `min_cash_ratio`：最低现金比例

**配置示例**：

```json
{
  "risk": {
    "max_asset_weight": 0.30,
    "max_portfolio_exposure": 0.90,
    "min_cash_ratio": 0.10
  }
}
```

**接口**：`RiskManager` Protocol

------

## 6. Rebalance Module（调仓模块）

**职责**：控制调仓时间。

**支持频率**：

| 频率 | 说明 |
|---|---|
| `daily` | 每日调仓 |
| `weekly` | 每周指定日调仓 |
| `monthly` | 每月指定日调仓 |

**配置示例**：

```json
{
  "rebalance": {
    "frequency": "weekly",
    "day_of_week": 4
  }
}
```

**接口**：`RebalanceScheduler` Protocol

------

## 7. Timing Module（择时模块）

**职责**：评估市场环境，输出 regime 信号。

**Regime 判定**：

- `offensive`（进攻）：综合得分 ≥ offensive 阈值
- `defensive`（防守）：综合得分 ≤ defensive 阈值
- `neutral`（观望）：介于两者之间

**配置示例**：

```json
{
  "timing": {
    "factors": {
      "pe_percentile": 0.4,
      "pb_percentile": 0.4,
      "volume_ratio_20d": 0.2
    },
    "transforms": {
      "pe_percentile": "invert_percentile",
      "pb_percentile": "invert_percentile",
      "volume_ratio_20d": "volume_score"
    },
    "thresholds": {
      "offensive": 65,
      "defensive": 35
    }
  }
}
```

------

# 完整配置示例

## ETF 资产配置策略

```json
{
  "strategy_id": "etf_allocation",
  "display_name": "ETF 资产配置",
  "version": "1.0.0",
  "description": "择时 → 轮动 → 仓位分配",
  "frequency": "daily",
  "asset_scope": "a_share_etf",
  "timing": {
    "factors": {"pe_percentile": 0.4, "pb_percentile": 0.4, "volume_ratio_20d": 0.2},
    "transforms": {"pe_percentile": "invert_percentile", "pb_percentile": "invert_percentile", "volume_ratio_20d": "volume_score"},
    "thresholds": {"offensive": 65, "defensive": 35}
  },
  "score": {
    "factors": {"return_20d": 0.6, "pe_percentile": 0.4},
    "transforms": {"return_20d": "momentum_score", "pe_percentile": "invert_percentile"}
  },
  "rank": {"sort_by": "score", "order": "desc", "top_n": 5},
  "portfolio": {
    "method": "score_weight",
    "timing_exposure": {"offensive": 0.80, "neutral": 0.50, "defensive": 0.20}
  },
  "risk": {"max_asset_weight": 0.30}
}
```

## 量能突破策略（信号模式）

```json
{
  "strategy_id": "volume_breakout_daily",
  "display_name": "量能突破",
  "version": "1.0.0",
  "description": "基于量比的突破信号",
  "frequency": "daily",
  "asset_scope": "a_share_etf",
  "score": {
    "factors": {"volume_ratio_20d": 0.5, "return_5d": 0.3, "change_pct": 0.2},
    "transforms": {"volume_ratio_20d": "volume_score"}
  },
  "filters": {
    "logic": "AND",
    "rules": [
      {"factor": "volume_ratio_20d", "op": "gt", "value": 1.3}
    ]
  },
  "rank": {"sort_by": "score", "order": "desc", "top_n": 10}
}
```

无 `portfolio` 配置，为信号模式，只输出得分和排名。

------

# 数据结构

## EngineContext（引擎上下文）

```python
@dataclass
class EngineContext:
    trade_date: date                              # 交易日
    universe: list[dict[str, Any]]                # 资产宇宙
    asset_factors: dict[tuple[str, str], float | None]  # 每资产因子值
    market_factors: dict[str, float | None]       # 市场级因子（择时用）
    asset_metadata: dict[str, dict[str, Any]]     # 资产元数据
    extra: dict[str, Any]                         # 扩展字段
```

## EngineResult（引擎结果）

```python
@dataclass
class EngineResult:
    trade_date: date
    strategy_id: str
    timing: TimingSignal | None       # 择时信号
    scores: dict[str, float]          # 每资产得分
    rankings: list[AssetRanking]      # 排名列表
    positions: dict[str, float]       # 目标仓位（信号模式为空）
    total_exposure: float             # 总仓位比例
    cash_ratio: float                 # 现金比例
    strategy_results: list[StrategyResult]  # 兼容旧接口
```

------

# 文件结构

```
apps/api/src/quant_etf_api/
├── engine/                          # 策略引擎
│   ├── __init__.py                  # 包初始化，导出核心类
│   ├── base.py                      # EngineContext, EngineResult
│   ├── config.py                    # StrategyConfig 及子配置 Pydantic 模型
│   ├── score.py                     # ScoreCalculator Protocol + DefaultScoreCalculator
│   ├── filter.py                    # FilterEngine Protocol + DefaultFilterEngine
│   ├── rank.py                      # RankEngine Protocol + DefaultRankEngine
│   ├── portfolio.py                 # WeightAllocator Protocol + EqualWeight/ScoreWeight
│   ├── risk.py                      # RiskManager Protocol + DefaultRiskManager
│   ├── rebalance.py                 # RebalanceScheduler Protocol + DefaultRebalanceScheduler
│   └── orchestrator.py              # StrategyEngine 编排器
├── services/
│   ├── context_builder.py           # 统一上下文构建器
│   ├── strategy_config_service.py   # 配置 CRUD 服务
│   ├── strategy_service.py          # 策略服务（使用引擎）
│   ├── backtest_service.py          # 回测服务（统一模式）
│   └── strategy_execution_service.py # 策略执行服务
└── infra/db/
    ├── models/core.py               # StrategyConfigModel
    └── repositories/strategy_config.py # 配置 Repository
```

------

# 信号输出

最终输出统一结构 `EngineResult`：

- `timing`：择时信号（regime、confidence、label）
- `scores`：每资产综合得分
- `rankings`：资产排名列表
- `positions`：目标仓位权重（配置模式）
- `strategy_results`：兼容旧接口的信号列表

前端页面展示：

- 择时状态（进攻/观望/防守）
- 资产排名表
- 仓位分配图
- 信号详情

------

# 数据库

## strategy_config 表

```sql
CREATE TABLE strategy_config (
    strategy_id   VARCHAR(64) PRIMARY KEY,
    display_name  VARCHAR(128) NOT NULL,
    version       VARCHAR(32) NOT NULL,
    description   TEXT,
    frequency     VARCHAR(32) NOT NULL DEFAULT 'daily',
    asset_scope   VARCHAR(64) NOT NULL DEFAULT 'a_share_etf',
    config_json   JSONB NOT NULL,
    status        VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

策略配置通过 API 端点管理：

- `POST /strategies` — 创建策略
- `GET /strategies` — 列出所有策略
- `GET /strategies/{id}` — 获取策略详情
- `PUT /strategies/{id}` — 更新策略
- `DELETE /strategies/{id}` — 删除策略
- `POST /strategies/validate` — 校验配置

------

# 扩展指南

## 添加新变换函数

在 `engine/score.py` 的 `_TRANSFORM_REGISTRY` 中注册：

```python
def _my_transform(value: float) -> float:
    """自定义变换函数。"""
    ...

_TRANSFORM_REGISTRY["my_transform"] = _my_transform
```

## 添加新权重分配方法

实现 `WeightAllocator` Protocol，在 `engine/portfolio.py` 的 `build_allocator` 中注册。

## 添加新过滤操作符

在 `engine/filter.py` 的 `_OPERATORS` 中注册。

------

# 向后兼容

- `EngineResult.strategy_results` 提供兼容旧接口的 `StrategyResult` 列表
- `EtfSignalModel` 和 `EtfFactorValueModel` 持久化逻辑不变
- 现有 API 端点结构不变
