import { apiClient } from './client'
import type { AlertListResponse } from '../types/api'

/**
 * 获取站内告警列表与未读数。
 *
 * @param unreadOnly 只返回未读
 * @param limit 条数上限
 */
export async function fetchAlerts(unreadOnly = false, limit = 30): Promise<AlertListResponse> {
  const { data } = await apiClient.get<AlertListResponse>('/alerts', { params: { unread_only: unreadOnly, limit } })
  return data
}

/** 单条标记已读。 */
export async function markAlertRead(alertId: number): Promise<void> {
  await apiClient.post(`/alerts/${alertId}/read`)
}

/** 全部标记已读。 */
export async function markAllAlertsRead(): Promise<void> {
  await apiClient.post('/alerts/read-all')
}
