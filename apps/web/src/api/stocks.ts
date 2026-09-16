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

// 说明：个股的质量检查/补全/重拉已统一走数据管理页
// （POST /data-management/operations，dataset=stock_daily_close|stock_daily_basic|stock_moneyflow），
// 原 `/runs/stocks/{code}/quality|fill|rebuild` 触发函数已删除。
