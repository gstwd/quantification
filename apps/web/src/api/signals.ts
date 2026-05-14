import { apiClient } from './client'
import type { SignalRow } from '../types/api'

export async function fetchLatestSignals(strategyId: string): Promise<SignalRow[]> {
  const { data } = await apiClient.get<SignalRow[]>('/signals/latest', { params: { strategy_id: strategyId } })
  return data
}
