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

## backtest

| 命令 | 说明 |
| --- | --- |
| `backtest run --strategy <id> [--start] [--end] [--universe all\|subset] [--index-codes a,b] [--benchmark 000300] [--no-benchmark] [--purpose research\|validation\|monitor] [--purpose-reason] [--cost-bps] [--async]` | 创建并执行回测；默认同步；`purpose=research` 时 `--end` 缺省为研究期末端（2025-12-31），其他用途缺省为今天；`--start` 缺省为 end 往前 2 年且不早于 2016-01-01；单次跨度建议 ≤ 2 年，长跨度按 1-2 年滚动分段执行 |
| `backtest status <id> [--wait] [--timeout 600]` | 状态与指标；`--wait` 轮询至终态（failed 退出码 1，超时 2） |
| `backtest show <id>` | 详情（含 config_snapshot / config_hash / data_cutoff_date / warnings） |
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
| `robustness scan --strategy <id> [--windows 4] [--max-knobs 30] [--sync]` | 单旋钮邻域扰动：从配置数值叶子派生 ±1 档变体 |
| `robustness ablate --strategy <id> [--windows 4] [--sync]` | 因子消融：逐个移除评分因子与过滤条件 |
| `robustness pool --strategy <id> [--windows 4] [--samples 8] [--sync]` | 资产池扰动：随机 80% 子池 / 剔除常持 / 剔除后上市 |
| `robustness collect <id> [--wait] [--timeout 3600]` | 等待并汇总批次（邻域稳定度 / 边际贡献 / 池扰动分布） |
| `robustness stats <id> [--n-trials] [--cost-bps] [--block 20] [--bootstrap 2000]` | CSCV-PBO / Deflated Sharpe / 块自助法置信区间 |
| `robustness show <id>` / `robustness list [--limit]` | 批次详情 / 列表 |

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
- 分段原则：分段用于控制单任务风险、资源峰值、失败局部重跑和跨市场阶段比较。总跨度 > 5 年时必须按 1-2 年逐段 `backtest run`（或按段分别建 optimization 会话）；较短区间在指数/候选较多时也优先按 1-2 年分段或分批执行。
- 异步并行：`backtest run --async` 与 `optimization evaluate --async` 只把任务写入 `background_job`，由服务端 uvicorn 多 worker 进程经 `FOR UPDATE SKIP LOCKED` 并行认领执行，互不重复；实际并行度 = min(worker 数, CPU 核心数)。多方向/多段并行时用 `--async`，入队后 CLI 进程可退出，任务继续在服务端执行。
- `fold_summary`：每个指标输出基线/候选的均值、中位数、候选胜出折数。
- 验收清单默认阈值（共 7 项）：验证窗平均夏普 Δ≥0；平均最大回撤劣化 ≤ 2pct；夏普胜出折数 ≥ 50%；验证窗平均累计收益 Δ≥0；净成本口径夏普 Δ≥0；参数邻域无方向反转（需先跑 `robustness scan`）；分段一致性（剔除最好折后候选夏普不低于基线）。`--strict` 时 accept 必须全部满足。

## 回测快照与会话

- `backtest_run` 创建时写入 `config_snapshot`（元数据 + config_json）与 `config_hash`（sha256）；执行时优先用快照重建配置，保证结果可复现，旧行无快照时回退实时配置。
- `strategy_optimization` 表保存会话级审计：基线/候选 ID 与哈希、回测 ID、逐折指标、聚合统计、报告全文与 accept/reject 结论。

## 环境注意

- 数据库在远程 PostgreSQL（见 `.env` 的 DATABASE_URL）；沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。
- 终端中文乱码是控制台代码页问题，不影响落库数据（UTF-8）。
- 同步执行使用 `status --wait`，异步执行按 worker 数分批；单次回测通常仍控制在 1-2 年，长跨度按段执行，便于比较、重试和控制资源。
