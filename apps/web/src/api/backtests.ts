import { apiClient } from './client'
import type {
  BacktestCreateRequest,
  BacktestDetail,
  BacktestDailyResult,
  BacktestEtfResult,
  BacktestSummary,
} from '../types/api'

/** 创建回测任务，后端立即返回 pending 状态并在后台执行 */
export async function createBacktest(req: BacktestCreateRequest): Promise<BacktestSummary> {
  const { data } = await apiClient.post<BacktestSummary>('/backtests', req)
  return data
}

/** 获取回测列表 */
export async function fetchBacktests(limit = 50): Promise<BacktestSummary[]> {
  const { data } = await apiClient.get<BacktestSummary[]>('/backtests', { params: { limit } })
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

/** 获取回测每日每 ETF 信号与收益，可按 ETF 代码过滤 */
export async function fetchBacktestEtfResults(
  backtestId: string,
  etfCode?: string,
): Promise<BacktestEtfResult[]> {
  const { data } = await apiClient.get<BacktestEtfResult[]>(
    `/backtests/${backtestId}/etf-results`,
    { params: etfCode ? { etf_code: etfCode } : {} },
  )
  return data
}
