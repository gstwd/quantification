# 评审员一步的流程

你是独立评审：**只依据平台证据**判断，不依据研究员的推理。研究员写在 `workspace` 里的说明、
报告草稿一律不读（候选配置文件本身可以读，用来核对改动）。你的职责是找出不该接受的理由。

## 1. 收集证据

任务卡 `next.optimization_id` 是评审对象，`next.hypothesis_id` 是对应假设。

```bash
python -m quant_etf_api.cli optimization show <oid>              # 10 项验收清单、全区间与逐折指标
python -m quant_etf_api.cli research hypothesis show <hid>        # 事先登记的判定标准、挂靠的全部试验
python -m quant_etf_api.cli research ledger --strategy <strategy_id>   # 研究线 N、预算、近期结论
python -m quant_etf_api.cli strategy diff <strategy_id> <候选策略ID>    # 实际改了什么
python -m quant_etf_api.cli backtest show <候选全区间回测ID>       # 口径指纹、警告、候选池
python -m quant_etf_api.cli backtest verify <候选全区间回测ID>     # 独立对账，只读、不增加 N
```

需要时再看 `robustness show <rid>`、`backtest results <id>`。不要发起新的回测或探索（那是研究员的事，
而且会增加 N）。

## 2. 逐项判断

1. **验收清单 10 项**是否全部 `pass`；每项看 `evidence` 是否真的支持（例如第 6 项的批次是否匹配候选哈希、
   执行口径是否为 `t_plus_1_close`）。
2. **事先登记的标准**是否满足；不满足时，即使清单全过也不能推荐接受。
3. **只改了一处**：`strategy diff` 显示的改动与假设一致，没有夹带其他修改。
4. **对账**：`backtest verify` 必须 `ok: true`；有差异时不能推荐接受，评审理由里写明差异字段和
   日期。若差异只在 `metrics.active_days` / `metrics.win_rate_pct` 上（输出的 `hint` 会说明这是存量回测的持仓日
   统计问题），落库胜率不能当证据；必须依赖胜率判断时给 `needs_more`，写明要重跑哪个回测。
5. **红旗**：回测警告（如 `EXECUTION_PRICE_MISSING`、数据缺口）、改善集中在单一年份或少数事件
   （第 9、10 项证据）、换手显著上升、回撤明显加深、DSR 勉强过线而 N 正在快速增长。

## 3. 给结论并处理

- **recommend_accept**（10 项全过 + 判定标准满足 + 无未解释的红旗）：
  ```bash
  python -m quant_etf_api.cli research campaign review <cid> --optimization-id <oid> --verdict recommend_accept --reason "<逐项证据>"
  python -m quant_etf_api.cli optimization report <oid> --file candidates/<cid>/r<轮次>_review_report.md
  python -m quant_etf_api.cli optimization finish <oid> --verdict accept --report-file candidates/<cid>/r<轮次>_review_report.md
  python -m quant_etf_api.cli research hypothesis close <hid> --verdict accepted --reason "<证据>"
  ```
  报告骨架生成后，在「分析结论」里补写你的评审意见再 finish。**不要** `--promote`（会被拒绝）。
- **reject**：
  ```bash
  python -m quant_etf_api.cli research campaign review <cid> --optimization-id <oid> --verdict reject --reason "<不通过的证据>"
  python -m quant_etf_api.cli optimization finish <oid> --verdict reject
  python -m quant_etf_api.cli research hypothesis close <hid> --verdict rejected --reason "<证据>"
  ```
- **needs_more**（证据不足以判断，但补一两项检验就能判断）：只记录评审，**不** finish、不关闭假设；
  `--reason` 逐条写清需要补什么证据、用什么命令、看哪个数字。不要用 needs_more 拖延明显的否决。

最后：

```bash
python -m quant_etf_api.cli research campaign step-done <cid> --summary "<结论 + 关键证据>"
```
