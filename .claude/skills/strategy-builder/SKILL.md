---
name: strategy-builder
description: >
  When a user describes an A-share index allocation, timing, or rotation strategy and wants it configured,
  use this skill. Trigger on phrases like "配置一个策略", "帮我建一个...策略", "create a strategy for...",
  "我想实现...轮动", or any natural-language description of an allocation/timing/rotation strategy.
  Also use when the user asks "能不能配置..." or "系统支持...吗" about a strategy idea —
  the skill will analyze feasibility and identify gaps. Do NOT trigger on purely theoretical
  discussions about investment concepts (e.g., "what is momentum investing").
---

# 策略构建器

> 本技能服务于 A 股指数研究系统，不配置 ETF、个股、做空或交易执行。执行前先读仓库根目录 `AGENTS.md`；字段、因子模板与命令
> 参数以 `engine/config.py`、`factors/catalog.py` 与 CLI `--help` 为准，`references/` 与代码冲突时以代码为准。

根据用户的自然语言描述，生成 A 股指数策略配置。若现有能力不支持，明确说明缺口和替代方案。

## 工作流程

### 第一步：理解策略需求

从用户描述中提取：

1. **策略标的**：哪些可配置 A 股指数？（如沪深300 = 000300，中证500 = 000905）
2. **选股 / 评分逻辑**：用什么指标判断好坏？绝对评分还是相对比较？
3. **仓位规则**：怎么分配资金？集中还是分散？有没有空仓条件？是否满仓？
4. **择时规则**：要不要按市场环境调节总仓位？分几档？
5. **调仓频率**：每天、每周、双周还是每月？选股与仓位调整是否同频？
6. **风控约束**：单资产上限？组合波动目标？回撤止损或单资产移动止损？

关键信息缺失时**主动询问用户**，不要猜。特别是：
- 标的指数代码不确定时（用户可能说"大盘""小盘"而非具体代码）；
- 因子选择有多种可能时（"趋势"可能是均线、收益率或通道突破）。

### 第二步：匹配系统能力

对照参考文件把需求映射到引擎配置：

- **因子**：`references/factors.md`，选模板并在 `factor_aliases` 声明参数（不存在 `return_20d` 这类带周期后缀的 ID）
- **变换函数**：`references/transforms.md`
- **配置模式**：`references/patterns.md`，选最接近的模板
- **配置模型**：`references/config_model.md`，确认每个字段的合法值与缺省
- **能力边界**：`references/limits.md`
- **复杂度纪律**：`references/anti_overfit.md`

### 第三步：生成配置

**创建之前先把完整 JSON 配置展示给用户确认**，并用 `python -m quant_etf_api.cli strategy validate --file <配置.json>`
（或 `POST /api/strategies/validate`）校验通过。展示内容包括：

1. 请求体（`strategy_id`、`display_name`、`description`、`config_json`）；
2. 配置的逻辑说明（管线每一步会发生什么，包括总仓位从哪里来）。

- `strategy_id`：英文小写 + 下划线，体现核心逻辑，如 `er_ba_lun_dong`、`momentum_rotation`；
- `display_name`：中文，简洁明了，如"二八轮动""多因子打分"。

### 第四步：处理能力缺口

系统不支持的需求**不要强行凑合**，明确指出：哪个需求无法实现、为什么（引用 `references/limits.md`）、可用的变通方案、
变通不理想时的改进建议。常见缺口：

- 跨资产比较的过滤（"只保留得分高于均值的资产"）→ 过滤只看单个资产，用 `scoring_mode: rank` + `top_n`；
- 做空弱势资产、加杠杆 → 只做多、总仓位 ≤ 1；
- 分钟级数据 → 只有日频；
- 用 ETF、个股或申万行业作为持仓标的 → 只能持有 `benchmark_index` 中的指数。

### 第五步：创建策略

用户确认后创建：

```bash
python -m quant_etf_api.cli strategy create --id <strategy_id> --name "<display_name>" --file <config.json> \
  [--description "..."] [--draft] [--lineage <原策略ID>]
```

或 `POST /api/strategies`（请求体同第三步）。基于已有策略派生的新策略**必须** `--lineage <原策略ID>` 归入原研究线，
不能用另建策略的方式重置试验次数 N。

创建后引导用户查看：`GET /api/strategies/{id}`（配置）、`GET /api/strategies/{id}/allocation`（最新决策，仅交易日有效）。

### 第六步：完成研究期验证（创建不等于研究完成）

创建后必须补齐以下动作，否则不能宣称"策略已经就绪"：

1. **研究期基线回测**：`backtest run --strategy <id> --start 2016-01-01 --end 2025-12-31`，确认能跑通、不是长期空仓，记录毛 / 净两套
   指标；用 `backtest verify <回测ID>` 做一次独立对账。
2. **先登记假设**：做扫描或消融前用 `research hypothesis add --statement ... --category ... --criteria ... --strategy <id>` 写下判定标准。
3. **参数邻域筛查**：`robustness scan --strategy <id> --preset quick --sync --workers 0`，确认处于参数平台而非尖峰；出现方向反转时
   调回粗粒度档位再验证。不带 `--sync` 时批次只入队、需要后台 worker。
4. **因子重要性**：因子数 > 1 时 `robustness ablate --strategy <id> --sync --workers 0` 检查边际贡献，删掉几乎没有影响的因子。
5. **向用户汇报复杂度**：因子族数量、原始指标数量、可调参数数量、阈值数量，以及哪些参数是粗粒度档位、哪些是精确值。

研究期、验证期与执行口径规则见 `AGENTS.md`：验证期数据只能用于否决，研究类回测越过研究期末端会被直接拒绝。上线后由生命周期
模块持续监控，方法见 `docs/architecture/策略生命周期管理方法.md`。

## 重要提醒

- **过滤检查的是因子原值，不是变换后的得分**。例如过滤 `mom20 >= 0` 比较的是原始收益率 %。
- **全部被过滤 = 空仓**，常用于实现"条件不满足时空仓"。
- **无择时策略缺省半仓**：`portfolio.default_exposure` 缺省 0.5，想满仓显式写 1.0；`winner_take_all` 满仓单一资产时把
  `risk.max_asset_weight` 放到 1.0。
- **轮动用 rank 评分**：涉及资产间比较的策略用 `scoring_mode: "rank"`。
- **估值因子不一定有数据**：行业 / 主题指数的 `pe_percentile` 可能为空，明确缺失语义。
- **引用名必须是别名或模板 ID**，不要编造因子 ID；派生因子（`expr`）进入研究批量或优化前必须先做诊断。
- **配置不含 strategy_id 和 display_name**：它们在请求顶层，`config_json` 内只有引擎配置。
- **复杂度纪律**：因子族 ≤ 5、原始指标 ≤ 10、权重默认等权、阈值优先用分位数、参数使用粗粒度档位（详见
  [references/anti_overfit.md](references/anti_overfit.md)）。配置里出现 0.173 这类精确权重、或为了贴合历史而挑选的调仓星期，都属于危险信号。
- **资产池纪律**：逐资产说明入选理由与可交易性；固定切分研究期 / 验证期防不住"事后挑选资产池"的选择性偏差，池子需要在研究结论里单独说明。

## 示例：二八轮动

用户说："配置一个二八轮动策略，比较沪深300和中证500的20日涨幅，谁强买谁，都跌就空仓"

分析过程：
1. 标的：沪深300（000300）+ 中证500（000905）
2. 评分：20 日涨幅 → `return` 模板、`period: 20`，声明别名 `mom20`；跨资产比较 → `scoring_mode: "rank"`
3. 选最强：`top_n: 1`
4. 集中满仓：`method: "winner_take_all"`、`default_exposure: 1.0`，单资产上限放到 1.0
5. 都跌空仓：过滤 `mom20 >= 0`，全部被过滤 → 空仓

生成的 config_json：
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

管线行为：评分（横截面排名）→ 过滤（剔除负收益）→ 排名（取第一）→ 组合（满仓第一名）
