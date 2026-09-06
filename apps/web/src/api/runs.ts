import { apiClient } from './client'
import type {
  DataQualityResponse,
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

export async function fetchDataQuality(): Promise<DataQualityResponse> {
  const { data } = await apiClient.get<DataQualityResponse>('/system/data-quality')
  return data
}

export async function triggerDailyIngest(): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>('/runs/daily-ingest')
  return data
}

export async function triggerIndexRefresh(): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>('/runs/index-refresh')
  return data
}

export async function triggerMacroRefresh(): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>('/runs/macro-refresh')
  return data
}

export async function triggerColdStart(): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>('/runs/cold-start')
  return data
}

/** 触发单指数全量覆盖重拉（删除旧历史数据后重新拉取全量） */
export async function triggerIndexRebuild(indexCode: string): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>(`/runs/indexes/${indexCode}/rebuild`)
  return data
}

/** 触发单指数增量补数据（从数据库最新交易日补充到当天） */
export async function triggerIndexIncrementalFill(indexCode: string): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>(`/runs/indexes/${indexCode}/incremental-fill`)
  return data
}

export async function triggerStrategyRun(strategyId: string): Promise<{ run_id: string }> {
  const { data } = await apiClient.post<{ run_id: string }>(`/runs/strategies/${strategyId}/run`)
  return data
}

/** 后台行业任务触发结果 */
export interface IndustryRunAccepted {
  status: string
  run_type: string
  run_id: string
}

/** 触发行业目录与成分事件强制刷新（更新行业信息） */
export async function triggerIndustryUniverseRefresh(): Promise<IndustryRunAccepted> {
  const { data } = await apiClient.post<IndustryRunAccepted>('/runs/industry/refresh-info')
  return data
}

/** 触发全部行业日线增量刷新并重算质量快照 */
export async function triggerIndustryBarsRefresh(): Promise<IndustryRunAccepted> {
  const { data } = await apiClient.post<IndustryRunAccepted>('/runs/industry/refresh-bars')
  return data
}

/** 触发单个行业数据质量检查 */
export async function triggerIndustryQualityCheck(
  industryCode: string,
): Promise<IndustryRunAccepted> {
  const { data } = await apiClient.post<IndustryRunAccepted>(
    `/runs/industries/${industryCode}/quality`,
  )
  return data
}

/** 触发单个行业日线补全 */
export async function triggerIndustryFill(industryCode: string): Promise<IndustryRunAccepted> {
  const { data } = await apiClient.post<IndustryRunAccepted>(
    `/runs/industries/${industryCode}/fill`,
  )
  return data
}

/** 触发单个行业全量重拉 */
export async function triggerIndustryRebuild(
  industryCode: string,
): Promise<IndustryRunAccepted> {
  const { data } = await apiClient.post<IndustryRunAccepted>(
    `/runs/industries/${industryCode}/rebuild`,
  )
  return data
}
