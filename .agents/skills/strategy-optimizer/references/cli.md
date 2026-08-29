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
| `backtest run --strategy <id> [--start] [--end] [--universe all\|subset] [--index-codes a,b] [--benchmark 000300] [--no-benchmark] [--async]` | 创建并执行回测；默认同步；`--start/--end` 缺省为今天往前 2 年 |
| `backtest status <id> [--wait] [--timeout 600]` | 状态与指标；`--wait` 轮询至终态（failed 退出码 1，超时 2） |
| `backtest show <id>` | 详情（含 config_snapshot / config_hash / data_cutoff_date / warnings） |
| `backtest results <id> [--daily] [--index]` | 每日组合绩效 / 每指数信号与收益 |

## optimization

| 命令 | 说明 |
| --- | --- |
| `optimization start --strategy <基线> --candidate-file <path> --hypothesis "<假设>" [--start] [--end] [--folds 4] [--candidate-id] [--version]` | 建草稿候选 + 会话；候选 ID 默认 `<基线>__opt_<会话前8位>`；`--version` 建议必传（promote 用） |
| `optimization evaluate <opt_id> [--folds] [--async]` | 全区间 + 逐折回测；同步跑 2+2K 个，K=4 约 40-50 分钟 |
| `optimization report <opt_id> [--file <path>]` | 生成 Markdown 报告骨架 |
| `optimization finish <opt_id> --verdict accept\|reject [--report-file] [--promote] [--strict]` | 结束会话；accept+promote 把候选配置写回基线（strategy_id 不变、version 取候选版本） |
| `optimization show <opt_id>` | 会话详情（含逐折指标与聚合） |
| `optimization list [--strategy] [--limit]` | 会话列表 |

## 评估语义

- 折叠：`[start, end]` 按交易日等分为 K 个连续验证窗，最后一折吸收余数；每折对基线与候选各跑一次回测。
- `fold_summary`：每个指标输出基线/候选的均值、中位数、候选胜出折数。
- 验收清单默认阈值：验证窗平均夏普 Δ≥0；平均最大回撤劣化 ≤ 2pct；夏普胜出折数 ≥ 50%；验证窗平均累计收益 Δ≥0。`--strict` 时 accept 必须全部满足。

## 回测快照与会话

- `backtest_run` 创建时写入 `config_snapshot`（元数据 + config_json）与 `config_hash`（sha256）；执行时优先用快照重建配置，保证结果可复现，旧行无快照时回退实时配置。
- `strategy_optimization` 表保存会话级审计：基线/候选 ID 与哈希、回测 ID、逐折指标、聚合统计、报告全文与 accept/reject 结论。

## 环境注意

- 数据库在远程 PostgreSQL（见 `.env` 的 DATABASE_URL）；沙箱内连库失败（`Permission denied` / `WinError 10013`）时用 require_escalated 重跑同一命令。
- 终端中文乱码是控制台代码页问题，不影响落库数据（UTF-8）。
- 回测耗时：2 年 × ~18 指数约 8 分钟/个；评估 K=4 共 10 个，同步执行请耐心等待。
