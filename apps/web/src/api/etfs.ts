import { apiClient } from './client'
import type { EtfDetail } from '../types/api'

export async function fetchEtfs(): Promise<EtfDetail[]> {
  const { data } = await apiClient.get<EtfDetail[]>('/etfs')
  return data
}

export async function fetchEtfDetail(etfCode: string): Promise<EtfDetail> {
  const { data } = await apiClient.get<EtfDetail>(`/etfs/${etfCode}`)
  return data
}
