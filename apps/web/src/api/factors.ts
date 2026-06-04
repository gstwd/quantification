import { apiClient } from './client'
import type {
  CorrelationResponse,
  CrossSectionResponse,
  FactorRow,
  FactorSpec,
  FactorUpdatePayload,
  ICResponse,
} from '../types/api'

/** 获取所有因子元数据列表（含已禁用） */
export async function fetchFactorSpecs(): Promise<FactorSpec[]> {
  const { data } = await apiClient.get<FactorSpec[]>('/factors/')
  return data
}

/** 编辑因子元数据 */
export async function updateFactor(
  factorId: string,
  payload: FactorUpdatePayload,
): Promise<FactorSpec> {
  const { data } = await apiClient.patch<FactorSpec>(`/factors/${factorId}`, payload)
  return data
}

/** 查询单因子在指定交易日的横截面（含 ETF 中文名），不传日期时自动选最新 */
export async function fetchFactorCrossSection(
  factorId: string,
  tradeDate?: string,
  forceRecompute = false,
): Promise<CrossSectionResponse> {
  const params: Record<string, string | boolean> = {}
  if (tradeDate) {
    params.trade_date = tradeDate
  }
  if (forceRecompute) {
    params.force_recompute = true
  }
  const { data } = await apiClient.get<CrossSectionResponse>(
    `/factors/${factorId}/cross-section`,
    { params },
  )
  return data
}

/** 查询单因子在单 ETF 上的时间序列（后端自动补算缺失日期） */
export async function fetchFactorTimeSeries(
  factorId: string,
  etfCode: string,
  startDate: string,
  endDate: string,
  forceRecompute = false,
): Promise<FactorRow[]> {
  const params: Record<string, string | boolean> = {
    etf_code: etfCode,
    start_date: startDate,
    end_date: endDate,
  }
  if (forceRecompute) {
    params.force_recompute = true
  }
  const { data } = await apiClient.get<FactorRow[]>(`/factors/${factorId}/values`, { params })
  return data
}

/** 查询因子 IC 分析（IC 时间序列和汇总统计） */
export async function fetchFactorIC(
  factorId: string,
  startDate: string,
  endDate: string,
  forwardDays = 1,
): Promise<ICResponse> {
  const { data } = await apiClient.get<ICResponse>(`/factors/${factorId}/ic`, {
    params: {
      start_date: startDate,
      end_date: endDate,
      forward_days: forwardDays,
    },
  })
  return data
}

/** 查询因子间截面 Rank 相关性矩阵 */
export async function fetchFactorCorrelation(
  tradeDate: string,
  factorIds?: string[],
): Promise<CorrelationResponse> {
  const params: Record<string, string | string[]> = { trade_date: tradeDate }
  if (factorIds && factorIds.length > 0) {
    params.factor_ids = factorIds
  }
  const { data } = await apiClient.get<CorrelationResponse>('/factors/correlation', { params })
  return data
}
