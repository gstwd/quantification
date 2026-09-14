import { apiClient } from './client'
import type {
  BacktestCancelResponse,
  BacktestCreateRequest,
  BacktestDeleteResponse,
  BacktestDetail,
  BacktestDailyResult,
  BacktestIndexResult,
  BacktestSummary,
  ComparisonCreateRequest,
  ComparisonDetail,
  ComparisonDailyResponse,
  ComparisonSummary,
  DanglingReferenceReport,
  PaginatedResponse,
  QueueStatsResponse,
} from '../types/api'

/** 创建回测任务，后端立即返回 pending 状态并在后台执行 */
export async function createBacktest(req: BacktestCreateRequest): Promise<BacktestSummary> {
  const { data } = await apiClient.post<BacktestSummary>('/backtests', req)
  return data
}

/** 回测列表筛选条件 */
export interface BacktestListFilters {
  strategyId?: string
  status?: string
  purpose?: string
  createdFrom?: string
  createdTo?: string
  /** 调仓日历来源过滤（C1）：upstream / database / not_required */
  calendarSource?: string
}

/** 分页获取回测列表（支持状态/用途/时间范围/日历来源筛选） */
export async function fetchBacktests(
  offset = 0,
  limit = 50,
  filters?: BacktestListFilters,
): Promise<PaginatedResponse<BacktestSummary>> {
  const { data } = await apiClient.get<PaginatedResponse<BacktestSummary>>('/backtests', {
    params: {
      offset,
      limit,
      strategy_id: filters?.strategyId || undefined,
      status: filters?.status || undefined,
      purpose: filters?.purpose || undefined,
      created_from: filters?.createdFrom || undefined,
      created_to: filters?.createdTo || undefined,
      calendar_source: filters?.calendarSource || undefined,
    },
  })
  return data
}

/** 请求取消回测（未开始直接取消，运行中在安全检查点退出） */
export async function cancelBacktest(backtestId: string): Promise<BacktestCancelResponse> {
  const { data } = await apiClient.post<BacktestCancelResponse>(
    `/backtests/${backtestId}/cancel`,
  )
  return data
}

/** 获取后台任务队列统计（积压 / 吞吐 / 运行中任务 / 并发预算） */
export async function fetchQueueStats(windowHours = 1): Promise<QueueStatsResponse> {
  const { data } = await apiClient.get<QueueStatsResponse>('/queue/stats', {
    params: { window_hours: windowHours },
  })
  return data
}

/** 获取回测详情（含配置、口径指纹、多档成本与候选池时间线）
 *
 * costBps 可覆盖净口径主成本档位（C3）：多档成本仍并列返回在 stability.cost_ladder。
 */
export async function fetchBacktest(
  backtestId: string,
  costBps?: number | null,
): Promise<BacktestDetail> {
  const { data } = await apiClient.get<BacktestDetail>(`/backtests/${backtestId}`, {
    params: costBps === null || costBps === undefined ? {} : { cost_bps: costBps },
  })
  return data
}

/** 删除回测记录（C5）；存在 JSONB 引用时需 force=true */
export async function deleteBacktest(
  backtestId: string,
  force = false,
): Promise<BacktestDeleteResponse> {
  const { data } = await apiClient.delete<BacktestDeleteResponse>(
    `/backtests/${backtestId}`,
    { params: { force } },
  )
  return data
}

/** 审计指向已删除回测的悬挂引用（C5） */
export async function fetchBacktestOrphans(limit = 200): Promise<DanglingReferenceReport> {
  const { data } = await apiClient.get<DanglingReferenceReport>('/backtests/orphans', {
    params: { limit },
  })
  return data
}

/** 获取回测每日组合绩效（用于权益曲线和回撤图） */
export async function fetchBacktestDaily(backtestId: string): Promise<BacktestDailyResult[]> {
  const { data } = await apiClient.get<BacktestDailyResult[]>(`/backtests/${backtestId}/daily`)
  return data
}

/** 获取回测每日每指数信号与收益，可按指数代码过滤 */
export async function fetchBacktestIndexResults(
  backtestId: string,
  indexCode?: string,
): Promise<BacktestIndexResult[]> {
  const { data } = await apiClient.get<BacktestIndexResult[]>(
    `/backtests/${backtestId}/index-results`,
    { params: indexCode ? { index_code: indexCode } : {} },
  )
  return data
}

// ── 策略对比回测 API ──────────────────────────────────────────────

/** 创建策略对比回测，后端立即返回 pending 状态并在后台并行执行两个子回测 */
export async function createComparison(
  req: ComparisonCreateRequest,
): Promise<ComparisonSummary> {
  const { data } = await apiClient.post<ComparisonSummary>(
    '/backtests/comparisons',
    req,
  )
  return data
}

/** 分页获取对比回测列表 */
export async function fetchComparisons(
  offset = 0,
  limit = 50,
): Promise<PaginatedResponse<ComparisonSummary>> {
  const { data } = await apiClient.get<PaginatedResponse<ComparisonSummary>>(
    '/backtests/comparisons',
    { params: { offset, limit } },
  )
  return data
}

/** 获取对比回测详情（含两个子回测信息和对比指标） */
export async function fetchComparison(
  comparisonId: string,
): Promise<ComparisonDetail> {
  const { data } = await apiClient.get<ComparisonDetail>(
    `/backtests/comparisons/${comparisonId}`,
  )
  return data
}

/** 获取对比回测的两个策略每日收益（用于叠加图表） */
export async function fetchComparisonDaily(
  comparisonId: string,
): Promise<ComparisonDailyResponse> {
  const { data } = await apiClient.get<ComparisonDailyResponse>(
    `/backtests/comparisons/${comparisonId}/daily`,
  )
  return data
}
