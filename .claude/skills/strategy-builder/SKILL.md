---
name: strategy-builder
description: >
  When a user describes a trading/investment strategy for the quant ETF system and wants it configured,
  use this skill. Trigger on phrases like "配置一个策略", "帮我建一个...策略", "create a strategy for...",
  "我想实现...轮动", or any natural-language description of an allocation/timing/rotation strategy.
  Also use when the user asks "能不能配置..." or "系统支持...吗" about a strategy idea —
  the skill will analyze feasibility and identify gaps. Do NOT trigger on purely theoretical
  discussions about investment concepts (e.g., "what is momentum investing").
---

# 策略构建器

根据用户的自然语言描述，自动在量化 ETF 系统中创建策略配置。如果系统的现有能力不支持该策略，则明确指出缺口和改进方向。

## 工作流程

### 第一步：理解策略需求

从用户描述中提取以下关键信息：

1. **交易标的**：哪些指数/ETF？（如沪深300=000300，中证500=000905）
2. **选股/评分逻辑**：用什么指标判断好坏？是绝对评分还是相对比较？
3. **仓位规则**：怎么分配资金？集中还是分散？有没有空仓条件？
4. **择时规则**：需要根据市场环境调节仓位吗？
5. **调仓频率**：每天、每周、还是每月？
6. **风控约束**：单资产上限？最大回撤？

如果关键信息缺失，**主动询问用户**，不要猜测。特别是：
- 标的指数代码不确定时（用户可能说"大盘"、"小盘"而非具体代码）
- 因子选择有多种可能时（如"趋势"可能是均线、可能是收益率、可能是通道突破）

### 第二步：匹配系统能力

对照参考文件，将策略需求映射到引擎配置：

- **因子选择**：参考 `references/factors.md`，选择匹配的因子 ID
- **变换函数**：参考 `references/transforms.md`，选择合适的数值映射
- **配置模式**：参考 `references/patterns.md`，选择最接近的策略模板
- **配置模型**：参考 `references/config_model.md`，确认每个字段的合法值
- **能力边界**：参考 `references/limits.md`，检查是否有无法实现的需求

### 第三步：生成配置

**在创建之前，先生成完整的 JSON 配置展示给用户确认**。配置必须包含：

1. API 请求体（包含 `strategy_id`、`display_name`、`config_json` 等）
2. 配置的逻辑说明（管线每一步会发生什么）

#### strategy_id 命名规范

- 使用英文小写 + 下划线，如 `er_ba_lun_dong`、`momentum_rotation`
- 体现策略核心逻辑

#### display_name 命名规范

- 使用中文，简洁明了，如 "二八轮动"、"多因子打分"

### 第四步：处理能力缺口

如果策略的某些需求系统无法支持，**不要强行凑合**。明确指出：

1. **哪个需求无法实现**
2. **为什么**（引用 `references/limits.md` 中的具体限制）
3. **可用的变通方案**（如果有）
4. **系统改进建议**（如果变通方案不理想）

常见的缺口示例：
- 需要跨资产比较过滤（如"只保留得分高于均值的资产"）→ filter 不支持
- 需要做空弱势资产 → 系统仅支持做多
- 需要风险平价权重 → 仅支持 3 种权重方法
- 需要分钟级数据 → 仅支持日频

### 第五步：创建策略

用户确认后，通过 API 创建策略：

```
POST /api/strategies
Content-Type: application/json

{
  "strategy_id": "...",
  "display_name": "...",
  "description": "...",
  "config_json": { ... }
}
```

创建成功后，引导用户验证：
- `GET /api/strategies/{id}` 查看配置
- `GET /api/strategies/{id}/allocation` 查看最新决策（仅交易日有效）
- `POST /api/backtests` 创建回测验证历史表现

## 重要提醒

- **filter 检查的是因子原始值，不是变换后的得分**。例如 filter `return_20d >= 0` 检查的是原始收益率 %。
- **全被过滤 = 空仓**。这是一个常用的技巧，用于实现"条件不满足时空仓"。
- **rank 评分模式是轮动策略的核心**。任何涉及资产间比较的策略都应使用 `scoring_mode: "rank"`。
- **估值因子不一定有数据**。行业/主题指数的 pe_percentile 可能为 None。
- **因子 ID 必须精确**。使用参考文件中列出的确切 ID，不要自行编造。
- **策略配置不包含 strategy_id 和 display_name**。这些在 API 请求顶层，config_json 内只有引擎配置。

## 示例：二八轮动

用户说："配置一个二八轮动策略，比较沪深300和中证500的20日涨幅，谁强买谁，都跌就空仓"

分析过程：
1. 标的：沪深300(000300) + 中证500(000905)
2. 评分：20日涨幅 → 因子 `return_20d`，需要跨资产比较 → `scoring_mode: "rank"`
3. 选最强：`top_n: 1`
4. 集中持仓：`method: "winner_take_all"`，满仓：`default_exposure: 1.0`
5. 都跌空仓：用 filter `return_20d >= 0`，全被过滤 → 自动空仓

生成的 config_json：
```json
{
  "index_codes": ["000300", "000905"],
  "score": {
    "factors": {"return_20d": 1.0},
    "scoring_mode": "rank"
  },
  "filters": {
    "logic": "AND",
    "rules": [{"factor": "return_20d", "op": "gte", "value": 0}]
  },
  "rank": {"sort_by": "score", "order": "desc", "top_n": 1},
  "portfolio": {"method": "winner_take_all", "default_exposure": 1.0}
}
```

管线行为：Score(rank 排名) → Filter(剔除负收益) → Rank(取第一) → Portfolio(满仓第一)
