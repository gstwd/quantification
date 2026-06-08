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
    ├── [可选] RebalanceModule → 调仓日检查（非调仓日沿用上次持仓）
    │
    ├── [可选] PortfolioModule → 目标仓位权重
    │
    ├── [可选] RiskModule      → 风控裁剪
    │
    ├── [可选] BenchmarkModule → 基准对比收益计算
    │
    ├── [可选] CostModule      → 交易成本扣除（佣金+滑点）
    │
    └── EngineResult           → 统一输出
```

**两种运行模式：**

- **信号模式**：无 `portfolio` 配置，只输出得分和排名
- **配置模式**：有 `portfolio` 配置，输出目标仓位权重

------

## 0. FactorProvider（因子供应器）

**职责**：桥接因子计算层与策略引擎层，消除引擎层对具体因子计算的硬编码依赖。

**两种工作模式**：

| 模式 | 方法 | 说明 |
|------|------|------|
| 实时模式 | `load_asset_factors()` / `load_market_factors()` | 从 `index_factor_value` 表加载预计算因子值 |
| 回测模式 | `precompute_backtest_factors()` | 利用预加载的 K 线数据，通过 `FactorComputer` 批量计算全区间因子值 |

**因子 ID 自动推导**：`collect_required_factor_ids(config)` 遍历 `timing.factors`、`score.factors`、`filters.rules`，自动收集所有需要的因子 ID 列表，去重后返回。

**依赖**：
- 实时模式：注入 `db: Session` 即可
- 回测模式：需额外注入 `registry: FactorRegistry`，以调用 `FactorComputer.compute()`

------

## 0.1 ContextBuilder（上下文构建器）

**职责**：构建引擎执行所需的 `EngineContext`，统一实时和回测两种模式。

位于 `engine/context_builder.py`（`services/context_builder.py` 为向后兼容 shim）。

**build() 方法统一入口**：

| 参数 | 实时模式 | 回测模式 |
|------|----------|----------|
| `index_codes` | None（从 DB 查询全量） | 传入标的列表 |
| `all_bars` | None（从 DB 查询） | 预加载的行情数据 |
| `all_valuation` | None（从 DB 查询） | 预加载的估值数据 |
| `precomputed_factors` | None（FactorProvider 从 DB 加载） | 预计算的因子值字典 |

**实时模式流程**：
1. 从 `benchmark_index` 表获取全量指数
2. 加载 90 天回望 K 线和估值数据
3. 通过 `FactorProvider.load_asset_factors()` 加载预计算因子值
4. 补充原始行情数据（change_pct、close_price）和估值因子（pe_percentile、pb_percentile）
5. 加载市场级择时因子（以沪深300 为代理）

**回测模式流程**：
1. 使用传入的 `index_codes` 构建 universe
2. 从 `precomputed_factors` 获取当日因子值
3. 补充原始行情和估值数据

------

## 1. Score Module（评分模块）

**职责**：根据多个因子计算综合得分。

**评分公式**：

```
score = Σ(transform(factor_value) × weight) / Σ(|weight|)
```

仅对有值因子归一化权重，支持 `missing_factor_strategy` 控制缺失行为。

**内置变换函数**（在 `engine/score.py` 的 `_TRANSFORM_REGISTRY` 中注册）：

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

**职责**：控制调仓时间，回测中非调仓日沿用上次持仓。

**支持频率**：

| 频率 | 说明 |
|---|---|
| `daily` | 每日调仓 |
| `weekly` | 每周指定日调仓（day_of_week，0=周一） |
| `monthly` | 每月指定日调仓（day_of_month） |

**配置示例**：

```json
{
  "rebalance": {
    "frequency": "weekly",
    "day_of_week": 4
  }
}
```

无 `rebalance` 配置时默认为每日调仓。

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

## ETF 资产配置策略（含回测增强配置）

```json
{
  "strategy_id": "etf_allocation",
  "display_name": "ETF 资产配置",
  "version": "1.0.0",
  "description": "择时 → 轮动 → 仓位分配",
  "frequency": "daily",
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
  "rebalance": {"frequency": "weekly", "day_of_week": 4},
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
    extra: dict[str, Any]                         # 扩展字段（原始 K 线/估值数据等）
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

# 回测系统

## 统一回测主循环

`BacktestService._run_backtest_loop()` 替代旧的 signal/allocation 双分支：

1. 准备数据（标的、交易日、行情、估值）
2. 通过 `FactorProvider` 预计算全区间因子值
3. 逐日通过 `ContextBuilder.build()` 构建上下文
4. 执行 `StrategyEngine.run()` 管线
5. 按调仓频率决定是否更新持仓
6. 按仓位/排名计算组合收益
7. 计算基准收益（如启用）
8. 扣除交易成本（如配置）
9. 写入 `backtest_daily_result` 和 `backtest_index_result`
10. 汇总绩效指标（年化收益、夏普/索提诺/卡玛比率、Alpha/Beta、信息比率等）

## 绩效指标

由 `services/metrics.py` 提供，专业指标包括：

| 指标 | 说明 |
|------|------|
| `cumulative_return_pct` | 累计收益率 |
| `annualized_return_pct` | 年化收益率 |
| `max_drawdown_pct` | 最大回撤 |
| `max_drawdown_days` | 最大回撤持续天数 |
| `sharpe_ratio` | 年化夏普比率 |
| `sortino_ratio` | 年化索提诺比率（下行风险调整） |
| `calmar_ratio` | 年化卡玛比率（收益/最大回撤） |
| `win_rate_pct` | 胜率（正收益日占比） |
| `profit_loss_ratio` | 盈亏比 |
| `alpha` | vs 基准的年化 Alpha |
| `beta` | vs 基准的 Beta 系数 |
| `information_ratio` | 信息比率 |
| `benchmark_return_pct` | 基准累计收益率 |
| `excess_return_pct` | 超额收益率 |

## 信号等级判定

统一使用 `domain/common/constants.py` 中的常量：

- `SIGNAL_THRESHOLD_HIGH = 70`：得分 ≥70 → 推荐配置
- `SIGNAL_THRESHOLD_MID = 50`：得分 50-70 → 可选配置
- 得分 <50 → 暂不配置

引擎和回测服务统一引用这些常量，避免硬编码散落。

------

# 文件结构

```
apps/api/src/quant_etf_api/
├── engine/                          # 策略引擎（12 个文件）
│   ├── __init__.py                  # 包初始化，导出核心类
│   ├── base.py                      # EngineContext, EngineResult
│   ├── config.py                    # StrategyConfig 及子配置 Pydantic 模型
│   ├── score.py                     # ScoreCalculator Protocol + DefaultScoreCalculator + _TRANSFORM_REGISTRY
│   ├── filter.py                    # FilterEngine Protocol + DefaultFilterEngine
│   ├── rank.py                      # RankEngine Protocol + DefaultRankEngine
│   ├── portfolio.py                 # WeightAllocator Protocol + EqualWeight/ScoreWeight
│   ├── risk.py                      # RiskManager Protocol + DefaultRiskManager
│   ├── rebalance.py                 # RebalanceScheduler Protocol + DefaultRebalanceScheduler
│   ├── factor_provider.py           # FactorProvider：因子层→引擎层桥接，支持实时/回测双模式
│   ├── context_builder.py           # ContextBuilder：统一上下文构建器（实时+回测）
│   └── orchestrator.py              # StrategyEngine 编排器
├── services/
│   ├── context_builder.py           # 向后兼容 shim，re-export 自 engine/context_builder.py
│   ├── strategy_config_service.py   # 配置 CRUD 服务
│   ├── strategy_service.py          # 策略服务（使用引擎）
│   ├── strategy_execution_service.py # 策略执行服务
│   ├── backtest_service.py          # 回测服务（统一主循环）
│   ├── ingest_service.py            # 数据摄取服务
│   ├── run_service.py               # 运行管理服务
│   ├── signal_service.py            # 信号服务
│   ├── factor_service.py            # 因子计算编排（re-export 自 factors/service.py）
│   ├── index_service.py             # 指数管理服务
│   ├── universe_service.py          # ETF 宇宙管理服务
│   ├── system_service.py            # 系统状态服务
│   ├── metrics.py                   # 专业绩效指标计算
│   └── benchmark.py                 # 基准收益计算（买入持有 + 等权组合）
├── factors/
│   ├── base.py                      # FactorSpec / FactorContext / FactorValue / FactorComputer Protocol
│   ├── registry.py                  # FactorRegistry + build_default_factor_registry()
│   ├── service.py                   # FactorService 编排计算 + 持久化
│   ├── evaluation.py                # IC/IR 分析 + 因子相关性矩阵
│   └── builtins/                    # 7 个内置因子计算器
│       ├── volume.py                # VolumeRatio20dComputer
│       ├── momentum.py              # Return5d/20d/60dComputer
│       ├── volatility.py            # Volatility20dComputer
│       └── valuation.py             # PEPercentileComputer / PBPercentileComputer
├── infra/db/
│   ├── models/core.py               # 22 张表的 SQLAlchemy ORM 模型
│   └── repositories/                # 11 个 Repository 类
├── api/
│   ├── routers/                     # 10 个路由组
│   ├── deps.py                      # FastAPI 依赖注入
│   └── middleware.py                # RequestIdMiddleware
├── domain/
│   ├── common/                      # constants.py, enums.py, values.py, bar_metrics.py
│   ├── strategies/models.py         # 策略领域模型
│   ├── etf/                         # ETF 领域（预留）
│   ├── market_data/                 # 行情领域（预留）
│   └── research/                    # 研究领域（预留）
├── schemas/                         # 11 个 Pydantic schema 文件
└── config/                          # Pydantic settings (.env)
```

------

# 数据库

## 表结构（22 张表，12 次迁移）

| 分组 | 表名 | 说明 |
|------|------|------|
| Reference | `etf_universe` | ETF 基础信息 |
| Reference | `benchmark_index` | 基准指数 |
| Market Data | `etf_daily_bar` | ETF 日线行情 |
| Market Data | `index_daily_bar` | 指数日线行情 |
| Market Data | `etf_daily_share` | ETF 每日份额 |
| Market Data | `index_valuation` | 指数估值（PE/PB 百分位） |
| Market Data | `macro_indicator` | 宏观经济指标 |
| Market Data | `source_payload_log` | 数据源原始响应日志 |
| Analytics | `factor_definition` | 因子定义元数据 |
| Analytics | `etf_factor_value` | ETF 因子计算值 |
| Analytics | `index_factor_value` | 指数因子计算值 |
| Analytics | `signal_definition` | 信号定义 |
| Analytics | `etf_signal` | ETF 策略信号 |
| Analytics | `index_signal` | 指数策略信号 |
| Runtime | `strategy_plugin` | 旧策略插件（已弃用，始终为空） |
| Runtime | `research_run` | 研究运行记录 |
| Runtime | `research_run_item` | 运行明细 |
| Backtest | `backtest_run` | 回测任务 |
| Backtest | `backtest_daily_result` | 回测每日组合绩效 |
| Backtest | `backtest_etf_result` | 回测 ETF 级别结果 |
| Backtest | `backtest_index_result` | 回测指数级别结果 |
| Strategy | `strategy_config` | 策略配置（JSON） |

## strategy_config 表

```sql
CREATE TABLE strategy_config (
    strategy_id   VARCHAR(64) PRIMARY KEY,
    display_name  VARCHAR(128) NOT NULL,
    version       VARCHAR(32) NOT NULL,
    description   TEXT,
    frequency     VARCHAR(32) NOT NULL DEFAULT 'daily',
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

## 迁移历史

| 迁移 | 内容 |
|------|------|
| 0001 | 基础表结构（ETF、行情、信号、运行） |
| 0002 | 回测表（backtest_run/daily/etf/index_result） |
| 0003 | 指数日线和宏观表 |
| 0004 | ETF 宇宙种子数据 |
| 0005 | 因子层表（factor_definition, factor_value） |
| 0006 | 因子定义增强 |
| 0007 | 指数因子回测支持 |
| 0008 | 策略配置表 |
| 0009 | 回测模式字段 |
| 0010 | index_signal 表 |
| 0011 | 回测日基准收益和换手率字段 |
| 0012 | 回测指数原始得分字段 |

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

## 添加新内置因子

1. 在 `factors/builtins/` 中创建因子计算器，实现 `FactorComputer` Protocol
2. 在 `factors/registry.py` 的 `build_default_factor_registry()` 中注册
3. 运行 `python -m quant_etf_api.cli init-factors` 同步到数据库

## 添加新权重分配方法

实现 `WeightAllocator` Protocol，在 `engine/portfolio.py` 的 `build_allocator` 中注册。

## 添加新过滤操作符

在 `engine/filter.py` 的 `_OPERATORS` 中注册。

------

# 向后兼容

- `EngineResult.strategy_results` 提供兼容旧接口的 `StrategyResult` 列表
- `EtfSignalModel` 和 `EtfFactorValueModel` 持久化逻辑不变
- `services/context_builder.py` 是向后兼容 shim，re-export 自 `engine/context_builder.py`
- 现有 API 端点结构不变
- `strategy_plugin` 表保留但始终为空，不允许新增 FK 引用
