---
name: strategy-optimizer
description: >
  对量化系统中的策略执行 AI 自动优化闭环：基线回测 → 单假设候选改动 → 滚动样本外验证 →
  优化报告 → accept/reject 并 promote。当用户要求"优化/改进某个策略"、"跑一轮策略优化"、
  "根据回测结果调整策略"时使用。不用于：仅配置新策略（用 strategy-builder）、纯策略理论讨论、
  手工逐个跑回测分析。
---

# 策略优化器

> 先读根目录 `AGENTS.md`，命令参数以 CLI `--help` 为准；本技能中的并行度与耗时数字只作参考。不得启动自动交易、用验证期反向调参，或绕过策略生命周期的人工确认。

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

   `--workers` 语义：`1`=串行；`N`=并发 N 条；`0`=先读环境变量 `QUANT_ETF_CLI_WORKERS`，没有再按
   CPU 与内存自动推导（上限 8）。`robustness scan|ablate|pool` 与 `optimization evaluate` 不写时缺省 `1`
   （串行）；`backtest batch` 不写时等同 `0`。
   **并发不是越大越好**：每个子进程各自加载行情与因子缓存（数百 MB），并发过高会先撞内存与远端数据库
   连接预算；6~8 是常见甜点区，想知道本机最优值就固定同一批任务比较不同 `--workers` 的墙钟时间。
3. **长任务与输出**。并行池会把子进程输出直接打到当前终端（无管道通信），
   所以**不要**把命令接进 `| Select-Object -Last N` 之类的管道——那会把输出缓冲到
   进程结束才显示，中途完全看不到进度。要盯进度就重定向到文件（`*> run.log`）再 tail；
   要事后排查就用 `--log-dir <dir>`（每个子进程一个日志文件）。
4. **中断可续跑**。进度与结果全部落在数据库，所以随时可以中断：
   重跑 `backtest batch --pending` 会自动跳过已完成的回测（子进程带 `--only-pending`）。

## 工作流程

0. **读台账、登记假设**（开工第一步）：
   ```bash
   python -m quant_etf_api.cli research ledger --strategy <id>      # 进行中假设、近期结论、N 拆分
   python -m quant_etf_api.cli research hypothesis list --index <指数代码> --status rejected
   ```
   已被否决的方向不要换个说法重试；要在已有结论上细化时用 `--parent <父假设ID>` 登记子假设。
   确定本轮方向后**先登记再检验**，判定标准检验前写定、检验后不改：
   ```bash
   python -m quant_etf_api.cli research hypothesis add --strategy <id> --category timing \
       --statement "<假设>" --criteria "<主要指标与通过阈值>" \
       --execution-models t_plus_1_open,t_plus_1_close --index-codes 000852 --tags pv_resonance
   ```
   之后的 `research batch` / `optimization start` / `robustness scan|ablate|pool` 都带
   `--hypothesis-id <hid>`。
1. **读基线**：`strategy show <id>`，拿到完整 config_json 与元数据。
2. **跑基线回测**（未跑过时）：`backtest run --strategy <id> --start 2016-01-01 --end 2025-12-31`
   （研究与验收统一按 T+1 收盘口径，CLI 缺省即 `t_plus_1_close`，见「关键约束」），
   记录年化/夏普/回撤/超额等指标作为对照。
3. **诊断（先于变体探索）**：想看"某因子/信号到底有没有预测力、阈值切在哪"时，
   **用平台诊断命令，不要在 `candidates/` 里手写 pandas 原型脚本**（手写脚本的收益口径、
   数据清洗与研究期边界都和平台不一致，结论无法迁移）。这些命令只读、秒级、不落库，
   区间缺省为研究期全段、越过 2025-12-31 直接拒绝，前瞻收益也不越过研究期末端：

   ```bash
   # 因子值分桶 → 各桶未来 N 日收益均值 / 胜率 / t 值 / 样本数 + 前后两段一致性
   python -m quant_etf_api.cli research buckets --strategy <id> --ref <因子别名> \
       --edges 0.5,1,1.2,1.5 --horizons 1,5,20 --split-halves --summary
   # 截面 Rank IC（与因子中心同一实现）：汇总 + 分年
   python -m quant_etf_api.cli research factor-ic --factor <模板ID> --params '{"period": 60}' \
       --forward-days 5 --summary
   # 策略 timing 块走引擎口径的择时评估（IC fwd20/past20、翻转频率、持仓收益、分年）
   python -m quant_etf_api.cli research signal-lab timing --index <评估指数> \
       --signal existing_composite --strategy <id> --summary
   ```

   `research buckets` 的 `--ref` 是策略择时因子时缺省在其代理指数上计算；收益口径
   `--basis` 缺省 `t_plus_1_close`，须与后续回测的执行模型一致。`--quantiles` 的边界取自
   全样本，只作描述性诊断；**桶的切点/阈值不得反复按诊断结果微调后直接写进候选**——
   诊断用来提出假设，阈值是否稳健仍由 `research batch` / `robustness scan` 判定。
   命令清单见 [references/cli.md](references/cli.md) 的「research 诊断」。

   **探索**：`research batch` 先筛掉没价值的想法（不落库、分钟级、与平台同一条执行路径）：

   ```bash
   python -m quant_etf_api.cli research batch --strategy <基线> --variants variants.json \
       --windows 5 --execution-model t_plus_1_close --summary
   ```

   变体文件里每个变体给完整 `config`，或只给 `patch`（改了哪几个参数就写哪几项）：

   ```json
   {"variants": [
     {"label": "topn4", "patch": {"rank.top_n": 4}},
     {"label": "loose_drawdown", "patch": {"filters.rules[1].value": -30}},
     {"label": "add_breadth", "patch": {"filters.rules[+]": {"factor": "breadth_ma_pct", "op": "gt", "value": 30}}}
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
   想引入新因子而非微调参数时，优先考虑模板（参数在 `factor_aliases` 声明，见
   `.agents/skills/strategy-builder/references/factors.md`）：`sharpe`（风险调整动量）、
   `ma_deviation`（配 `trend_score` 做趋势强度）、`amount_ratio`（成交额确认）、
   `drawdown`（配 `drawdown_score`）、`pmi_momentum` 与 `breadth_ma_pct`
   （后者是市场级因子，放 timing / filters，勿放横截面评分）。
   派生因子（`expr`）进入 `research batch` 变体或 `optimization start` 候选前，必须先有
   `research factor-ic` 或 `research buckets` 的诊断记录，否则命令直接拒绝。
4. **写候选文件并校验**：完整 config_json 存入 `candidates/` 下的 JSON 文件，`strategy validate --file <path>` 通过后再用。**一轮只改一处、可解释的地方**（打分权重、过滤阈值、top_n、调仓频率、择时等），其余保持不变。
5. **开会话**：
   `optimization start --strategy <基线> --candidate-file <path> --hypothesis-id <hid> --version <新版本> [--start 2016-01-01 --end 2025-12-31] --folds 4 [--execution-model ...]`
   （`--hypothesis` 缺省取台账中的假设陈述；`--start/--end` 缺省覆盖整个研究期）。
   `--version` 必传：promote 时基线版本取该值；不传时候选继承基线版本号，promote 后版本不变。
   组合策略（`kind=composite`）不能开优化会话，对成员分别优化。
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
   - 汇总与统计：`robustness collect <id>` → `robustness stats <id> [--n-trials N] [--cost-bps <成本>]`
     （PBO / Deflated Sharpe；窗口数 <4 时 PBO 为 null，看 `pbo.reason`）。
   详见 [references/robustness.md](references/robustness.md)。
   **证据必须与本次会话同配置哈希、同执行口径**，否则第 6 项清单不通过（见下）。
8. **出报告与对账**：`optimization report <opt_id> --file <path>` 生成骨架，补写"分析结论"：假设是否成立、数据支持（含净口径与稳健性数字）、风险、下一步方向。准备 accept 前对候选的全区间落库回测跑 `backtest verify <回测ID>`（只读独立对账），逐日记账有差异时不能接受。
9. **收尾**：对照验收清单（共 10 项，`finish` **默认强制**，只有 `--no-strict` 才跳过）——
   - 通过 → `optimization finish <opt_id> --verdict accept --report-file <path>`；**promote 必须先得到用户明确同意**，
     同意后再加 `--promote`（或另行执行）。基线处于 LIVE 时配置已冻结，不 promote，状态如何变更由用户决定。
   - 未通过 → `--verdict reject`（不 promote），会话保留作审计。
   - 确需在清单未全过时接受时用 `--no-strict --no-strict-reason "<理由>"`（理由必填，连同未通过项写入
     研究台账），并在报告的"分析结论"里写明哪一项没过、为什么不构成否决。
   - **关闭假设**：`research hypothesis close <hid> --verdict accepted|rejected|inconclusive|blocked_by_schema --reason "<数据支撑与失败点>"`。
     会话 verdict 是一个候选的结论，假设结论要综合全部试验；配置写不出来时用
     `blocked_by_schema`（schema 补齐后 `research hypothesis reopen`）。关闭后的假设不再接收试验。
10. **确认落地**：`strategy show <基线>` 验证 promote 生效，必要时用 `strategy update --version` 补版本号。
11. **收尾清理**：需要腾空间时用 `backtest slim --strategy <基线>` 预演、确认后 `--apply`，
    只删除研究线回测的逐指数明细，台账、会话、批次、草稿与 N 不变。`strategy prune-variants`
    会删除变体草稿（`--force` 连带删除回测，之后该批次不能再 collect / stats 或参与 DSR 校准），
    只在人工确认后使用；两者在战役模式下都拒绝执行。**不要删除 `robustness_run` 批次行**——它是
    Deflated Sharpe 的试验次数台账。

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
- **研究期 / 验证期边界（硬约束）**：规则见 `AGENTS.md`。优化属于研究行为，区间越过研究期末端
  （2025-12-31）会被系统直接拒绝；**禁止读验证期结果来调参**，一旦看过，那段数据就不再是样本外。
- **回测区间下限（用户约定）**：所有回测、对比与优化评估的 `--start` 一律取 2016-01-01，不使用 2016 年之前的数据；滚动分段的第一段即 2016-01-01 起。
- **执行模型是口径，必须两侧一致**：`--execution-model t_plus_1_open|t_plus_1_close`
  （`backtest run` / `optimization start` / `robustness scan|ablate|pool` / `research batch` 均支持，
  CLI 缺省 `t_plus_1_close`，即研究与验收的统一口径；`t_plus_1_open` 只作专项对照时显式传）。
  会话与批次会把该值落库，`evaluate` / `finish` 一律复用会话值，
  探索（`research batch`）、验收（`backtest run`）与邻域证据（`robustness scan`）必须同口径，
  否则比较的是两种执行假设而不是两个配置。**切换执行模型会改变可交易资产域**：
  缺历史开盘价的指数在 `t_plus_1_open` 下按 `EXECUTION_PRICE_MISSING` 被剔出候选池，候选池可能明显变小。
- **净口径成本**：回测汇总为毛收益口径，验收必须同时看净口径指标。成本缺省取 `default_cost_bps`（0.5bp，
  按单边换手折算的往返成本），`backtest run --cost-bps` 可覆盖；详情另并列 0 / 0.5 / 5 / 10bp 成本阶梯。
  净收益 = 毛收益 − 单边换手 × 成本；换手越高，成本对结论的影响越大。
- **验收清单默认阈值**（`finish` 默认强制 accept，共 10 项；`--no-strict` 可显式跳过，须给理由）：
  1. 验证窗平均夏普 ≥ 基线；
  2. 平均最大回撤劣化 ≤ 2pct；
  3. 夏普胜出折数 ≥ 50%（只统计基线/候选两侧都有值的**配对折**）；
  4. 验证窗平均累计收益 ≥ 基线；
  5. **净成本口径**验证窗平均夏普 ≥ 基线；
  6. **参数邻域**无方向反转且处于平台（需先跑 `robustness scan`；只接受 `baseline_config_hash` 等于
     **本次会话的基线或候选配置哈希**、且 `execution_model` 与会话相同的批次，其他批次一律不算；
     未提供匹配证据直接判不通过）；
  7. **分段一致性**：剔除最好折后候选夏普仍不低于基线；
  8. **统计显著性**：候选相对基线的全区间逐日**超额**净收益（候选 − 基线，默认成本）的
     Deflated Sharpe ≥ 0.95，N 取**研究线**累计试验数（`acceptance_min_deflated_sharpe`）；
     检验的是改进本身而非候选绝对夏普（后者只在证据里作参考）；N 统计失败时本项判不通过；
  9. **分年度一致性**：逐自然年比较净夏普，剔除候选领先最多的一年后候选年均夏普仍 ≥ 基线，
     且胜出年数不少于一半（需 ≥ 3 个完整年份）；
  10. **差异事件数**：候选与基线逐日净收益差异 > 1bp 的交易日（间隔 ≤ 2 日合并为一段）
      至少 8 段、40 天（`acceptance_min_diff_episodes` / `acceptance_min_diff_days`）——
      改善只来自少数几个事件时不可与运气区分。
- **试验次数台账**：`robustness stats --n-trials` 缺省取"该策略历史所有稳健性批次的
  变体总数 + 已评估的优化会话数 + `research batch` 评估过的变体数 + 人工 / 补录试验"
  （`statistics.n_trials_breakdown` 给出拆分，`estimated` 为其中的历史估算部分）。
  `research batch` 每次运行都自动记入台账（不论是否挂假设，输出里的 `ledger` 块给出
  试验 ID；写入失败会显式报错，须用 `research trial add` 补录）。平台外的原型 / 手工对比用
  `research trial add --strategy <id> --count N --note "<依据>"` 补记。
- **研究线（lineage）**：N 按研究线汇总——`optimization start` 的候选与稳健性变体自动继承基线的
  研究线，手工另建的派生策略用 `strategy create --lineage <研究线中任一策略>` 归入（已有策略用
  `strategy update --lineage`；仍有成员的研究线根、自身已有试验的成员不能改归——移出等于拿走 N）。在候选上跑 `robustness stats` 也会计入基线名下的探索
  （`statistics.n_trials_scope` 给出研究线与成员）。验证期消费同样按研究线生效：线上任一策略
  看过验证期，全线留痕。
- **试验预算**：每条研究线累计 N 上限 `research_trial_budget`（默认 100）。达到后
  `research batch` / `optimization start` / `robustness scan|ablate|pool` 直接拒绝；确需继续时加
  `--over-budget "<理由>"`，理由写入研究台账、超出的试验照常计入 N。预算用尽通常意味着
  该研究线应收尾（关闭假设、换方向），而不是继续调参。`research ledger --strategy <id>`
  的 `budget` 块给出已用 / 剩余。
- **改动要小且可解释**；候选明显更差时先 reject 换假设，不要在同一轮叠加多个改动。
- **并行实现**：并发度见「硬约束」第 2 条。别用 `ProcessPoolExecutor`-式的管道并行——受限环境下
  会被拒绝；本平台的池实现走独立子进程、无管道通信。
- 命令默认 JSON 输出。
- 数据库在远程服务器；Codex 沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。终端中文乱码只是控制台代码页问题，落库数据是 UTF-8。

## 参考

- [references/cli.md](references/cli.md)：全部命令、参数、评估语义与输出形态。
