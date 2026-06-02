import { apiClient } from './client'
import type { PaginatedResponse, SignalRow } from '../types/api'

/** 分页获取指定策略最新交易日的 ETF 信号，按得分降序 */
export async function fetchLatestSignals(
  strategyId: string,
  offset = 0,
  limit = 50,
): Promise<PaginatedResponse<SignalRow>> {
  const { data } = await apiClient.get<PaginatedResponse<SignalRow>>('/signals/latest', {
    params: { strategy_id: strategyId, offset, limit },
  })
  return data
}

/** 分页查询某策略在某 ETF 上的历史信号 */
export async function fetchSignalHistory(
  strategyId: string,
  etfCode: string,
  offset = 0,
  limit = 50,
): Promise<PaginatedResponse<SignalRow>> {
  const { data } = await apiClient.get<PaginatedResponse<SignalRow>>('/signals/history', {
    params: { strategy_id: strategyId, etf_code: etfCode, offset, limit },
  })
  return data
}
