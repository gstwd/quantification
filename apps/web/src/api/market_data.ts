import { apiClient } from './client'
import type { BenchmarkIndex, DailyBar, IndexValuation, MacroIndicator } from '../types/api'

/** 获取所有基准指数列表 */
export async function fetchBenchmarkIndexes(): Promise<BenchmarkIndex[]> {
  const { data } = await apiClient.get<BenchmarkIndex[]>('/market-data/indexes')
  return data
}

/** 指数日线行情 */
export async function fetchIndexDailyBars(
  indexCode: string,
  limit = 60,
): Promise<DailyBar[]> {
  const { data } = await apiClient.get<DailyBar[]>(
    `/market-data/indexes/${indexCode}/daily-bars`,
    { params: { limit } },
  )
  return data
}

/** 指数 PE/PB 估值历史 */
export async function fetchIndexValuation(
  indexCode: string,
  limit = 60,
): Promise<IndexValuation[]> {
  const { data } = await apiClient.get<IndexValuation[]>(
    `/market-data/indexes/${indexCode}/valuation`,
    { params: { limit } },
  )
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
