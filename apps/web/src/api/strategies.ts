import { apiClient } from './client'
import type { StrategyDetail } from '../types/api'

export async function fetchStrategies(): Promise<StrategyDetail[]> {
  const { data } = await apiClient.get<StrategyDetail[]>('/strategies')
  return data
}

export async function fetchStrategyDetail(strategyId: string): Promise<StrategyDetail> {
  const { data } = await apiClient.get<StrategyDetail>(`/strategies/${strategyId}`)
  return data
}
