# 日报内容契约

CLI `snapshot` 输出：

```json
{
  "schema_version": "daily_report_snapshot_v1",
  "fact_snapshot_hash": "...",
  "facts": {},
  "draft": {}
}
```

发布文件必须符合 `DailyReportContent`：

- 顶层：`schema_version`、`report_date`、`next_trade_date`、`readiness_status`、
  `title`、`subtitle`、`generated_at`、`data_cutoff`、`executive_summary`、`market_regime`。
- 事实块：`market_snapshot`、`timeline`、`sentiment`、`market_signals`、
  `stock_anomalies`、`index_anomalies`、`structure_basis`、`industry_leaders`、
  `industry_laggards`、`style_pairs`、`macro_items`、`warnings`。
- 编辑块：`news_items`、`claim_checks`、`interpretation`、`risks`、
  `watch_items`、`calendar_items`。

新闻项字段：

```json
{
  "id": "news-20261127-state-council",
  "category": "资本市场",
  "title": "...",
  "publisher": "中国政府网 / 新华社",
  "source_tier": "official",
  "published_at": "2026-11-27",
  "url": "https://...",
  "verification": "verified",
  "verification_note": "...",
  "excerpt": "...",
  "impact": "bullish",
  "impact_strength": 3,
  "impact_horizon": "immediate",
  "affected_sectors": ["A股整体", "券商"]
}
```

观察项字段：

```json
{
  "key": "breadth-follow-through",
  "label": "放量扩散能否延续",
  "current_state": "上涨占比 96.28%，成交额 2.00 万亿元",
  "confirm_condition": "成交额高于 1.6 万亿元且上涨占比保持 55% 以上",
  "invalidate_condition": "成交额跌破 1.3 万亿元且上涨占比低于 45%",
  "affected_sectors": ["A股整体", "小盘成长"]
}
```

事件日期使用北京时间业务日期。时间戳使用带时区的 ISO 8601。
