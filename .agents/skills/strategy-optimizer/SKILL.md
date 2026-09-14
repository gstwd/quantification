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
   - 若当前轮次想引入新因子而非微调参数，优先考虑：`sharpe_60d`（风险调整动量，替代/补充 return_60d）、
     `ma60d_deviation`（配合 `trend_score` 变换做趋势强度）、`amount_ratio_20d`（成交额确认）、
     `drawdown_current`（长窗口回撤 + 水下时间，配 `drawdown_score`）、`pmi_momentum_3m` 与
     `breadth_ma20_pct`（市场级择时/过滤因子，放 timing 或 filters，勿放横截面评分）。
   需要并行探索多个方向时，先列出互斥假设清单，每个方向一个候选文件（见「并行执行」）。
4. **写候选文件并校验**：完整 config_json 存入 `candidates/` 下的 JSON 文件，`strategy validate --file <path>` 通过后再用。
5. **开会话**：`optimization start --strategy <基线> --candidate-file <path> --hypothesis "<假设>" [--start --end] --folds 4 --version <新版本>`。`--version` 必传：promote 时基线版本取该值，不传会沿用旧版本号导致版本不递增。
6. **评估**：`optimization evaluate <opt_id> [--folds 4]`。多方向/多段并行时改用 `--async`（需要 API 服务端在跑，2+2K 个回测一次入队并行执行，见「并行执行」），之后用 `optimization show` 轮询。长跨度（> 5 年）按「滚动分段回测」处理：分会话逐段评估，或把 `--folds` 调到每折 ≈ 1-2 年。
7. **稳健性验证**（评估通过后、收尾之前必做）：
   - `robustness scan --strategy <基线>` 确认参数处于平台、无方向反转；
   - 因子数 > 1 时 `robustness ablate --strategy <基线>` 看边际贡献；
   - `robustness collect <id> --wait` 后 `robustness stats <id>` 取 PBO 与 Deflated Sharpe。
   详见 [references/robustness.md](references/robustness.md)。
8. **出报告**：`optimization report <opt_id> --file <path>` 生成骨架，补写"分析结论"：假设是否成立、数据支持（含净口径与稳健性数字）、风险、下一步方向。
9. **收尾**：对照验收清单（共 7 项）——
   - 通过 → `optimization finish <opt_id> --verdict accept --report-file <path> --promote --strict`
   - 未通过 → `--verdict reject`（不 promote），会话保留作审计。
10. **确认落地**：`strategy show <基线>` 验证 promote 生效，必要时用 `strategy update --version` 补版本号。

## 并行执行（多方向 / 多段加速）

并行只发生在服务端任务队列：回测/评估必须用 `--async` 入队，由 uvicorn 多 worker 进程通过 `FOR UPDATE SKIP LOCKED` 认领并行执行（互不重复）；CLI 同步模式在单个进程内串行，多跑几个也是排队，没有加速。实际并行度上限 = min(worker 进程数, CPU 核心数)，并行前先确认服务端以多 worker 启动（如 `--workers 6`）。

### 同时验证多个修改方向

- 防过拟合原则不变："每轮只测一个假设"约束的是同一候选内的改动；多个**独立**假设可以并行验证，每个方向一个候选文件 + 一个 optimization 会话，各自单独 evaluate 与 verdict。
- 推荐派子代理并行：每个方向一个子代理，负责 validate → `optimization start` → `evaluate --async` → 轮询 → 出报告与初步结论；根代理汇总所有方向后，再决定 accept/reject/promote 哪一个。
- `evaluate --async` 一次入队 2+2K 个回测（K=4 即 10 个）；同时 evaluate 的方向数建议 ≤ worker 进程数，按 worker 数分批入队，避免任务堆积与连接池打满。
- 每个候选会话都会重复跑一遍基线（全区间 + 每折）；纯分段对比（不经会话）时基线每段只跑一次，多个候选共享同一段基线回测结果。

### 分段区间并行回测

- 单会话多折同样适用：`optimization evaluate --async` 把 2+2K 个回测一次入队；worker 足够时分批并行执行（如 6 worker、10 个任务约分 2 批），避免所有任务串行排队。
- 失败回退局部化：某段失败只重跑该段（同 `--start/--end` 再 `backtest run`），不必整轮重来。
- 逐段对比规则不变：以每段候选 vs 基线胜出/劣化为准，禁止只看全区间合计。

## 关键约束

- **防过拟合**：每轮只测一个假设；以滚动样本外（fold）聚合为准，不能只看全区间；`--strict` 强制验收清单。
- **研究期 / 验证期边界（硬约束）**：研究期固定为 2016-01-01 ~ 2025-12-31，
  2026-01-01 起为验证期。优化属于研究行为，区间越过研究期末端会被**系统直接拒绝**，
  `optimization start` 的默认 `--end` 即研究期末端。**禁止读验证期结果来调参**：
  一旦看过，那段数据就不再是样本外。
- **回测区间下限（用户约定）**：所有回测、对比与优化评估的 `--start` 一律取 2016-01-01，不使用 2016 年之前的数据；滚动分段的第一段即 2016-01-01 起。
- **净口径成本**：回测汇总为毛收益口径，验收必须同时看净口径指标
  （默认 10bp 单边，`backtest run --cost-bps` 可覆盖）。
  净收益 = 毛收益 − 单边换手 × 成本；换手越高，成本对结论的影响越大。
- **验收清单默认阈值**（`--strict` 强制 accept，共 7 项）：
  1. 验证窗平均夏普 ≥ 基线；
  2. 平均最大回撤劣化 ≤ 2pct；
  3. 夏普胜出折数 ≥ 50%；
  4. 验证窗平均累计收益 ≥ 基线；
  5. **净成本口径**验证窗平均夏普 ≥ 基线；
  6. **参数邻域**无方向反转且处于平台（需先跑 `robustness scan`，未跑直接判不通过）；
  7. **分段一致性**：剔除最好折后候选夏普仍不低于基线。
- **试验次数台账**：`robustness stats --n-trials` 缺省取该策略历史所有稳健性批次的
  变体总数；手工做过的不入批次的对比要额外计入，多重检验的 N 只会被低估不会被高估。
- **改动要小且可解释**；候选明显更差时先 reject 换假设，不要在同一轮叠加多个改动。
- **并行前提与上限**：并行必须走 `--async` + 服务端多 worker，实际并行度 = min(worker 数, CPU 核心数)；每个 worker 进程持有独立 DB 连接池（pool_size=5 + max_overflow=10），6 worker 最坏约 90 连接，并行前先确认 PostgreSQL max_connections 够用。
- 命令默认 JSON 输出；回测同步执行不依赖服务端进程（但无并行收益）。
- 同步任务使用 `status --wait`，避免高频轮询；异步任务按 worker 数分批。
- 数据库在远程服务器；沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。终端中文乱码只是控制台代码页问题，落库数据是 UTF-8。

## 参考

- [references/cli.md](references/cli.md)：全部命令、参数、评估语义与输出形态。
