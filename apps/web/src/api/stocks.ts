import { apiClient } from './client'

import type { PaginatedResponse } from '../types/api'

/** 个股列表摘要（元数据 + 日线质量快照） */
export interface StockSummary {
  stock_code: string
  name_cn: string
  industry_code: string | null
  industry_name: string | null
  ipo_date: string | null
  delist_date: string | null
  is_active: boolean
  data_start_date: string | null
  data_end_date: string | null
  bar_count: number | null
  missing_day_count: number | null
  quality_checked_at: string | null
}

/** 个股列表查询参数 */
export interface StockListQuery {
  offset?: number
  limit?: number
  keyword?: string
  industryCode?: string
  status?: 'active' | 'delisted'
}

/** 后台单股任务触发结果 */
export interface StockRunAccepted {
  status: string
  run_type: string
  run_id: string
}

/**
 * 分页查询个股元数据与质量快照。
 *
 * @param query - 分页/搜索/筛选参数
 * @returns 分页响应
 */
export async function fetchStockSummaries(
  query: StockListQuery = {},
): Promise<PaginatedResponse<StockSummary>> {
  const { data } = await apiClient.get<PaginatedResponse<StockSummary>>(
    '/market-data/stocks',
    {
      params: {
        offset: query.offset ?? 0,
        limit: query.limit ?? 20,
        keyword: query.keyword || undefined,
        industry_code: query.industryCode || undefined,
        status: query.status || undefined,
      },
    },
  )
  return data
}

/** 触发单只股票数据质量检查（重算并落库质量快照） */
export async function triggerStockQualityCheck(stockCode: string): Promise<StockRunAccepted> {
  const { data } = await apiClient.post<StockRunAccepted>(
    `/runs/stocks/${stockCode}/quality`,
  )
  return data
}

/** 触发单只股票日线补全 */
export async function triggerStockFill(stockCode: string): Promise<StockRunAccepted> {
  const { data } = await apiClient.post<StockRunAccepted>(`/runs/stocks/${stockCode}/fill`)
  return data
}

/** 触发单只股票全量重拉 */
export async function triggerStockRebuild(stockCode: string): Promise<StockRunAccepted> {
  const { data } = await apiClient.post<StockRunAccepted>(
    `/runs/stocks/${stockCode}/rebuild`,
  )
  return data
}
