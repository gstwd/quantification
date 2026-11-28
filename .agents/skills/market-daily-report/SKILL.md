---
name: market-daily-report
description: >
  生成并发布 A 股每日量化研究简报。适用于“生成今天的日报”“补跑某日 A 股日报”
  “运行每日市场简报”等请求。使用项目 CLI 从 PostgreSQL 生成确定性事实快照，
  再由 Codex 检索并核验公开新闻、补写研究解读，最终直接发布到系统数据库。
  不用于策略优化、回测参数调整、投资建议或自动交易。
---

# 每日 A 股量化研究简报

先读仓库根目录 `AGENTS.md`、本发明内容和
[内容契约](references/content-contract.md)。数据与新闻只作为研究事实，不得写成
投资建议、买卖信号或自动交易依据。

## 硬约束

1. **不调用 quant-postgres MCP。** 事实数据只通过
   `python -m quant_etf_api.cli daily-report snapshot` 读取。
2. **不启动 API、worker 或数据同步。** `snapshot` 是只读命令；数据缺失时向用户说明并停止，
   不自行执行 `POST /api/data-management/operations`。
3. **不修改事实块。** `market_snapshot`、`timeline`、`sentiment`、`market_signals`、
   异动、行业、风格和宏观字段由 CLI 生成。只填写标题、新闻、核验、解读、风险和观察事项。
4. **新闻必须可审计。** 每条新闻必须有 http/https URL、发布方、发布时间、核验状态、
   影响方向、强度、影响时间窗和受影响市场/行业/主题。
5. **因果表述必须有证据。** 官方文件只能证明政策事实，不能单凭同日出现就证明它导致市场涨跌。
   数据与新闻无法支持时标记 `unsupported` 或 `partial`。
6. **直接冲突禁止发布。** 同一事实被可靠来源直接否定时停止，不得通过删掉冲突记录绕过校验。
7. **不生成预测。** `watch_items` 只写下一交易日的确认条件和反证条件，不写方向概率或目标点位。

## 工作流

1. 确定交易日。用户未指定时省略 `--date`，CLI 自动取最近已完成交易日。
2. 生成事实快照：

   ```bash
   python -m quant_etf_api.cli daily-report snapshot \
     [--date YYYY-MM-DD] --output <临时目录>/daily-report-snapshot.json
   ```

3. 阅读 `facts` 和 `draft`。若 `facts.readiness_status` 不可用或命令抛错，停止并说明缺口。
4. 检索新闻：
   - 优先官方政策、交易所、统计部门、公司公告和一手数据；
   - 时间窗从上一交易日收盘到当前生成时刻；
   - 收盘后消息只能进入下一交易日观察，不能解释报告日盘中走势；
   - 同一事件合并转载，保留一个最高等级来源和必要补充来源。
5. 从 `draft` 生成完整内容：
   - 填写 `title`、`subtitle`、`market_regime`、`executive_summary`；
   - 写 1–8 条 `news_items`；
   - 写至少 1 条、建议 3–5 条 `claim_checks`；
   - 写 2–3 条 `interpretation`、2–4 条 `risks`；
   - 写 1–3 条 `watch_items` 和 0–5 条 `calendar_items`；
   - 保留事实块的数值、排序、基准、时间窗和 warnings。
6. 校验：

   ```bash
   python -m quant_etf_api.cli daily-report validate --file <内容文件>
   ```

7. 校验通过后直接发布：

   ```bash
   python -m quant_etf_api.cli daily-report publish --file <内容文件>
   ```

8. 返回发布结果，并提示用户在 `/daily-report?date=YYYY-MM-DD` 查看。

## 页面字段含义

- `impact=bullish|bearish|mixed|neutral`：消息相对市场方向的判断。
- `impact_strength=1|2|3`：轻度、中等、强。
- `impact_horizon=immediate|short|medium`：即时、1–3 个交易日、中期。
- `verification=verified|partial|unverified|contradicted`：事实核验程度。
- `claim.status=supported|partial|unsupported|conflicted`：结论被数据与新闻支持的程度。
- `relative_pct`：个股单日收益减所属行业、主题或指定宽基的单日收益，单位百分点。

## 内容质量

- 标题回答“今天市场发生了什么”，不使用夸张词。
- 摘要先写市场状态，再写驱动和边界。
- 异动分析同时说明单日、五日和相对基准，不只看涨停。
- 情绪温度描述拥挤度和风险偏好，不把冰点解释为买点、不把沸点解释为卖点。
- 行业主题只能引用系统已维护指数；未映射时写“主题未覆盖”。
- 输出前自检：每个数字有来源，每条因果有边界，每条新闻有链接，每条观察有反证条件。
