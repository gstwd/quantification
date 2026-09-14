# CLI 命令参考

所有命令在 `apps/api` 目录下执行：

```bash
.venv/Scripts/python.exe -m quant_etf_api.cli <group> <command> ...
```

新命令默认 JSON 输出（`--no-json` 转人类可读文本）。

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
| `backtest run --strategy <id> [--start] [--end] [--universe all\|subset] [--index-codes a,b] [--benchmark 000300] [--no-benchmark] [--purpose research\|validation\|monitor] [--purpose-reason] [--cost-bps] [--async] [--priority N]` | 创建并执行回测；默认同步；`purpose=research` 时 `--end` 缺省为研究期末端（2025-12-31），其他用途缺省为今天；`--start` 缺省为 end 往前 2 年且不早于 2016-01-01；**研究期内任意跨度（1 个月~10 年）都可单次执行，不存在跨度上限，也不再要求分段** |
| `backtest status <id> [--wait] [--timeout 600]` | 状态与指标；`--wait` 轮询至终态（failed 退出码 1，超时 2） |
| `backtest show <id> [--cost-bps N] [--cost-ladder 0,10,20,30,50]` | 详情（含 config_snapshot / config_hash / data_cutoff_date / warnings / 口径指纹 / 多档成本） |
| `backtest list [--status] [--purpose] [--calendar-source] [--order-by] [--asc] [--limit]` | 回测列表过滤（状态/用途/日历来源/排序） |
| `backtest cancel <id>` | 取消回测（未开始直接取消，运行中在安全检查点退出） |
| `backtest pool <id>` | 有效候选池逐日时间线与剔除区间 |
| `backtest orphans [--limit N]` / `backtest prune-dangling-refs [--apply]` | 悬挂回测引用审计 / 清理（默认预演） |
| `backtest delete <id> [--force]` | 受控删除回测（运行中先取消；存在 JSONB 引用默认拒绝） |
| `backtest results <id> [--daily] [--index]` | 每日组合绩效 / 每指数信号与收益 |

## optimization

| 命令 | 说明 |
| --- | --- |
| `optimization start --strategy <基线> --candidate-file <path> --hypothesis "<假设>" [--start] [--end] [--folds 4] [--candidate-id] [--version]` | 建草稿候选 + 会话；候选 ID 默认 `<基线>__opt_<会话前8位>`；`--version` 建议必传（promote 用） |
| `optimization evaluate <opt_id> [--folds] [--async]` | 全区间 + 逐折回测；多方向或多段执行时按批控制跨度与并发 |
| `optimization report <opt_id> [--file <path>]` | 生成 Markdown 报告骨架 |
| `optimization finish <opt_id> --verdict accept\|reject [--report-file] [--promote] [--strict]` | 结束会话；accept+promote 把候选配置写回基线（strategy_id 不变、version 取候选版本） |
| `optimization show <opt_id>` | 会话详情（含逐折指标与聚合） |
| `optimization list [--strategy] [--limit]` | 会话列表 |

## robustness（稳健性验证）

所有稳健性回测固定在研究期（2016-01-01 ~ 2025-12-31）内执行，默认入队异步运行
（CLI 用 `--sync` 改为同步），详见 [robustness.md](robustness.md)。

| 命令 | 说明 |
| --- | --- |
| `robustness scan --strategy <id> [--preset quick\|standard] [--windows 4] [--max-knobs 30] [--knobs a,b] [--knobs-file k.json] [--sync --parallel N]` | 单旋钮邻域扰动：按业务重要性优先扫择时/过滤阈值；`quick`=2 窗口/8 旋钮轻量体检；`--parallel` 走本地进程池并行（仅同步模式） |
| `robustness ablate --strategy <id> [--windows 4] [--sync --parallel N]` | 因子消融：逐个移除评分因子与过滤条件（标签形如 `ablate_filter_rules1_close_price`，含规则下标） |
| `robustness pool --strategy <id> [--windows 4] [--samples 8] [--sync --parallel N]` | 资产池扰动：随机 80% 子池 / 剔除常持 / 剔除后上市 |
| `robustness collect <id> [--wait] [--timeout 3600] [--allow-partial]` | 等待并汇总批次（邻域稳定度 / 边际贡献 / 池扰动分布）；`--allow-partial` 按已完成窗口汇总并写 coverage |
| `robustness cancel\|pause\|resume <id>` | 整批取消 / 暂停 / 恢复（运行中的任务在安全检查点退出） |
| `robustness abandon <id> [--reason "..."]` | 作废批次（把长期 running 的批次显式收口，证据与试验台账保留） |
| `robustness stats <id> [--n-trials] [--cost-bps] [--block 20] [--bootstrap 2000]` | CSCV-PBO / Deflated Sharpe / 块自助法置信区间；窗口数 <4 时 `pbo.value=null` + `reason` |
| `robustness show <id>` / `robustness list [--limit]` | 批次详情（含 `scan_params` 扫描口径）/ 列表（含 `is_stale` 停滞提示） |

## research（批量变体探索，不落库）

| 命令 | 说明 |
| --- | --- |
| `research batch --strategy <id> --variants v.json [--windows 5] [--cost-bps 10] [--cost-ladder 0,10,20,30,50] [--no-baseline] [--summary]` | 同一批变体 × 窗口的离线评估：复用平台执行路径但**不落库**，按窗口共享行情与因子缓存；`--summary` 输出「变体 × 指标」排名表（否则是完整 JSON，几十个变体会超输出上限） |

变体文件格式：`{"variants": [{"label": "x", "patch": {"rank.top_n": 4}}, {"label": "y", "config": {...}}]}`。
`patch` 的路径支持列表下标（`filters.rules[1].value`）；路径不存在或字段拼错都会直接报错。
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
- 异步并行：`backtest run --async` 与 `optimization evaluate --async` 只把任务写入 `background_job`，由服务端 uvicorn 多 worker 进程经 `FOR UPDATE SKIP LOCKED` 并行认领执行，互不重复；实际并行度 = min(worker 数, CPU 核心数)。多方向/多段并行时用 `--async`，入队后 CLI 进程可退出，任务继续在服务端执行。
- `fold_summary`：每个指标输出基线/候选的均值、中位数、候选胜出折数。
- 验收清单默认阈值（共 7 项）：验证窗平均夏普 Δ≥0；平均最大回撤劣化 ≤ 2pct；夏普胜出折数 ≥ 50%；验证窗平均累计收益 Δ≥0；净成本口径夏普 Δ≥0；参数邻域无方向反转（需先跑 `robustness scan`）；分段一致性（剔除最好折后候选夏普不低于基线）。`--strict` 时 accept 必须全部满足。
- 验收清单现在随 `optimization show` 一起返回（`acceptance_checklist`），不必先 `finish` 就能看到哪一项没过；`metrics_full` / `metrics_folds` 也带净口径（`net_sharpe_ratio` / `net_annualized_return_pct` / `annualized_turnover`）。
- `optimization start` 的 `--start` 缺省为**研究期起点**（不是"最近两年"）；`finish --promote` 会把版本历史追加进策略描述。

## 回测快照与会话

- `backtest_run` 创建时写入 `config_snapshot`（元数据 + config_json）与 `config_hash`（sha256）；执行时优先用快照重建配置，保证结果可复现，旧行无快照时回退实时配置。
- `strategy_optimization` 表保存会话级审计：基线/候选 ID 与哈希、回测 ID、逐折指标、聚合统计、报告全文与 accept/reject 结论。

## 环境注意

- 数据库在远程 PostgreSQL（见 `.env` 的 DATABASE_URL）；沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。
- 终端中文乱码是控制台代码页问题，不影响落库数据（UTF-8）。
- 同步执行使用 `status --wait`，异步执行按 worker 数分批；单次回测通常仍控制在 1-2 年，长跨度按段执行，便于比较、重试和控制资源。
