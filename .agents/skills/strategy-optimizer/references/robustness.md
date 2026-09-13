# 稳健性验证 CLI 与口径（strategy-optimizer 参考）

## 命令

```bash
# 1) 单旋钮邻域扰动：检查参数是否处于平台而非尖峰
python -m quant_etf_api.cli robustness scan --strategy <id> --windows 4 [--max-knobs 30]

# 2) 因子消融：逐个移除评分因子与过滤条件，看边际贡献
python -m quant_etf_api.cli robustness ablate --strategy <id> --windows 4

# 3) 资产池扰动：随机 80% 子池 + 剔除常持指数 + 剔除后上市指数
python -m quant_etf_api.cli robustness pool --strategy <id> --windows 4 --samples 8

# 4) 等待并汇总（默认入队异步执行；--wait 轮询至终态）
python -m quant_etf_api.cli robustness collect <robustness_id> --wait

# 5) 统计显著性：CSCV-PBO / Deflated Sharpe / 块自助法置信区间
python -m quant_etf_api.cli robustness stats <robustness_id> [--n-trials N] [--cost-bps 10]
```

所有稳健性回测都固定在研究期（2016-01-01 ~ 2025-12-31）内执行，不会消耗验证期数据。

## 结果怎么读

| 字段 | 位置 | 判读 |
| --- | --- | --- |
| `summary.neighborhood.is_plateau` | scan | true = 所有扰动都在容差内（平台）；false 且 `reversal` = 参数脆弱 |
| `summary.neighborhood.worse_ratio` | scan | 劣于基线的变体占比；接近 1 说明当前参数只是局部幸运 |
| `summary.marginal` | ablate | 按 Δ夏普排序的因子边际贡献；接近 0 的因子应考虑删除 |
| `summary.pool.delta_median` | pool | 子池扰动的中位 Δ夏普；接近 0 说明结果不依赖特定成分 |
| `statistics.pbo.value` | stats | CSCV-PBO，约 0.5 相当于纯噪声；越高越可疑 |
| `statistics.deflated_sharpe.deflated_sharpe` | stats | 计入试验次数后的显著性概率；< 0.5 基本可以认为不显著 |
| `statistics.bootstrap.lower/upper` | stats | 年化夏普置信区间；跨 0 表示无法区分于噪声 |

## 试验次数 N 的取法

`stats` 默认 `--n-trials` 取自"该策略历史所有稳健性批次的变体总数"台账。
若同一段时间内还做过多次不经批次的手工对比，应显式 `--n-trials` 传入更大的估计值——
多重检验的严重程度只会被低估，不会被高估。

## 写进优化报告的要求

优化报告必须包含：

1. 毛口径与净口径（默认 10bp 单边）两套验证窗指标对比；
2. 参数邻域的 Δ 范围与是否存在方向反转；
3. 消融结果（哪个因子贡献最小）；
4. `stats` 的 PBO 与 Deflated Sharpe 数值，以及本次计入的 N；
5. 明确结论：通过 / 不通过，以及不通过时下一步改什么假设。
