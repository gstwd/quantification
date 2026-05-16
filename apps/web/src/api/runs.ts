import { apiClient } from './client'
import type { ResearchRunSummary, SystemStatusResponse } from '../types/api'

export async function fetchRuns(): Promise<ResearchRunSummary[]> {
  const { data } = await apiClient.get<ResearchRunSummary[]>('/runs')
  return data
}

export async function fetchSystemStatus(): Promise<SystemStatusResponse> {
  const { data } = await apiClient.get<SystemStatusResponse>('/system/status')
  return data
}

export async function triggerUniverseRefresh(): Promise<void> {
  await apiClient.post('/runs/universe-refresh')
}

export async function triggerDailyIngest(): Promise<void> {
  await apiClient.post('/runs/daily-ingest')
}

export async function triggerStrategyRun(strategyId: string): Promise<void> {
  await apiClient.post(`/runs/strategies/${strategyId}/run`)
}
