/**
 * 枚举值 → 中文标签的统一映射。
 *
 * 各页面不再自行维护零散、不完整的映射表；后端新增枚举值时只需在此补充。
 * 未登记的值原样返回，便于发现遗漏。
 */

/** 按映射表取中文标签；空值返回 '—'，未登记值原样返回。 */
function lookup(map: Record<string, string>, value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return map[value] ?? value
}

/** 运行记录类型（含已下线类型，供历史记录展示） */
export const RUN_TYPE_LABELS: Record<string, string> = {
  data_sync_all: '全局数据同步',
  data_manage_operation: '数据维护',
  strategy_run: '策略运行',
  ai_analysis: 'AI 舆情分析',
  // 以下类型已下线，仅用于展示历史运行记录
  factor_computation: '因子计算（已下线）',
  industry_universe_refresh: '更新行业信息（已下线）',
  industry_bars_refresh: '行业日线刷新（已下线）',
  industry_quality_check: '行业质量检查（已下线）',
  industry_data_fill: '行业日线补全（已下线）',
  industry_data_rebuild: '行业全量重拉（已下线）',
  stock_quality_check: '个股质量检查（已下线）',
  stock_data_fill: '个股日线补全（已下线）',
  stock_data_rebuild: '个股全量重拉（已下线）',
  daily_ingest: '日频入库（已下线）',
  cold_start: '历史回补（已下线）',
  index_refresh: '指数数据刷新（已下线）',
  macro_refresh: '宏观数据刷新（已下线）',
  index_rebuild: '指数全量重拉（已下线）',
  index_incremental_fill: '指数增量补数（已下线）',
  industry_ingest: '行业数据刷新（已下线）',
}

/** 后端支持重试的运行类型（与 api/routers/runs.py 的 _enqueue_for_run 保持一致） */
export const RETRYABLE_RUN_TYPES: ReadonlySet<string> = new Set([
  'strategy_run',
  'ai_analysis',
  'data_sync_all',
  'data_manage_operation',
])

/** 运行记录 / 子项状态 */
export const RUN_STATUS_LABELS: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  success: '成功',
  partial_success: '部分成功',
  skipped: '已跳过',
  failed: '失败',
  cancelled: '已取消',
  completed: '完成',
}

/** 后台队列任务类型 */
export const JOB_TYPE_LABELS: Record<string, string> = {
  strategy_run: '策略运行',
  ai_analysis: 'AI 舆情分析',
  backtest: '回测',
  comparison: '策略对比',
  data_fill: '数据补齐',
  data_manage_operation: '数据维护',
  data_sync_all: '全局数据同步',
  lifecycle_refresh_all: '生命周期定时体检',
  warm_calendar: '交易日历预热',
}

/** 回测 / 对比状态 */
export const BACKTEST_STATUS_LABELS: Record<string, string> = {
  pending: '待执行',
  running: '执行中',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
  partial: '部分成功',
}

/** 回测用途 */
export const PURPOSE_LABELS: Record<string, string> = {
  research: '研究期',
  validation: '验证期',
  monitor: '上线监控',
}

/** 执行模型 */
export const EXECUTION_MODEL_LABELS: Record<string, string> = {
  t_plus_1_open: 'T+1 开盘',
  t_plus_1_close: 'T+1 收盘',
}

/** 记账口径 */
export const ACCOUNTING_MODEL_LABELS: Record<string, string> = {
  drift_v1: '持仓漂移（drift_v1）',
  constant_mix_v0: '恒定权重（历史口径）',
}

/** 策略配置状态 */
export const STRATEGY_STATUS_LABELS: Record<string, string> = {
  active: '启用',
  draft: '草稿',
  disabled: '禁用',
}

/** 策略标注频率（元数据，不控制调仓） */
export const FREQUENCY_LABELS: Record<string, string> = {
  daily: '日频',
  weekly: '周频',
  monthly: '月频',
}

/** 生命周期状态 */
export const LIFECYCLE_STATUS_LABELS: Record<string, string> = {
  LIVE: '上线中',
  RETIRED: '已退役',
}

/** 研究台账试验来源 */
export const TRIAL_KIND_LABELS: Record<string, string> = {
  research_batch: '研究批量评估',
  manual: '人工 / 补录',
  optimization: '优化会话',
  robustness: '稳健性批次',
  diagnostic: '派生因子诊断',
}

/** 因子模板分类 */
export const FACTOR_CATEGORY_LABELS: Record<string, string> = {
  volume: '量能',
  momentum: '动量',
  volatility: '波动率',
  valuation: '估值',
  technical: '技术',
  macro: '宏观',
  market: '市场',
  constituent: '成分股',
  derived: '派生',
  price: '价格',
  relative_strength: '相对强度',
  breadth: '市场宽度',
}

/** 择时状态 */
export const REGIME_LABELS: Record<string, string> = {
  offensive: '进攻',
  neutral: '中性',
  defensive: '防守',
}

/** 研究数据瘦身的保护原因 */
export const SLIM_PROTECT_LABELS: Record<string, string> = {
  unfinished: '未结束（排队 / 运行中）',
  non_research: '验证 / 监控用途',
  lifecycle: '生命周期引用',
  pending_decision: '优化会话待人工决策',
  latest_research: '策略最新一条研究回测',
}

export const runTypeLabel = (v: string | null | undefined): string => lookup(RUN_TYPE_LABELS, v)
export const runStatusLabel = (v: string | null | undefined): string => lookup(RUN_STATUS_LABELS, v)
export const jobTypeLabel = (v: string | null | undefined): string => lookup(JOB_TYPE_LABELS, v)
export const backtestStatusLabel = (v: string | null | undefined): string => lookup(BACKTEST_STATUS_LABELS, v)
export const purposeLabel = (v: string | null | undefined): string => lookup(PURPOSE_LABELS, v)
export const executionModelLabel = (v: string | null | undefined): string => lookup(EXECUTION_MODEL_LABELS, v)
export const accountingModelLabel = (v: string | null | undefined): string => lookup(ACCOUNTING_MODEL_LABELS, v)
export const strategyStatusLabel = (v: string | null | undefined): string => lookup(STRATEGY_STATUS_LABELS, v)
export const frequencyLabel = (v: string | null | undefined): string => lookup(FREQUENCY_LABELS, v)
export const lifecycleStatusLabel = (v: string | null | undefined): string => lookup(LIFECYCLE_STATUS_LABELS, v)
export const trialKindLabel = (v: string | null | undefined): string => lookup(TRIAL_KIND_LABELS, v)
export const factorCategoryLabel = (v: string | null | undefined): string => lookup(FACTOR_CATEGORY_LABELS, v)
export const regimeLabel = (v: string | null | undefined): string => lookup(REGIME_LABELS, v)
export const slimProtectLabel = (v: string | null | undefined): string => lookup(SLIM_PROTECT_LABELS, v)
