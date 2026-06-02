import { apiClient } from './client'
import type { DataQualityResponse, PaginatedResponse, ResearchRunSummary, SystemStatusResponse } from '../types/api'

/** 分页获取运行记录 */
export async function fetchRuns(
  offset = 0,
  limit = 50,
): Promise<PaginatedResponse<ResearchRunSummary>> {
  const { data } = await apiClient.get<PaginatedResponse<ResearchRunSummary>>('/runs', {
    params: { offset, limit },
  })
  return data
}

export async function fetchSystemStatus(): Promise<SystemStatusResponse> {
  const { data } = await apiClient.get<SystemStatusResponse>('/system/status')
  return data
}

export async function fetchDataQuality(): Promise<DataQualityResponse> {
  const { data } = await apiClient.get<DataQualityResponse>('/system/data-quality')
  return data
}

export async function triggerUniverseRefresh(): Promise<void> {
  await apiClient.post('/runs/universe-refresh')
}

export async function triggerDailyIngest(): Promise<void> {
  await apiClient.post('/runs/daily-ingest')
}

export async function triggerColdStart(): Promise<void> {
  await apiClient.post('/runs/cold-start')
}

export async function triggerStrategyRun(strategyId: string): Promise<void> {
  await apiClient.post(`/runs/strategies/${strategyId}/run`)
}
