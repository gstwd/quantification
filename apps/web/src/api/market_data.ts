import { apiClient } from './client'
import type { BenchmarkIndex, DailyBar, DateRange, IndexCreatePayload, IndexValuation, MacroIndicator } from '../types/api'

/** 获取所有基准指数列表 */
export async function fetchBenchmarkIndexes(): Promise<BenchmarkIndex[]> {
  const { data } = await apiClient.get<BenchmarkIndex[]>('/market-data/indexes')
  return data
}

/** 添加基准指数 */
export async function createIndex(payload: IndexCreatePayload): Promise<BenchmarkIndex> {
  const { data } = await apiClient.post<{ index: BenchmarkIndex; message: string }>('/indexes', payload)
  return data.index
}

/** 删除基准指数 */
export async function deleteIndex(indexCode: string): Promise<void> {
  await apiClient.delete(`/indexes/${indexCode}`)
}

/** 指数日线行情（支持日期范围或 limit 模式） */
export async function fetchIndexDailyBars(
  indexCode: string,
  params: { limit?: number; startDate?: string; endDate?: string } = { limit: 60 },
): Promise<DailyBar[]> {
  const { data } = await apiClient.get<DailyBar[]>(
    `/market-data/indexes/${indexCode}/daily-bars`,
    { params: { limit: params.limit, start_date: params.startDate, end_date: params.endDate } },
  )
  return data
}

/** 指数 PE/PB 估值历史（支持日期范围或 limit 模式） */
export async function fetchIndexValuation(
  indexCode: string,
  params: { limit?: number; startDate?: string; endDate?: string } = { limit: 60 },
): Promise<IndexValuation[]> {
  const { data } = await apiClient.get<IndexValuation[]>(
    `/market-data/indexes/${indexCode}/valuation`,
    { params: { limit: params.limit, start_date: params.startDate, end_date: params.endDate } },
  )
  return data
}

/** 获取指数日线数据的日期范围 */
export async function fetchIndexDateRange(indexCode: string): Promise<DateRange> {
  const { data } = await apiClient.get<DateRange>(`/market-data/indexes/${indexCode}/date-range`)
  return data
}

/** 宏观指标数据（cpi / pmi / lpr1y / lpr5y） */
export async function fetchMacroIndicator(
  indicatorCode: string,
  limit = 60,
): Promise<MacroIndicator[]> {
  const { data } = await apiClient.get<MacroIndicator[]>(
    `/market-data/macro/${indicatorCode}`,
    { params: { limit } },
  )
  return data
}
