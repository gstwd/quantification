# Strategy contract examples

策略配置采用 JSON 格式，存储在 `strategy_config` 表的 `config_json` 字段中。策略引擎读取配置后按管线执行。

## 配置结构

```json
{
  "strategy_id": "唯一标识",
  "display_name": "中文名称",
  "version": "1.0.0",
  "description": "策略描述",
  "frequency": "daily | weekly | monthly",
  "timing": { ... },           // 可选：择时配置
  "score": { ... },            // 必填：评分配置
  "filters": { ... },          // 可选：过滤规则
  "rank": { ... },             // 必填：排名配置
  "portfolio": { ... },        // 可选：组合配置（缺失=信号模式）
  "risk": { ... },             // 可选：风控配置
  "rebalance": { ... },        // 可选：调仓频率
  "benchmark": { ... },        // 可选：基准对比
  "transaction_cost": { ... }  // 可选：交易成本
}
```

## 完整示例

详见 `docs/architecture/ETF量化系统策略层架构设计.md` 中的配置示例章节。

## API 端点

- `POST /api/strategies/validate` — 校验配置 JSON 合法性
- `POST /api/strategies` — 创建策略配置
- `GET /api/strategies` — 列出所有策略
- `GET /api/strategies/{id}` — 获取策略详情及完整配置
