import { apiClient } from './client'
import type { DailyReportDetail, DailyReportListResponse } from '../types/api'

/**
 * 获取已发布日报摘要列表。
 *
 * @param limit - 最大返回条数
 * @returns 按日期倒序排列的日报摘要
 */
export async function fetchDailyReports(limit = 30): Promise<DailyReportListResponse> {
  const { data } = await apiClient.get<DailyReportListResponse>('/daily-reports', {
    params: { limit },
  })
  return data
}

/**
 * 获取指定交易日日报详情。
 *
 * @param reportDate - YYYY-MM-DD 格式交易日
 * @param revision - 可选历史修订号
 * @returns 日报详情
 */
export async function fetchDailyReport(
  reportDate: string,
  revision?: number,
): Promise<DailyReportDetail> {
  const { data } = await apiClient.get<DailyReportDetail>(`/daily-reports/${reportDate}`, {
    params: revision == null ? undefined : { revision },
  })
  return data
}
