import { apiClient } from './client'
import type { FactorRow, FactorSpec } from '../types/api'

/** 获取所有因子元数据列表 */
export async function fetchFactorSpecs(): Promise<FactorSpec[]> {
  const { data } = await apiClient.get<FactorSpec[]>('/factors/')
  return data
}

/** 查询单因子在单 ETF 上的时间序列 */
export async function fetchFactorTimeSeries(
  factorId: string,
  etfCode: string,
  startDate: string,
  endDate: string,
): Promise<FactorRow[]> {
  const { data } = await apiClient.get<FactorRow[]>(`/factors/${factorId}/values`, {
    params: { etf_code: etfCode, start_date: startDate, end_date: endDate },
  })
  return data
}

/** 查询单因子在指定交易日的全 ETF 横截面 */
export async function fetchFactorCrossSection(
  factorId: string,
  tradeDate: string,
): Promise<FactorRow[]> {
  const { data } = await apiClient.get<FactorRow[]>(`/factors/${factorId}/cross-section`, {
    params: { trade_date: tradeDate },
  })
  return data
}

/** 触发指定交易日的因子计算（后台异步，返回 202） */
export async function triggerFactorCompute(tradeDate: string): Promise<{ message: string }> {
  const { data } = await apiClient.post<{ message: string }>('/factors/compute', {
    trade_date: tradeDate,
  })
  return data
}
