import { apiClient } from './client'
import type {
  BacktestCreateRequest,
  BacktestDetail,
  BacktestDailyResult,
  BacktestIndexResult,
  BacktestSummary,
  PaginatedResponse,
} from '../types/api'

/** 创建回测任务，后端立即返回 pending 状态并在后台执行 */
export async function createBacktest(req: BacktestCreateRequest): Promise<BacktestSummary> {
  const { data } = await apiClient.post<BacktestSummary>('/backtests', req)
  return data
}

/** 分页获取回测列表 */
export async function fetchBacktests(
  offset = 0,
  limit = 50,
): Promise<PaginatedResponse<BacktestSummary>> {
  const { data } = await apiClient.get<PaginatedResponse<BacktestSummary>>('/backtests', {
    params: { offset, limit },
  })
  return data
}

/** 获取回测详情（含配置和汇总指标） */
export async function fetchBacktest(backtestId: string): Promise<BacktestDetail> {
  const { data } = await apiClient.get<BacktestDetail>(`/backtests/${backtestId}`)
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
