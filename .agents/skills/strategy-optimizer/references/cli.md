# CLI 命令参考

所有命令在 `apps/api` 目录下执行：

```bash
.venv/Scripts/python.exe -m quant_etf_api.cli <group> <command> ...
```

新命令默认 JSON 输出（`--no-json` 转人类可读文本）。

> **只跑 CLI**：整条优化闭环只需要 CLI + 数据库，**不需要** uvicorn / `queue worker` /
> 任何 HTTP 接口。需要并行时用 `--workers N`（本地多进程），不要用 `--async`
> （它只入队，没有服务端消费时任务会永远停在 pending）。

## 并行执行（`--workers`）

以下命令支持 `--workers N`：`robustness scan|ablate|pool`、`optimization evaluate`、
`backtest batch`。

| 取值 | 含义 |
| --- | --- |
| `1` | 串行（默认） |
| `N` | 并发 N 条回测 |
| `0` | 按 CPU 与内存自动推导（上限 8，可用 `QUANT_ETF_CLI_WORKERS` 覆盖） |

实现是**独立 CLI 子进程池**：父进程派生 `cli backtest execute <id>` 子进程并等待，
子进程各自连库写自己的回测行，父子之间**没有管道通信**（受限沙箱里管道会被拒绝，
因此不要用 `ProcessPoolExecutor` 那套）。由此带来两个性质：

- **无输出管道**：子进程 stdout 直通当前终端。因此**不要把并行命令接进
  `| Select-Object -Last N`** —— 输出会被缓冲到进程结束才显示，中途看不到进度；
  要看进度就 `*> run.log` 后 tail，或用 `--log-dir <dir>` 给每个子进程一份日志；
- **中断可续跑**：进度与结果都在数据库，重跑 `backtest batch --pending` 会跳过已完成项。

并发度不是越大越好：每个子进程各自加载行情与因子缓存（数百 MB 量级），
过高会先撞内存与远端数据库连接预算。实测（20 核 / 远端库）：
`robustness scan --preset quick`（18 条回测）串行 ≈ 32 分钟、`--workers 6` ≈ 15 分钟。
找本机甜点值就用同一批任务跑 `--workers 4 / 8 / 12` 比墙钟时间。

## strategy

| 命令 | 说明 |
| --- | --- |
| `strategy list` | 列出启用策略 |
| `strategy show <id>` | 策略详情（含 config_json） |
| `strategy validate --file <path>` | 校验配置（含因子 ID / transform 存在性） |
| `strategy create --id <sid> --name <name> --file <path> [--draft] [--version]` | 创建策略 |
| `strategy update <id> [--file] [--status active\|draft\|disabled] [--version] [--name] [--description]` | 更新策略 |
| `strategy diff <a> <b>` | 两策略配置的 unified diff |
| `strategy prune-variants --batch <批次> [--apply] [--force]` | 清理稳健性变体草稿策略；默认预演；变体仍被回测引用时跳过，`--force` 才连带删回测 |
| `strategy consume-validation <id> [--note "..."]` | 标记"该策略的验证期数据已被消费"（此后验证期证据只能用于否决） |

## backtest

| 命令 | 说明 |
| --- | --- |
| `backtest run --strategy <id> [--start] [--end] [--universe all\|subset] [--index-codes a,b] [--benchmark 000300] [--no-benchmark] [--purpose research\|validation\|monitor] [--purpose-reason] [--cost-bps] [--execution-model t_plus_1_open\|t_plus_1_close] [--async] [--priority N]` | 创建并执行回测；默认同步；`purpose=research` 时 `--end` 缺省为研究期末端（2025-12-31），其他用途缺省为今天；`--start` 缺省为 end 往前 2 年且不早于 2016-01-01；**研究期内任意跨度（1 个月~10 年）都可单次执行，不存在跨度上限，也不再要求分段**；`--execution-model` 缺省 `t_plus_1_open`（T 日信号 T+1 开盘成交），`t_plus_1_close` 为 T 日信号 T+1 收盘成交 |

> **执行模型是口径，不是参数**：两种模型的逐日收益归属不同（收盘口径下信号日不持仓），
> 指标不可互比；口径指纹见 `backtest show` 的 `stability.execution_model`。缺历史开盘价的指数
> 在 `t_plus_1_open` 下会被剔出候选池（表现为 `EXECUTION_PRICE_MISSING` 警告与更小的候选池），
> 因此切换执行模型会同时改变**可交易资产域**，不只是成交时点。
| `backtest status <id> [--wait] [--timeout 600]` | 状态与指标；`--wait` 轮询至终态（failed 退出码 1，超时 2） |
| `backtest show <id> [--cost-bps N] [--cost-ladder 0,10,20,30,50]` | 详情（含 config_snapshot / config_hash / data_cutoff_date / warnings / 口径指纹 / 多档成本） |
| `backtest list [--status] [--purpose] [--calendar-source] [--order-by] [--asc] [--limit]` | 回测列表过滤（状态/用途/日历来源/排序） |
| `backtest cancel <id>` | 取消回测（未开始直接取消，运行中在安全检查点退出） |
| `backtest execute <id> [--only-pending]` | 执行一条**已落库**回测（并行池的子进程入口）；`--only-pending` 时已是终态则跳过并返回 `executed=false` |
| `backtest batch [--ids a,b] [--pending] [--strategy <id>] [--created-from] [--purpose] --workers N [--retries R] [--log-dir <dir>]` | **本地多进程批量执行**已落库回测（只用 CLI + 数据库）；`--ids` 与 `--pending` 二选一；失败条目按 `--retries` 重试；耗时/结果全在库里，中断后重跑 `--pending` 自动续跑 |
| `backtest pool <id>` | 有效候选池逐日时间线与剔除区间 |
| `backtest orphans [--limit N]` / `backtest prune-dangling-refs [--apply]` | 悬挂回测引用审计 / 清理（默认预演） |
| `backtest delete <id> [--force]` | 受控删除回测（运行中先取消；存在 JSONB 引用默认拒绝） |
| `backtest results <id> [--daily] [--index]` | 每日组合绩效 / 每指数信号与收益 |

## optimization

| 命令 | 说明 |
| --- | --- |
| `optimization start --strategy <基线> --candidate-file <path> --hypothesis "<假设>" [--start] [--end] [--folds 4] [--candidate-id] [--version] [--execution-model]` | 建草稿候选 + 会话；候选 ID 默认 `<基线>__opt_<会话前8位>`；`--version` 建议必传（promote 用）；执行模型落库，后续 evaluate/finish 复用 |
| `optimization evaluate <opt_id> [--folds] [--workers N] [--log-dir <dir>] [--async]` | 全区间 + 逐折回测（2+2K 条）；**默认串行，建议 `--workers 6`**：先创建全部回测行再交本地多进程池执行，中断后重跑同一条命令即可续跑（已 success 的不重复执行） |
| `optimization report <opt_id> [--file <path>]` | 生成 Markdown 报告骨架 |
| `optimization finish <opt_id> --verdict accept\|reject [--report-file] [--promote] [--no-strict]` | 结束会话；accept+promote 把候选配置写回基线（strategy_id 不变、version 取候选版本）。**验收清单默认强制**，`--no-strict` 才跳过 |
| `optimization show <opt_id>` | 会话详情（含逐折指标、聚合与 `acceptance_checklist`——收尾前就能看到哪一项没过） |
| `optimization list [--strategy] [--limit]` | 会话列表 |

## robustness（稳健性验证）

所有稳健性回测固定在研究期（2016-01-01 ~ 2025-12-31）内执行。**只跑 CLI 时用
`--sync --workers N`**（本地多进程）；不加 `--sync` 则默认入队、需要服务端 worker 消费。
详见 [robustness.md](robustness.md)。

| 命令 | 说明 |
| --- | --- |
| `robustness scan --strategy <id> [--preset quick\|standard] [--windows 4] [--max-knobs 30] [--knobs a,b] [--knobs-file k.json] [--sync --workers N] [--execution-model]` | 单旋钮邻域扰动：按业务重要性优先扫择时/过滤阈值；`quick`=2 窗口/8 旋钮/18 条回测，`standard`=4 窗口/30 旋钮 |
| `robustness ablate --strategy <id> [--windows 4] [--sync --workers N]` | 因子消融：逐个移除评分因子与过滤条件（标签形如 `ablate_filter_rules1_close_price`，含规则下标） |
| `robustness pool --strategy <id> [--windows 4] [--samples 8] [--sync --workers N]` | 资产池扰动：随机 80% 子池 / 剔除常持 / 剔除后上市 |
| `robustness collect <id> [--wait] [--timeout 3600] [--allow-partial]` | 等待并汇总批次（邻域稳定度 / 边际贡献 / 池扰动分布）；`--allow-partial` 按已完成窗口汇总并写 coverage |
| `robustness cancel\|pause\|resume <id>` | 整批取消 / 暂停 / 恢复（运行中的任务在安全检查点退出） |
| `robustness abandon <id> [--reason "..."]` | 作废批次（把长期 running 的批次显式收口，证据与试验台账保留） |
| `robustness stats <id> [--n-trials] [--cost-bps] [--block 20] [--bootstrap 2000]` | CSCV-PBO / Deflated Sharpe / 块自助法置信区间；窗口数 <4 时 `pbo.value=null` + `reason` |
| `robustness show <id>` / `robustness list [--limit]` | 批次详情（含 `scan_params` 扫描口径与 `execution_model`）/ 列表（含 `is_stale` 停滞提示） |

## research（批量变体探索，不落库）

| 命令 | 说明 |
| --- | --- |
| `research batch --strategy <id> --variants v.json [--windows 5] [--cost-bps 10] [--cost-ladder 0,10,20,30,50] [--no-baseline] [--summary] [--execution-model]` | 同一批变体 × 窗口的离线评估：复用平台执行路径但**不落库**，按窗口共享行情与因子缓存（因此比逐条正式回测快一个量级，是探索阶段的默认工具）；`--summary` 输出「变体 × 指标」排名表（否则是完整 JSON，几十个变体会超输出上限） |

变体文件格式：`{"variants": [{"label": "x", "patch": {"rank.top_n": 4}}, {"label": "y", "config": {...}}]}`。
`patch` 的路径支持列表下标（`filters.rules[1].value`）与列表追加（`filters.rules[+]` 追加一条规则）；
路径不存在或字段拼错都会直接报错。**变体给的 `config` 是整体替换而非合并**，
所以"只加一条过滤规则"要用 `patch`（`rules[+]`），只写 `{"filters": ...}` 的 config 会因缺
`score` 等必填模块而解析失败。
输出中的 `caliber.persisted=false` 表示结果不可审计：**只能用来决定"值不值得走正式回测"**。

## queue（任务队列可观测性）

| 命令 | 说明 |
| --- | --- |
| `queue stats [--window-hours 1]` | 积压 / 吞吐 / 运行中任务 / 并发预算 |
| `queue jobs [--status] [--job-type] [--limit]` | 任务明细（含心跳时间与已耗时） |
| `queue worker` | 以独立进程运行队列 worker（前台阻塞；配合 `QUANT_ETF_JOB_QUEUE_EMBEDDED=false`） |

## lifecycle（上线后监控）

| 命令 | 说明 |
| --- | --- |
| `lifecycle list` | 全部上线策略的生命周期摘要 |
| `lifecycle show <strategy_id>` | 生命周期详情（冻结快照 + 最近体检记录） |
| `lifecycle online <strategy_id> [--live-at] [--note] [--cost-bps]` | 人工标记上线：冻结配置 + 生成研究期分布（首次可能要跑十年回测） |
| `lifecycle status <strategy_id> --set LIVE\|SUSPENDED\|RETIRED [--note]` | 人工变更状态（系统不会自动切换） |
| `lifecycle refresh <strategy_id> [--cost-bps]` | 同步跑一次上线后回测并追加健康快照 |

## 评估语义

- 折叠：`[start, end]` 按交易日等分为 K 个连续验证窗，最后一折吸收余数；每折对基线与候选各跑一次回测。建议每折跨度 ≈ 1-2 年（K ≈ 总年数 ÷ 1.5，至少 3）。
- 区间下限（用户约定）：所有回测/评估的 `--start` 一律取 `2016-01-01`，不使用 2016 年之前的数据。
- 研究期 / 验证期边界（硬约束）：研究期为 2016-01-01 ~ 2025-12-31，验证期为 2026-01-01 起。
  `purpose=research` 的回测与优化会话越过研究期末端会被直接拒绝；验证/监控用途允许使用验证期数据，但会进入留痕列表（`GET /backtests/validation-usage`）。
- 分段原则（C4 起）：研究期内任意跨度都可单次回测，**不存在跨度上限、也不再要求"总跨度 > 5 年必须分段"**。分段只是可选的分析视角（分年度绩效 + 三段一致性），需要逐段比较时再按自然年/等分段跑。
- 探索与验收分工（D1）：几十个变体的快速筛选用 `research batch`（不落库、分钟级、同一条执行路径），**验收与报告数字必须来自落库回测**（`backtest run` / `robustness`）。
- **并行执行两条路，默认选第一条**：
  1. **本地多进程池（推荐，不依赖服务端）**：`--workers N` 派生独立 `cli backtest execute`
     子进程，各自连库写结果，父子无管道通信。适用于 `robustness scan|ablate|pool`、
     `optimization evaluate`、`backtest batch`。中断可续跑。
  2. **后台任务队列（可选，需要服务端）**：`--async` 只把任务写入 `background_job`，
     由 uvicorn 多 worker 或 `queue worker` 进程经 `FOR UPDATE SKIP LOCKED` 认领执行，
     实际并行度 = min(worker 数, CPU 核心数)；入队后 CLI 可退出、任务继续在服务端执行。
     **没有服务端时不要用**。
- `fold_summary`：每个指标输出基线/候选的均值、中位数、候选胜出折数；只统计两侧都有值的**配对折**（`paired_folds`），`evaluated_folds` 是实际评估的折数。
- 验收清单默认阈值（共 7 项）：验证窗平均夏普 Δ≥0；平均最大回撤劣化 ≤ 2pct；夏普胜出折数 ≥ 50%；验证窗平均累计收益 Δ≥0；净成本口径夏普 Δ≥0；参数邻域无方向反转（需先跑 `robustness scan`，且批次的配置哈希**与执行口径**都匹配本次会话）；分段一致性（剔除最好折后候选夏普不低于基线）。**`finish` 默认强制全部满足**，`--no-strict` 才跳过。
- 验收清单现在随 `optimization show` 一起返回（`acceptance_checklist`），不必先 `finish` 就能看到哪一项没过；`metrics_full` / `metrics_folds` 也带净口径（`net_sharpe_ratio` / `net_annualized_return_pct` / `annualized_turnover`）。
- `optimization start` 的 `--start` 缺省为**研究期起点**（不是"最近两年"）；`finish --promote` 会把版本历史追加进策略描述。

## 回测快照与会话

- `backtest_run` 创建时写入 `config_snapshot`（元数据 + config_json）与 `config_hash`（sha256）；执行时优先用快照重建配置，保证结果可复现，旧行无快照时回退实时配置。
- `strategy_optimization` 表保存会话级审计：基线/候选 ID 与哈希、回测 ID、逐折指标、聚合统计、报告全文与 accept/reject 结论。

## 环境注意

- 数据库在远程 PostgreSQL（见 `.env` 的 DATABASE_URL）；沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。
- 终端中文乱码是控制台代码页问题，不影响落库数据（UTF-8）。
- 单条回测用 `backtest status <id> --wait` 等待终态，避免高频轮询；
  批量执行看 `backtest batch` / `robustness collect` 的汇总，不要逐条轮询。
- 并发子进程数受 CPU、内存与远端数据库连接预算共同约束；默认自动推导上限 8，
  也可用 `QUANT_ETF_CLI_WORKERS` 固定。
- 并行命令的输出**不要接管道**（会被缓冲到结束才显示）；要进度就重定向到文件或 `--log-dir`。
