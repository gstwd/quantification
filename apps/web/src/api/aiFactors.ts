/** AI 舆情分析 API 客户端。
 *
 * 提供新闻采集、AI 分析触发、情绪数据查询等端点。
 */
import { apiClient } from './client'
import type { AIAnalysisRunResponse, DailySentimentResponse } from '../types/api'

/** 触发新闻采集（仅采集，不含 AI 分析）。
 *
 * @param platformIds - 可选，限定平台 ID 列表，默认全部
 * @returns 采集运行结果（collected/saved 数量）
 */
export async function triggerCollect(
  platformIds?: string[],
): Promise<AIAnalysisRunResponse> {
  const { data } = await apiClient.post<AIAnalysisRunResponse>(
    '/ai-factors/collect',
    null,
    { params: platformIds ? { platform_ids: platformIds } : {} },
  )
  return data
}

/** 触发完整 AI 分析链路（采集 + AI 分析 + 聚合）。
 *
 * @param targetDate - 可选，目标交易日，默认今天
 * @param marketContext - 可选，市场背景描述（如"央行降准后次日"）
 * @returns 分析运行结果（collected/saved/analyzed/aggregated 数量）
 */
export async function triggerAnalyze(
  targetDate?: string,
  marketContext?: string,
): Promise<AIAnalysisRunResponse> {
  const params: Record<string, string> = {}
  if (targetDate) params.target_date = targetDate
  if (marketContext) params.market_context = marketContext
  const { data } = await apiClient.post<AIAnalysisRunResponse>(
    '/ai-factors/analyze',
    null,
    { params },
  )
  return data
}

/** 查询指定日期的 AI 情绪聚合数据。
 *
 * @param queryDate - 查询日期（YYYY-MM-DD）
 * @param assetTag - 可选，限定资产标签（指数代码或行业名）
 * @returns 情绪聚合数据列表
 */
export async function fetchDailySentiment(
  queryDate: string,
  assetTag?: string,
): Promise<DailySentimentResponse[]> {
  const { data } = await apiClient.get<DailySentimentResponse[]>(
    `/ai-factors/sentiment/${queryDate}`,
    { params: assetTag ? { asset_tag: assetTag } : {} },
  )
  return data
}

/** 查询某指数最近 N 天的情绪摘要。
 *
 * @param indexCode - 指数代码（如 000300）
 * @param days - 回望天数（1-60），默认 7
 * @returns 情绪聚合数据列表（按日期升序）
 */
export async function fetchIndexSummary(
  indexCode: string,
  days = 7,
): Promise<DailySentimentResponse[]> {
  const { data } = await apiClient.get<DailySentimentResponse[]>(
    `/ai-factors/summary/${indexCode}`,
    { params: { days } },
  )
  return data
}

/** 活跃指数选项 */
export interface IndexOption {
  index_code: string
  name_cn: string
}

/** 获取所有活跃指数（供选择器使用）。
 *
 * @returns 活跃指数列表
 */
export async function fetchActiveIndexes(): Promise<IndexOption[]> {
  const { data } = await apiClient.get<IndexOption[]>('/indexes/active')
  return data
}
