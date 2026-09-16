import { apiClient } from './client'
import type {
  PaginatedResponse,
  ResearchRunDetail,
  ResearchRunItem,
  ResearchRunSummary,
  SystemStatusResponse,
} from '../types/api'

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

/** 获取单条运行记录详情 */
export async function fetchRunDetail(runId: string): Promise<ResearchRunDetail> {
  const { data } = await apiClient.get<ResearchRunDetail>(`/runs/${runId}`)
  return data
}

/** 获取运行的子项明细列表 */
export async function fetchRunItems(runId: string): Promise<ResearchRunItem[]> {
  const { data } = await apiClient.get<ResearchRunItem[]>(`/runs/${runId}/items`)
  return data
}

/** 重试失败的运行记录 */
export async function retryRun(runId: string): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>(`/runs/${runId}/retry`)
  return data
}

export async function fetchSystemStatus(): Promise<SystemStatusResponse> {
  const { data } = await apiClient.get<SystemStatusResponse>('/system/status')
  return data
}

// 数据质量只保留一个口径：数据管理页的 data_health_snapshot
// （见 api/dataManagement.ts 的 fetchDataManagementOverview / fetchDataSetDetail），
// 原 `/system/data-quality` 独立口径已下线。

// 数据同步/补缺口/全量重拉/质量检查统一由 /data-management/operations 提交，
// 指数、宏观、行业与个股均不再保留各自的触发入口（见 api/dataManagement.ts）。

export async function triggerStrategyRun(strategyId: string): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>(`/runs/strategies/${strategyId}/run`)
  return data
}
