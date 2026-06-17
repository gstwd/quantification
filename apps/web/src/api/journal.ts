import { apiClient } from './client'
import type {
  CalendarResponse,
  JournalEntryCreate,
  JournalEntryDetail,
  JournalEntrySummary,
  JournalEntryUpdate,
  ObservationRow,
  ObservationsBatchUpdate,
  IndexSnapshotRow,
  SetTagsRequest,
  TagCreate,
  TagSummary,
  TagUpdate,
  AIAnalysisResponse,
  PaginatedResponse,
} from '../types/api'

/** 获取日历视图数据 */
export async function fetchJournalCalendar(year: number, month?: number): Promise<CalendarResponse> {
  const { data } = await apiClient.get<CalendarResponse>('/journal/calendar', {
    params: { year, month },
  })
  return data
}

/** 分页查询日志列表 */
export async function fetchJournalEntries(params: {
  date_from?: string
  date_to?: string
  tag?: string
  phase?: string
  is_complete?: boolean
  offset?: number
  limit?: number
}): Promise<PaginatedResponse<JournalEntrySummary>> {
  const { data } = await apiClient.get<PaginatedResponse<JournalEntrySummary>>('/journal/entries', { params })
  return data
}

/** 创建日志 */
export async function createJournalEntry(req: JournalEntryCreate): Promise<JournalEntryDetail> {
  const { data } = await apiClient.post<JournalEntryDetail>('/journal/entries', req)
  return data
}

/** 按日期查询日志 */
export async function fetchJournalEntryByDate(tradeDate: string): Promise<JournalEntryDetail> {
  const { data } = await apiClient.get<JournalEntryDetail>('/journal/entries/by-date', {
    params: { trade_date: tradeDate },
  })
  return data
}

/** 按 ID 查询日志详情 */
export async function fetchJournalEntry(entryId: string): Promise<JournalEntryDetail> {
  const { data } = await apiClient.get<JournalEntryDetail>(`/journal/entries/${entryId}`)
  return data
}

/** 更新日志 */
export async function updateJournalEntry(entryId: string, req: JournalEntryUpdate): Promise<JournalEntryDetail> {
  const { data } = await apiClient.put<JournalEntryDetail>(`/journal/entries/${entryId}`, req)
  return data
}

/** 删除日志 */
export async function deleteJournalEntry(entryId: string): Promise<void> {
  await apiClient.delete(`/journal/entries/${entryId}`)
}

/** 刷新快照 */
export async function refreshSnapshots(entryId: string): Promise<IndexSnapshotRow[]> {
  const { data } = await apiClient.post<IndexSnapshotRow[]>(`/journal/entries/${entryId}/snapshot/refresh`)
  return data
}

/** 批量保存观察分区 */
export async function saveObservations(entryId: string, req: ObservationsBatchUpdate): Promise<ObservationRow[]> {
  const { data } = await apiClient.put<ObservationRow[]>(`/journal/entries/${entryId}/observations`, req)
  return data
}

/** 获取所有标签 */
export async function fetchTags(): Promise<TagSummary[]> {
  const { data } = await apiClient.get<TagSummary[]>('/journal/tags')
  return data
}

/** 创建标签 */
export async function createTag(req: TagCreate): Promise<TagSummary> {
  const { data } = await apiClient.post<TagSummary>('/journal/tags', req)
  return data
}

/** 更新标签 */
export async function updateTag(tagId: string, req: TagUpdate): Promise<TagSummary> {
  const { data } = await apiClient.put<TagSummary>(`/journal/tags/${tagId}`, req)
  return data
}

/** 删除标签 */
export async function deleteTag(tagId: string): Promise<void> {
  await apiClient.delete(`/journal/tags/${tagId}`)
}

/** 设置日志标签 */
export async function setEntryTags(entryId: string, req: SetTagsRequest): Promise<TagSummary[]> {
  const { data } = await apiClient.put<TagSummary[]>(`/journal/entries/${entryId}/tags`, req)
  return data
}

/** 触发 AI 分析 */
export async function triggerAIAnalysis(entryId: string): Promise<{ status: string }> {
  const { data } = await apiClient.post<{ status: string }>(`/journal/entries/${entryId}/ai-analysis`)
  return data
}

/** 获取 AI 分析结果 */
export async function fetchAIAnalysis(entryId: string): Promise<AIAnalysisResponse> {
  const { data } = await apiClient.get<AIAnalysisResponse>(`/journal/entries/${entryId}/ai-analysis`)
  return data
}
