import { apiClient } from './client'
import type { DailyBar, EtfDetail, ShareSnapshot } from '../types/api'

export async function fetchEtfs(): Promise<EtfDetail[]> {
  const { data } = await apiClient.get<EtfDetail[]>('/etfs')
  return data
}

export async function fetchEtfDetail(etfCode: string): Promise<EtfDetail> {
  const { data } = await apiClient.get<EtfDetail>(`/etfs/${etfCode}`)
  return data
}

export async function fetchDailyBars(etfCode: string, limit = 60): Promise<DailyBar[]> {
  const { data } = await apiClient.get<DailyBar[]>(`/market-data/etfs/${etfCode}/daily-bars`, { params: { limit } })
  return data
}

export async function fetchShareHistory(etfCode: string, limit = 30): Promise<ShareSnapshot[]> {
  const { data } = await apiClient.get<ShareSnapshot[]>(`/market-data/etfs/${etfCode}/shares`, { params: { limit } })
  return data
}
