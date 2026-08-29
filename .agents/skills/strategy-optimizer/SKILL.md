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
2. **跑基线回测**（未跑过时）：`backtest run --strategy <id> --start ... --end ...`，记录年化/夏普/回撤/超额等指标作为对照。
3. **设计候选**：只改一处、可解释的模块（打分权重、过滤阈值、top_n、调仓频率、择时等），其余保持不变。因子 ID 与配置 schema 参考 `.agents/skills/strategy-builder/references/`（factors.md / config_model.md / transforms.md / limits.md）。
4. **写候选文件并校验**：完整 config_json 存入 `candidates/` 下的 JSON 文件，`strategy validate --file <path>` 通过后再用。
5. **开会话**：`optimization start --strategy <基线> --candidate-file <path> --hypothesis "<假设>" [--start --end] --folds 4 --version <新版本>`。`--version` 必传：promote 时基线版本取该值，不传会沿用旧版本号导致版本不递增。
6. **评估**：`optimization evaluate <opt_id> [--folds 4]`。默认同步执行 2+2K 个回测（K=4 约 40-50 分钟）；`--async` 需要 API 服务端在跑，之后用 `optimization show` 轮询。
7. **出报告**：`optimization report <opt_id> --file <path>` 生成骨架，补写"分析结论"：假设是否成立、数据支持、风险、下一步方向。
8. **收尾**：对照验收清单——
   - 通过 → `optimization finish <opt_id> --verdict accept --report-file <path> --promote --strict`
   - 未通过 → `--verdict reject`（不 promote），会话保留作审计。
9. **确认落地**：`strategy show <基线>` 验证 promote 生效，必要时用 `strategy update --version` 补版本号。

## 关键约束

- **防过拟合**：每轮只测一个假设；以滚动样本外（fold）聚合为准，不能只看全区间；`--strict` 强制验收清单。
- **验收清单默认阈值**（`--strict` 强制 accept）：验证窗平均夏普 ≥ 基线；平均最大回撤劣化 ≤ 2pct；夏普胜出折数 ≥ 50%；验证窗平均累计收益 ≥ 基线。
- **改动要小且可解释**；候选明显更差时先 reject 换假设，不要在同一轮叠加多个改动。
- 命令默认 JSON 输出；回测同步执行不依赖服务端进程。
- 回测很慢：2 年 × 18 指数约 8 分钟/个；评估 K=4 共 10 个，同步执行时耐心等待，勿高频轮询。
- 数据库在远程服务器；沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。终端中文乱码只是控制台代码页问题，落库数据是 UTF-8。

## 参考

- [references/cli.md](references/cli.md)：全部命令、参数、评估语义与输出形态。
