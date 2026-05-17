import { apiClient } from './client'
import type { DailyBar, DateRange, EtfCreatePayload, EtfDetail, ShareSnapshot } from '../types/api'

/** 获取所有 ETF 列表 */
export async function fetchEtfs(): Promise<EtfDetail[]> {
  const { data } = await apiClient.get<EtfDetail[]>('/etfs')
  return data
}

/** 获取单个 ETF 详情 */
export async function fetchEtfDetail(etfCode: string): Promise<EtfDetail> {
  const { data } = await apiClient.get<EtfDetail>(`/etfs/${etfCode}`)
  return data
}

/** 添加 ETF */
export async function createEtf(payload: EtfCreatePayload): Promise<EtfDetail> {
  const { data } = await apiClient.post<{ etf: EtfDetail; message: string }>('/etfs', payload)
  return data.etf
}

/** 删除 ETF */
export async function deleteEtf(etfCode: string): Promise<void> {
  await apiClient.delete(`/etfs/${etfCode}`)
}

/** ETF 日线行情（支持日期范围或 limit 模式） */
export async function fetchDailyBars(
  etfCode: string,
  params: { limit?: number; startDate?: string; endDate?: string } = { limit: 60 },
): Promise<DailyBar[]> {
  const { data } = await apiClient.get<DailyBar[]>(
    `/market-data/etfs/${etfCode}/daily-bars`,
    { params: { limit: params.limit, start_date: params.startDate, end_date: params.endDate } },
  )
  return data
}

/** ETF 份额历史（支持日期范围或 limit 模式） */
export async function fetchShareHistory(
  etfCode: string,
  params: { limit?: number; startDate?: string; endDate?: string } = { limit: 30 },
): Promise<ShareSnapshot[]> {
  const { data } = await apiClient.get<ShareSnapshot[]>(
    `/market-data/etfs/${etfCode}/shares`,
    { params: { limit: params.limit, start_date: params.startDate, end_date: params.endDate } },
  )
  return data
}

/** 获取 ETF 日线数据的日期范围 */
export async function fetchEtfDateRange(etfCode: string): Promise<DateRange> {
  const { data } = await apiClient.get<DateRange>(`/market-data/etfs/${etfCode}/date-range`)
  return data
}
