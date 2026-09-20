---
name: strategy-optimizer
description: >
  对量化系统中的策略执行 AI 自动优化闭环：基线回测 → 单假设候选改动 → 滚动样本外验证 →
  优化报告 → accept/reject 并 promote。当用户要求"优化/改进某个策略"、"跑一轮策略优化"、
  "根据回测结果调整策略"时使用。不用于：仅配置新策略（用 strategy-builder）、纯策略理论讨论、
  手工逐个跑回测分析。
---

# 策略优化器

> **当前契约优先**：先读取根目录 `AGENTS.md`，并以 CLI `--help` 为准。本技能中的历史并行参数、性能数字和固定因子 ID 不构成接口承诺；不得启动自动交易、使用验证期反向调参，或绕过策略生命周期的人工确认。

使用 `quant_etf_api.cli` 的 strategy/backtest/optimization 命令，对策略执行"基线 → 候选 → 滚动样本外验证 → 报告 → 收尾"的自动优化闭环。系统不内置参数搜索：每一轮由你提出一个有明确假设的改动，用回测与滚动验证检验，再决定 accept/reject 或进入下一轮。

## 硬约束：只跑 CLI + 数据库，且尽量把核用满

1. **不依赖后端服务**。整条闭环只需要两样东西：`python -m quant_etf_api.cli` 和数据库。
   **不要**依赖 uvicorn、独立 `queue worker`、或任何 HTTP 接口来推进任务：
   - 不要用 `--async`（它只把任务写进 `background_job`，执行要靠服务端 worker 消费；
     没有服务端时任务会永远停在 pending）；
   - 不要为了"让任务跑起来"去启动 API 服务或 `queue worker`；
   - 需要并行时用 `--workers N`（本地多进程，见下）。
2. **默认并行**。串行跑一条十年回测约 10 分钟；一批稳健性回测动辄 20~120 条，
   串行是数小时。**任何会产出多条回测的命令都要显式给出 `--workers`**：

   | 命令 | 并行参数 | 典型批量 |
   | --- | --- | --- |
   | `research batch` | 内部按窗口复用缓存，天然快（分钟级） | 10~30 个变体 |
   | `robustness scan\|ablate\|pool` | `--sync --workers N` | 18~124 条回测 |
   | `optimization evaluate` | `--workers N` | 2+2K 条回测 |
   | `backtest batch` | `--workers N` | 任意条已落库回测 |

   `--workers` 语义：`1`=串行；`N`=并发 N 条；`0`=按 CPU 与内存自动推导。
   未显式给定时会读环境变量 `QUANT_ETF_CLI_WORKERS`。
   实测参考（本机 20 核 / 远端数据库）：`robustness scan --preset quick`（18 条回测）
   串行 ≈ 32 分钟、`--workers 6` ≈ 15 分钟；`research batch` 11 变体 × 5 窗口
   （55 段回测）≈ 15 分钟。**并发不是越大越好**：每个子进程都要各自加载行情与因子
   缓存（数百 MB），并发过高会先撞内存与远端数据库连接预算；6~8 是常见甜点区，
   想知道本机最优值就固定同一批任务跑 `--workers 4 / 8 / 12` 比较墙钟时间。
3. **长任务与输出**。并行池会把子进程输出直接打到当前终端（无管道通信），
   所以**不要**把命令接进 `| Select-Object -Last N` 之类的管道——那会把输出缓冲到
   进程结束才显示，中途完全看不到进度。要盯进度就重定向到文件（`*> run.log`）再 tail；
   要事后排查就用 `--log-dir <dir>`（每个子进程一个日志文件）。
4. **中断可续跑**。进度与结果全部落在数据库，所以随时可以中断：
   重跑 `backtest batch --pending` 会自动跳过已完成的回测（子进程带 `--only-pending`）。

## 工作流程

1. **读基线**：`strategy show <id>`，拿到完整 config_json 与元数据。
2. **跑基线回测**（未跑过时）：`backtest run --strategy <id> --start 2016-01-01 --end 2025-12-31`
   （研究与验收都用 T+1 收盘口径时加 `--execution-model t_plus_1_close`，见「关键约束」），
   记录年化/夏普/回撤/超额等指标作为对照。
3. **探索**：`research batch` 先筛掉没价值的想法（不落库、分钟级、与平台同一条执行路径）：

   ```bash
   python -m quant_etf_api.cli research batch --strategy <基线> --variants variants.json \
       --windows 5 --cost-bps 10 --execution-model t_plus_1_close --summary
   ```

   变体文件里每个变体给完整 `config`，或只给 `patch`（改了哪几个参数就写哪几项）：

   ```json
   {"variants": [
     {"label": "topn4", "patch": {"rank.top_n": 4}},
     {"label": "loose_drawdown", "patch": {"filters.rules[1].value": -30}},
     {"label": "add_breadth", "patch": {"filters.rules[+]": {"factor": "breadth_ma20_pct", "op": "gt", "value": 30}}}
   ]}
   ```

   `patch` 只能改写成已有字段；**删因子 / 删过滤条件属于结构改动**，
   要么给完整 `config`，要么直接走 `robustness ablate`（那才是消融的正式口径）。
   列表下标必须写成 `rules[1]`（`rules.1` 会被当作字典键而报错），**追加**一条规则写
   `rules[+]`；值可以是数值、字符串或对象（如整条过滤规则）。
   ⚠️ 变体给的 `config` 是**整体替换**而非合并：只写 `{"filters": ...}` 会因缺 `score` 等
   必填模块而解析失败——改单个模块请用 `patch`。

   输出含逐窗口毛/净口径、多档成本、`vs_baseline`（Δ净年化/Δ净夏普/劣化窗口占比）与口径指纹。
   **`caliber.persisted=false` 的结果只能用来决定"值不值得走正式回测"，不能写进验收结论。**
   想看邻域/消融/池扰动的正式口径时，仍然走第 7 步的 `robustness`。
   想引入新因子而非微调参数时，优先考虑：`sharpe_60d`（风险调整动量）、
   `ma60d_deviation`（配 `trend_score` 做趋势强度）、`amount_ratio_20d`（成交额确认）、
   `drawdown_current`（配 `drawdown_score`）、`pmi_momentum_3m` 与 `breadth_ma20_pct`
   （市场级因子，放 timing / filters，勿放横截面评分）。
4. **写候选文件并校验**：完整 config_json 存入 `candidates/` 下的 JSON 文件，`strategy validate --file <path>` 通过后再用。**一轮只改一处、可解释的地方**（打分权重、过滤阈值、top_n、调仓频率、择时等），其余保持不变。
5. **开会话**：
   `optimization start --strategy <基线> --candidate-file <path> --hypothesis "<假设>" --version <新版本> [--start 2016-01-01 --end 2025-12-31] --folds 4 [--execution-model ...]`。
   `--version` 必传：promote 时基线版本取该值，不传会沿用旧版本号导致版本不递增。
6. **评估（并行）**：
   ```bash
   python -m quant_etf_api.cli optimization evaluate <opt_id> --folds 4 --workers 6 --log-dir .optlogs
   ```
   它一次性创建 2+2K 条回测行再交给本地多进程池；失败条目留在库里，
   `optimization show <opt_id>` 的 `missing_backtest_ids` / 折指标缺失能看出来。
   中断后重跑同一条命令即可（已 success 的回测不会被重复执行）。
   多方向探索时，**每个方向一个会话**，用子代理并行推进（见「并行执行」）。
7. **稳健性验证**（评估通过后、收尾之前必做）：
   - 邻域：`robustness scan --strategy <基线或候选> --preset quick --sync --workers 6`
     （quick=2 窗口/8 旋钮/18 条回测，够回答"平台还是尖峰"；要写进报告的完整结论用
     `--preset standard`）；也可 `--knobs a,b` / `--knobs-file knobs.json` 指定关键旋钮；
   - 消融：`robustness ablate --strategy <基线或候选> --sync --workers 6` 看因子边际贡献；
   - 池扰动：`robustness pool --strategy <基线或候选> --sync --workers 6 --samples 6`；
   - 汇总与统计：`robustness collect <id>` → `robustness stats <id> [--n-trials N] [--cost-bps 10]`
     （PBO / Deflated Sharpe；窗口数 <4 时 PBO 为 null，看 `pbo.reason`）。
   详见 [references/robustness.md](references/robustness.md)。
   **证据必须与本次会话同配置哈希、同执行口径**，否则第 6 项清单不通过（见下）。
8. **出报告**：`optimization report <opt_id> --file <path>` 生成骨架，补写"分析结论"：假设是否成立、数据支持（含净口径与稳健性数字）、风险、下一步方向。
9. **收尾**：对照验收清单（共 7 项，`finish` **默认强制**，只有 `--no-strict` 才跳过）——
   - 通过 → `optimization finish <opt_id> --verdict accept --report-file <path> --promote`
   - 未通过 → `--verdict reject`（不 promote），会话保留作审计。
   - 确需在清单未全过时接受时用 `--no-strict`，并在报告的"分析结论"里写明哪一项没过、为什么不构成否决。
10. **确认落地**：`strategy show <基线>` 验证 promote 生效，必要时用 `strategy update --version` 补版本号。
11. **收尾清理**：这一轮产生的稳健性变体草稿策略（`<基线>__rbXXXX_*`）用
    `strategy prune-variants --batch <批次>` 预演、确认后 `--apply` 清理（变体已被回测引用时
    需 `--force` 才连带删除回测）。**不要删除 `robustness_run` 批次行**——它是 Deflated Sharpe
    的试验次数台账。

## 并行执行（多方向 / 多段加速）

并行只有两种正当形态，**都不要服务端**：

### 一次会话内并行（首选）

`--workers N` 把该命令产生的整批回测交给本地多进程池：每个子进程是一条独立的
`cli backtest execute <id>`，各自连库写自己的回测行，父进程只做派生与等待。
好处是失败局部化：某条回测挂了只影响它自己，重跑 `backtest batch --pending` 即可续跑。

### 多个方向并行（子代理）

- 防过拟合原则不变："每轮只测一个假设"约束的是同一候选内的改动；多个**独立**假设可以并行验证，每个方向一个候选文件 + 一个 optimization 会话，各自单独 evaluate 与 verdict。
- 推荐派子代理并行：每个方向一个子代理，负责 validate → `optimization start` →
  `optimization evaluate --workers N` → 轮询 → 出报告与初步结论；根代理汇总所有方向后，
  再决定 accept/reject/promote 哪一个。
- **并发预算要相加**：同时跑 M 个子代理、各自 `--workers N`，总进程数约 M×N。
  按 `M × N ≤ 核数` 分配（本机 20 核 → 例如 3 个子代理各 `--workers 6`），
  否则子代理之间会互相抢 CPU 与数据库连接，整体更慢。
- `research batch` 不落库、按窗口共享缓存，最省资源，适合在派子代理之前先自己筛一轮。

### 分段区间

研究期内任意跨度都可单次回测，**不存在跨度上限**，分段只是分析视角（分年度绩效 + 三段一致性）。
逐段对比时每段单独 `backtest run`（可多条入一支 `backtest batch --ids`），
或者直接把 `--folds` 调到每折 ≈ 1-2 年。

## 关键约束

- **防过拟合**：每轮只测一个假设；以滚动样本外（fold）聚合为准，不能只看全区间；`finish` 默认强制验收清单（`--no-strict` 才跳过）。
  **fold 一致性 ≠ 样本外**：候选是看过全区间结果后提出的，4 折只是同一研究期内的
  一致性检查；真正的样本外只有 2026-01-01 起的验证期，且一旦观察即被消费。
- **研究期 / 验证期边界（硬约束）**：研究期固定为 2016-01-01 ~ 2025-12-31，
  2026-01-01 起为验证期。优化属于研究行为，区间越过研究期末端会被**系统直接拒绝**，
  `optimization start` 的默认 `--end` 即研究期末端。**禁止读验证期结果来调参**：
  一旦看过，那段数据就不再是样本外。
- **回测区间下限（用户约定）**：所有回测、对比与优化评估的 `--start` 一律取 2016-01-01，不使用 2016 年之前的数据；滚动分段的第一段即 2016-01-01 起。
- **执行模型是口径，必须两侧一致**：`--execution-model t_plus_1_open|t_plus_1_close`
  （`backtest run` / `optimization start` / `robustness scan|ablate|pool` / `research batch` 均支持，
  缺省 `t_plus_1_open`）。会话与批次会把该值落库，`evaluate` / `finish` 一律复用会话值，
  探索（`research batch`）、验收（`backtest run`）与邻域证据（`robustness scan`）必须同口径，
  否则比较的是两种执行假设而不是两个配置。**切换执行模型会改变可交易资产域**：
  缺历史开盘价的指数在 `t_plus_1_open` 下按 `EXECUTION_PRICE_MISSING` 被剔出候选池
  （实测同一配置候选池中位 34/35 → 25/35）。
- **净口径成本**：回测汇总为毛收益口径，验收必须同时看净口径指标
  （成本默认取系统配置 `default_cost_bps`，`backtest run --cost-bps` 可覆盖）。
  净收益 = 毛收益 − 单边换手 × 成本；换手越高，成本对结论的影响越大。
- **验收清单默认阈值**（`finish` 默认强制 accept，共 7 项；`--no-strict` 可显式跳过）：
  1. 验证窗平均夏普 ≥ 基线；
  2. 平均最大回撤劣化 ≤ 2pct；
  3. 夏普胜出折数 ≥ 50%（只统计基线/候选两侧都有值的**配对折**）；
  4. 验证窗平均累计收益 ≥ 基线；
  5. **净成本口径**验证窗平均夏普 ≥ 基线；
  6. **参数邻域**无方向反转且处于平台（需先跑 `robustness scan`，且批次的
     `baseline_config_hash` 必须等于**本次会话的基线或候选配置哈希**、`execution_model`
     与会话相同——promote 之后上一轮的旧批次、或另一执行口径下的批次都不再被复用；
     未提供匹配证据直接判不通过）；
  7. **分段一致性**：剔除最好折后候选夏普仍不低于基线。
- **试验次数台账**：`robustness stats --n-trials` 缺省取"该策略历史所有稳健性批次的
  变体总数 + 已评估的优化会话数"（`statistics.n_trials_breakdown` 给出拆分）；
  但**它不含 `research batch` 的探索变体**，手工做过但不入批次/会话的对比也要显式补，
  多重检验的 N 只会被低估不会被高估。做过多轮探索时按累计口径显式传 `--n-trials`。
- **改动要小且可解释**；候选明显更差时先 reject 换假设，不要在同一轮叠加多个改动。
- **并行前提与上限**：并发度受 CPU、**内存**（每个子进程各自加载行情与因子缓存）与远端
  数据库连接预算共同约束；默认自动推导上限 8，可用 `--workers` 或
  `QUANT_ETF_CLI_WORKERS` 覆盖。别用 `ProcessPoolExecutor`-式的管道并行——受限环境下
  会被拒绝；本平台的池实现走独立子进程、无管道通信。
- 命令默认 JSON 输出。
- 数据库在远程服务器；沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。终端中文乱码只是控制台代码页问题，落库数据是 UTF-8。

## 参考

- [references/cli.md](references/cli.md)：全部命令、参数、评估语义与输出形态。
