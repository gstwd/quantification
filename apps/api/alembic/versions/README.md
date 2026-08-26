# Alembic 迁移版本

> 说明：0001–0025 历史迁移曾创建 ETF 相关表（etf_universe / etf_daily_bar /
> etf_daily_share / etf_factor_value / etf_signal / backtest_etf_result），
> 迁移 `0027_remove_etf` 已全部删除，系统当前不研究 ETF。

## 已应用迁移

| 迁移 | 修订 ID | 说明 |
|------|---------|------|
| 0001 | `0001_initial_schema` | 基础表结构：ETF 宇宙、日线行情、份额、信号、研究运行 |
| 0002 | `0002_add_backtest_tables` | 回测表：backtest_run / backtest_daily_result / backtest_etf_result / backtest_index_result |
| 0003 | `0003_add_index_macro_tables` | 指数日线、指数估值、宏观指标、source_payload_log |
| 0004 | `0004_seed_etf_universe` | ETF 宇宙种子数据 |
| 0005 | `0005_factor_layer` | 因子层：factor_definition / etf_factor_value / index_factor_value |
| 0006 | `0006_factor_definition_enhance` | 因子定义增强字段 |
| 0007 | `0007_index_factor_backtest` | 指数因子回测支持 |
| 0008 | `0008_strategy_config` | 策略配置表 strategy_config |
| 0009 | `0009_backtest_mode_fields` | 回测模式字段增强 |
| 0010 | `0010_index_signal_table` | index_signal 表（与 etf_signal 结构对齐） |
| 0011 | `0011_add_backtest_daily_benchmark_turnover` | 回测日结果增加基准收益和换手率字段 |
| 0012 | `0012_add_backtest_index_original_score` | 回测指数结果增加原始得分字段 |
| 0013 | `0013_trading_calendar_and_fixes` | 交易日历表、基准指数活跃字段、宏观 period_date |
| 0014 | `0014_remove_asset_scope` | 移除资产范围字段 |
| 0015 | `0015_remove_backtest_mode_weighting` | 移除回测模式与加权字段 |
| 0016 | `0016_backtest_progress` | 回测进度列 |
| 0018 | `0018_backtest_comparison` | 策略对比回测表 |
| 0020 | `0020_add_ai_factor_tables` | AI 舆情相关表 |
| 0021 | `0021_add_market_synthesis` | 市场综合研判表 |
| 0022 | `0022_add_keyword_tag_config` | 关键词标签映射表 |
| 0023 | `0023_add_background_job` | 后台任务队列表 |
| 0024 | `0024_backtest_signal_semantics` | 回测信号口径统一 |
| 0025 | `0025_add_backtest_run_warnings` | 回测结构化提示 |
| 0026 | `0026_backtest_daily_missing_bar` | 回测日结果缺数计数 |
| 0027 | `0027_remove_etf` | 删除全部 ETF 表，research_run_item 改 index_code |
| — | `ac0cbcadbda1_add_column_comments` | 为已有表列添加中文注释 |

## 新建迁移

```bash
cd apps/api
alembic revision --autogenerate -m "description"
alembic upgrade head
```

迁移文件自动生成后需人工审查，确保字段类型、约束、默认值正确后再提交。
