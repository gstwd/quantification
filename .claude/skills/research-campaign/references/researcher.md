# 研究员一步的流程

目标：本轮**只检验一个假设**，结束时给出明确回报。检验流程与口径沿用
`.agents/skills/strategy-optimizer/SKILL.md`（第 0~7 步），这里只写战役内的差异。

## 1. 读任务卡，确定本轮做什么

- `next.hypothesis_id` 有值且 `last_review.outcome == "needs_more"`：本轮**继续该假设**，
  只补评审要求的证据（`last_review.summary` 逐条列出），补完后再次请求评审。不要换假设、不要顺手改配置。
- 否则提出新假设。先读：
  - `campaign_hypotheses`：本战役已检验过的假设与结论；
  - `ledger_recent_closed` 与 `python -m quant_etf_api.cli research ledger --strategy <strategy_id>`：研究线上的结论与 N；
  - `limits` / `lineage_budget`：剩余试验次数。剩余很少时只做最有把握的检验。
- 已否决的方向不要换个说法重试；在旧结论上细化时登记子假设（`--parent`）。
- 想不出值得检验、且有依据的新方向时，直接回报 `no_idea`（战役会结束），不要硬凑。

## 2. 先诊断，再登记

诊断命令只读、不计入 N，用来形成假设；但整个战役共用 `limits.max_diagnostics` 次配额
（每条 `factor-ic` / `buckets` / `signal-lab` 计一次，失败也算），本轮通常 2~4 次足够：

```bash
python -m quant_etf_api.cli strategy show <strategy_id>
python -m quant_etf_api.cli research buckets --strategy <strategy_id> --ref <因子别名> --quantiles 5 --horizons 1,5,20 --split-halves --summary
python -m quant_etf_api.cli research factor-ic --factor <模板ID> --forward-days 5 --summary
python -m quant_etf_api.cli research signal-lab timing --index <指数> --signal existing_composite --strategy <strategy_id> --summary
```

想用派生因子（`expr` 模板，如 `dev250 - dev60`）时，先对表达式本身做诊断，否则后面的
`research batch` / `optimization start` 会被拒绝：

```bash
python -m quant_etf_api.cli research buckets --strategy <strategy_id> --factor expr \
    --params '{"expr": "dev250 - dev60"}' --aliases '{"dev60": {"template_id": "ma_deviation", "params": {"period": 60}}}' \
    --index <指数> --quantiles 5 --horizons 1,5,20 --split-halves --summary
```

派生组合的搜索空间远大于调参，诊断不支持就放弃，不要换着组合反复试。

有依据后**先登记再检验**（判定标准写定后不得改）：

```bash
python -m quant_etf_api.cli research hypothesis add --strategy <strategy_id> --category <类别> \
    --statement "<一句话假设>" --criteria "<主要指标与通过阈值>" \
    --rationale "<诊断证据：命令与关键数字>" --execution-models t_plus_1_close --by campaign-researcher
```

## 3. 探索（research batch，计入 N）

变体写到 `workspace` 下（如 `r03_variants.json`），**不超过 4 个**，只围绕本假设：

```bash
python -m quant_etf_api.cli research batch --strategy <strategy_id> --variants candidates/<cid>/r03_variants.json \
    --windows 4 --cost-bps 10 --hypothesis-id <hid> --summary
```

- 没有变体在净口径下稳定优于基线（多数窗口改善、成本阶梯不翻转）→ 关闭假设并回报：
  ```bash
  python -m quant_etf_api.cli research hypothesis close <hid> --verdict rejected --reason "<数据>"
  python -m quant_etf_api.cli research campaign step-done <cid> --outcome rejected --hypothesis-id <hid> --summary "<结论与数字>"
  ```
- 配置表达不了 → `--verdict blocked_by_schema`，回报 `--outcome blocked`，摘要写清缺什么能力。

## 4. 正式评估（有希望时）

1. 从 `strategy show` 拿完整 `config_json`，只改本假设涉及的一处，写成 `workspace/r03_candidate.json`；
   `python -m quant_etf_api.cli strategy validate --file <文件>` 通过后再用。
2. 开会话并评估：
   ```bash
   python -m quant_etf_api.cli optimization start --strategy <strategy_id> --candidate-file <文件> \
       --hypothesis-id <hid> --version <新版本号>
   python -m quant_etf_api.cli optimization evaluate <oid> --folds 4 --workers 0
   ```
3. 邻域证据（验收第 6 项，须与候选配置哈希匹配）：
   ```bash
   python -m quant_etf_api.cli robustness scan --strategy <候选策略ID> --preset quick --sync --workers 0 --hypothesis-id <hid>
   python -m quant_etf_api.cli robustness collect <rid>
   ```
4. `python -m quant_etf_api.cli optimization show <oid>` 看 `acceptance_checklist`（10 项）。
   - 明显失败（多项不过、或第 8 项 DSR 远低于门槛）→ `optimization finish <oid> --verdict reject`，
     关闭假设，回报 `rejected`；
   - 其余情况交给评审：
     ```bash
     python -m quant_etf_api.cli research campaign step-done <cid> --outcome review_requested \
         --optimization-id <oid> --hypothesis-id <hid> --summary "<改了什么、清单哪几项过/不过、关键数字>"
     ```

## 5. 摘要怎么写

下一轮的研究员和评审员只能看到你的摘要与平台数据。写清：假设、做了哪些检验（命令 + 变体数）、
关键数字（净夏普、Δ、胜出窗口/折、DSR 与 N）、结论、遗留问题。不写投资建议。
