# 稳健性验证 CLI 与口径（strategy-optimizer 参考）

> **只跑 CLI**：稳健性验证只需要 CLI + 数据库。**用 `--sync --workers N` 走本地多进程**，
> 不要用默认的入队模式（那需要服务端 worker 消费）。
> `--workers`：缺省 `1`（串行）、`N`=并发 N 条、`0`=先读 `QUANT_ETF_CLI_WORKERS`，没有再按 CPU 与内存
> 自动推导（上限 8）。并发过高会先撞内存与数据库连接预算，6~8 是常见甜点区。
> 并行命令的输出**不要接管道**（会被缓冲到结束），要进度就 `*> run.log` 后 tail。

## 命令

```bash
# 1) 单旋钮邻域扰动：检查参数是否处于平台而非尖峰
#    --preset quick    = 轻量体检（2 窗口 / 8 旋钮 / 18 条回测），写进验收清单第 6 项
#    --preset standard = 完整（4 窗口 / 30 旋钮）
#    --knobs a,b       = 只扫指定的关键旋钮；--knobs-file knobs.json 同上
#    --sync --workers N = 本地多进程并行（回测是 CPU+DB 混合任务，线程会被 GIL 限制）
python -m quant_etf_api.cli robustness scan --strategy <id> [--preset quick|standard] \
    [--windows 4] [--max-knobs 30] [--knobs timing.thresholds.offensive] [--knobs-file k.json] \
    --sync --workers 6 [--execution-model t_plus_1_open]   # 缺省 t_plus_1_close

# 2) 因子消融：逐个移除评分因子与过滤条件，看边际贡献
python -m quant_etf_api.cli robustness ablate --strategy <id> --windows 4 --sync --workers 6

# 3) 资产池扰动：随机 80% 子池 + 剔除常持指数 + 剔除后上市指数
python -m quant_etf_api.cli robustness pool --strategy <id> --windows 4 --samples 6 --sync --workers 6

# 4) 汇总（本地并行执行时批次已是终态，直接 collect 即可；--wait 仅用于入队模式）
python -m quant_etf_api.cli robustness collect <robustness_id>

# 5) 统计显著性：CSCV-PBO / Deflated Sharpe / 块自助法置信区间
python -m quant_etf_api.cli robustness stats <robustness_id> [--n-trials N] [--cost-bps <成本>]

# 6) 清理本批次派生出来的变体草稿策略（默认预演；变体仍被回测引用时需 --force）
python -m quant_etf_api.cli strategy prune-variants --batch <robustness_id> [--apply] [--force]

# 7) 把长期挂着 running 的批次显式收口（回测被删除、进程重启遗留等；证据与试验台账保留）
python -m quant_etf_api.cli robustness abandon <robustness_id> --reason "回测已被删除，不再维护"
```

所有稳健性回测都固定在研究期（2016-01-01 ~ 2025-12-31）内执行，不会消耗验证期数据。

批次创建时行先落库（早于执行），所以执行期就能在 `robustness list` 里看到并取消；
`robustness list/show` 的 `is_stale=true` 表示"还是 running 但长时间没有进展"。
本地并行执行跑完后批次状态即终态；若父进程被中断，用
`backtest batch --pending` 续跑该批次剩余的回测（已 success 的不会重复执行）。

## 扫哪些旋钮

旋钮超过上限时，`scan` 按业务上最容易被拟合的参数优先保留（`services/robustness_service._KNOB_PRIORITY`）：
因子模板参数 `factor_aliases.*` → 择时阈值 → 择时代理 → 其余择时参数与路径依赖风控 / 偏离调仓阈值
（`risk.drawdown_stop|trailing_stop|vol_target`、`rebalance.band`）→ 过滤规则 → 评分权重 → 风险 → 组合 → 排名 → 调仓。
单个旋钮的全部 ±1 档变体 |Δ夏普| ≤ 0.1（`robustness_neighborhood_tolerance`）记为平台，两侧都超过容差记为方向反转。

默认规模与吞吐不匹配时用 `--preset quick`：2 个窗口 × 8 个旋钮 ≈ 18 条回测，
足以回答"参数是平台还是尖峰"；需要写进报告的完整结论再用 `standard`。
本次实际扫描口径随批次落库（`robustness_run.scan_params`），
`robustness show <id>` 与前端"稳健性验证"页签都能看到 preset / knobs / windows。

## 探索路径与验收路径

| 目的 | 命令 | 是否落库 | 能否作为验收依据 |
| --- | --- | --- | --- |
| 快速筛掉没价值的想法（几十个变体） | `research batch --variants v.json` | 否 | **否**（`caliber.persisted=false`） |
| 正式口径、可审计、可复现 | `backtest run` / `robustness scan|ablate|pool` | 是 | 是 |

`research batch` 与平台回测共用同一条执行路径（只关闭落库并按窗口共享行情/因子缓存），
所以口径一致；但"没落库"意味着它不可审计，结论必须回到落库回测复核。

## 变体清理与试验台账

- 稳健性批次会派生 `<基线>__rbXXXX_*` 草稿策略，带 `is_variant` / `source_batch_id` 元数据：
  `strategy prune-variants --batch <批次>` 预演 → `--apply` 删除；
  变体仍被回测引用时默认跳过（列出引用），确认后 `--force` 连带删除回测。
- **不要删除 `robustness_run` 批次行**：`trial_count` 累加值就是 Deflated Sharpe 的 N，
  删掉变体策略 ≠ 抹掉历史试验。
- 只要跑过使用验证期数据的回测，或人工看过验证期结果，策略上会留下
  `validation_consumed_at`（`cli strategy consume-validation <策略> --note "..."` 可人工补记）；
  被标记后该策略的验证期证据只能用于**否决**，不能作为通过依据。

## 结果怎么读

| 字段 | 位置 | 判读 |
| --- | --- | --- |
| `summary.coverage.common_windows` | 所有类型 | 汇总实际使用的窗口（所有变体都有数据的交集）；`comparable=false` 时**不要读 delta** |
| `summary.neighborhood.is_plateau` | scan | true = 所有扰动都在容差内（平台）；false 且 `reversal` = 参数脆弱；**null = 没有有效变体，不等于通过** |
| `summary.neighborhood.worse_ratio` | scan | 劣于基线的变体占比；接近 1 说明当前参数只是局部幸运 |
| `summary.marginal` | ablate | 按 Δ夏普排序的因子边际贡献；接近 0 的因子应考虑删除 |
| `summary.pool.delta_median` | pool | 子池扰动的中位 Δ夏普；接近 0 说明结果不依赖特定成分 |
| `variants[].windows` / `windows_available` | 所有类型 | 参与比较的窗口数 / 该变体自己跑成功的窗口数；两者不等说明该变体有缺窗 |
| `statistics.pbo.value` | stats | CSCV-PBO，约 0.5 相当于纯噪声；越高越可疑；**null = 窗口数不足（<4）**，看 `pbo.reason` |
| `statistics.pbo.power_note` | stats | 对称切分 < 10 时的分辨率提示；PBO 取值只有粗粒度，别把 0.17 与 0.33 的差异当真 |
| `statistics.deflated_sharpe.deflated_sharpe` | stats | 计入试验次数后的显著性概率；< 0.5 基本可以认为不显著 |
| `statistics.n_trials_breakdown` | stats | N 的来源拆分（`robustness` 变体数 + `optimization` 已评估会话数 + `research_batch` 探索变体数 + `manual` 人工 / 补录；`estimated` 为其中估算部分；或 `explicit`） |
| `statistics.bootstrap.lower/upper` | stats | 年化循环块自助法置信区间；跨 0 表示无法区分于噪声 |
| `coverage.missing_windows` / `failed_windows` | 所有类型 | "回测已被删除"与"执行失败"分开报；前者说明证据缺失，不是执行问题 |

## 试验次数 N 的取法

`stats` 默认 `--n-trials` 取自台账：**该策略历史所有稳健性批次的变体总数 + 已评估
（`evaluated`/`accepted`/`rejected`）的优化会话数 + `research_trial` 中 `research_batch`
与 `manual` 行的试验次数**，拆分见 `n_trials_breakdown`。`research batch` 每次运行自动记入；
N **按研究线汇总**（基线 + 优化候选 + 稳健性变体 + 手工归入的策略，`n_trials_scope` 给出成员），
在候选上跑 stats 也计入基线名下的探索。平台外的手工对比用 `research trial add` 补记，而不是只在 `stats` 里临时传 `--n-trials`
（临时值不留痕，下一轮会话看不到）。多重检验的严重程度只会被低估，不会被高估。

## 验收清单第 6 项的证据匹配

`optimization finish` 的"参数邻域"项只接受与**当前配置**对应、成功完成、执行口径与会话相同的 scan 批次：
批次跑在基线策略上且 `robustness_run.baseline_config_hash` 等于会话的基线哈希，或批次的哈希等于会话的候选哈希。
策略 ID 相同而配置哈希不同的批次不算——没有匹配批次时直接判不通过。

## 写进优化报告的要求

优化报告必须包含：

1. 毛口径与净口径（缺省 `default_cost_bps` = 0.5bp，按单边换手折算的往返成本；成本敏感时另给更高档位）两套验证窗指标对比；
2. 参数邻域的 Δ 范围与是否存在方向反转；
3. 消融结果（哪个因子贡献最小）；
4. `stats` 的 PBO 与 Deflated Sharpe 数值，以及本次计入的 N；
5. 明确结论：通过 / 不通过，以及不通过时下一步改什么假设。
