import { apiClient } from './client'
import type { PaginatedResponse, SignalRow } from '../types/api'

/** 分页获取指定策略最新交易日的指数信号，按得分降序 */
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

/** 分页查询某策略在某指数上的历史信号 */
export async function fetchSignalHistory(
  strategyId: string,
  indexCode: string,
  offset = 0,
  limit = 50,
): Promise<PaginatedResponse<SignalRow>> {
  const { data } = await apiClient.get<PaginatedResponse<SignalRow>>('/signals/history', {
    params: { strategy_id: strategyId, index_code: indexCode, offset, limit },
  })
  return data
}
