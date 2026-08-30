---
name: strategy-optimizer
description: >
  对量化系统中的策略执行 AI 自动优化闭环：基线回测 → 单假设候选改动 → 滚动样本外验证 →
  优化报告 → accept/reject 并 promote。当用户要求"优化/改进某个策略"、"跑一轮策略优化"、
  "根据回测结果调整策略"时使用。不用于：仅配置新策略（用 strategy-builder）、纯策略理论讨论、
  手工逐个跑回测分析。
---

# 策略优化器

使用 `quant_etf_api.cli` 的 strategy/backtest/optimization 命令，对策略执行"基线 → 候选 → 滚动样本外验证 → 报告 → 收尾"的自动优化闭环。系统不内置参数搜索：每一轮由你提出一个有明确假设的改动，用回测与滚动验证检验，再决定 accept/reject 或进入下一轮。

## 工作流程

1. **读基线**：`strategy show <id>`，拿到完整 config_json 与元数据。
2. **跑基线回测**（未跑过时）：`backtest run --strategy <id> --start ... --end ...`，记录年化/夏普/回撤/超额等指标作为对照。总跨度超过 5 年时按「滚动分段回测」逐段执行（每段 1-2 年），不要一次跑完整区间。
3. **设计候选**：只改一处、可解释的模块（打分权重、过滤阈值、top_n、调仓频率、择时等），其余保持不变。因子 ID 与配置 schema 参考 `.agents/skills/strategy-builder/references/`（factors.md / config_model.md / transforms.md / limits.md）。
   - 若当前轮次想引入新因子而非微调参数，优先考虑：`sharpe_60d`（风险调整动量，替代/补充 return_60d）、
     `ma60d_deviation`（配合 `trend_score` 变换做趋势强度）、`amount_ratio_20d`（成交额确认）、
     `drawdown_current`（长窗口回撤 + 水下时间，配 `drawdown_score`）、`pmi_momentum_3m` 与
     `breadth_ma20_pct`（市场级择时/过滤因子，放 timing 或 filters，勿放横截面评分）。
4. **写候选文件并校验**：完整 config_json 存入 `candidates/` 下的 JSON 文件，`strategy validate --file <path>` 通过后再用。
5. **开会话**：`optimization start --strategy <基线> --candidate-file <path> --hypothesis "<假设>" [--start --end] --folds 4 --version <新版本>`。`--version` 必传：promote 时基线版本取该值，不传会沿用旧版本号导致版本不递增。
6. **评估**：`optimization evaluate <opt_id> [--folds 4]`。默认同步执行 2+2K 个回测（K=4 约 40-50 分钟）；`--async` 需要 API 服务端在跑，之后用 `optimization show` 轮询。长跨度（> 5 年）按「滚动分段回测」处理：分会话逐段评估，或把 `--folds` 调到每折 ≈ 1-2 年。
7. **出报告**：`optimization report <opt_id> --file <path>` 生成骨架，补写"分析结论"：假设是否成立、数据支持、风险、下一步方向。
8. **收尾**：对照验收清单——
   - 通过 → `optimization finish <opt_id> --verdict accept --report-file <path> --promote --strict`
   - 未通过 → `--verdict reject`（不 promote），会话保留作审计。
9. **确认落地**：`strategy show <基线>` 验证 promote 生效，必要时用 `strategy update --version` 补版本号。

## 滚动分段回测（总跨度 > 5 年强制）

优化涉及的总区间超过 5 年时，禁止用单个回测/评估一次性跑完全部年份；必须滚动分段执行，每段约 1-2 年，再逐段对比：

1. **切段**：按 1-2 年切段（优先自然年边界），列出每段 `--start/--end`，如 2021-01-01~2022-12-31、2023-01-01~2024-12-31。
2. **逐段回测**：基线与候选都逐段执行 `backtest run --start <段起> --end <段止>`，每段单独记录年化收益、夏普、最大回撤、超额收益、换手等指标。
3. **逐段对比**：结论以「每段候选 vs 基线的胜出/劣化」为准（如胜出段数占比、是否存在致命劣化段、优劣是否集中于某段行情），禁止只看全区间合计。
4. **优化会话**：长跨度建议按段分别 `optimization start --start/--end` 建会话并 evaluate，保证单次执行跨度仍在 1-2 年；若必须单会话覆盖长跨度，`--folds` 按每折 ≈ 1-2 年设定（K ≈ 总年数 ÷ 1.5，至少 3），验收只以逐折验证窗聚合为准，忽略全区间单次指标。

## 关键约束

- **防过拟合**：每轮只测一个假设；以滚动样本外（fold）聚合为准，不能只看全区间；`--strict` 强制验收清单。
- **单次回测跨度 ≤ 约 2 年**：总跨度超过 5 年时禁止一次性跑全区间回测，必须按「滚动分段回测」逐段执行并逐段对比；分段后每段指标单独看，不以全区间合计掩盖劣化段。
- **验收清单默认阈值**（`--strict` 强制 accept）：验证窗平均夏普 ≥ 基线；平均最大回撤劣化 ≤ 2pct；夏普胜出折数 ≥ 50%；验证窗平均累计收益 ≥ 基线。
- **改动要小且可解释**；候选明显更差时先 reject 换假设，不要在同一轮叠加多个改动。
- 命令默认 JSON 输出；回测同步执行不依赖服务端进程。
- 回测很慢：2 年 × 18 指数约 8 分钟/个；评估 K=4 共 10 个，同步执行时耐心等待，勿高频轮询。跨度越长单次耗时越长、失败回退代价越大，长跨度务必按 1-2 年分段。
- 数据库在远程服务器；沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。终端中文乱码只是控制台代码页问题，落库数据是 UTF-8。

## 参考

- [references/cli.md](references/cli.md)：全部命令、参数、评估语义与输出形态。
