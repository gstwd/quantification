/** AI 舆情分析 API 客户端。
 *
 * 提供新闻采集、AI 分析触发、情绪数据查询、市场研判等端点。
 */
import { apiClient } from './client'
import type { AIAnalysisRunResponse, DailySentimentResponse, MarketSynthesisResponse, TagNewsItem } from '../types/api'

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
 * @returns 分析运行结果（collected/saved/analyzed/aggregated 数量）
 */
export async function triggerAnalyze(
  targetDate?: string,
): Promise<AIAnalysisRunResponse> {
  const params: Record<string, string> = {}
  if (targetDate) params.target_date = targetDate
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

/** 查询指定日期、指定资产标签下的所有新闻明细。
 *
 * @param date - 交易日（YYYY-MM-DD）
 * @param assetTag - 资产标签（如 "科技"、"军工"）
 * @returns 新闻明细列表（含标题、链接、情绪分等）
 */
export async function fetchSentimentNews(
  date: string,
  assetTag: string,
): Promise<TagNewsItem[]> {
  const { data } = await apiClient.get<TagNewsItem[]>(
    `/ai-factors/sentiment/${date}/news`,
    { params: { asset_tag: assetTag } },
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

/** 查询指定日期的市场综合研判。
 *
 * @param tradeDate - 交易日（YYYY-MM-DD）
 * @returns 市场研判数据，无数据时返回 null
 */
export async function fetchMarketSynthesis(
  tradeDate: string,
): Promise<MarketSynthesisResponse | null> {
  const { data } = await apiClient.get<MarketSynthesisResponse | null>(
    `/ai-factors/synthesis/${tradeDate}`,
  )
  return data
}

/** 查询日期范围内的市场研判列表。
 *
 * @param start - 起始日期（YYYY-MM-DD）
 * @param end - 截止日期（YYYY-MM-DD）
 * @returns 市场研判列表
 */
export async function fetchSynthesisRange(
  start: string,
  end: string,
): Promise<MarketSynthesisResponse[]> {
  const { data } = await apiClient.get<MarketSynthesisResponse[]>(
    '/ai-factors/synthesis-range',
    { params: { start, end } },
  )
  return data
}

/** 获取最新有情绪数据的交易日。
 *
 * @returns 最新交易日（YYYY-MM-DD）或 null
 */
export async function fetchLatestDataDate(): Promise<string | null> {
  const { data } = await apiClient.get<{ trade_date: string | null }>(
    '/ai-factors/latest-data-date',
  )
  return data.trade_date ?? null
}

/** 获取前一交易日日期（基于 A 股交易日历）。
 *
 * 用于定死展示前一交易日 AI 舆情概览，前一天无数据时展示为空。
 *
 * @returns 前一交易日（YYYY-MM-DD）或 null
 */
export async function fetchPreviousTradingDay(): Promise<string | null> {
  const { data } = await apiClient.get<{ trade_date: string | null }>(
    '/ai-factors/previous-trading-day',
  )
  return data.trade_date ?? null
}
