import { apiClient } from './client'
import type {
  CorrelationResponse,
  CrossSectionResponse,
  FactorRow,
  FactorSpec,
  FactorUpdatePayload,
  ICResponse,
} from '../types/api'

/** 模板参数取值：可覆盖参数名 → 数值 */
export type FactorParams = Record<string, number>

/**
 * 把模板参数序列化为查询串取值。
 * 后端以 JSON 对象文本接收 params，空参数返回 undefined 以便省略该查询参数。
 */
function encodeParams(params?: FactorParams): string | undefined {
  if (!params || Object.keys(params).length === 0) return undefined
  return JSON.stringify(params)
}

/** 获取所有因子模板元数据列表（含已禁用） */
export async function fetchFactorSpecs(): Promise<FactorSpec[]> {
  const { data } = await apiClient.get<FactorSpec[]>('/factors/')
  return data
}

/** 编辑因子模板元数据 */
export async function updateFactor(
  factorId: string,
  payload: FactorUpdatePayload,
): Promise<FactorSpec> {
  const { data } = await apiClient.patch<FactorSpec>(`/factors/${factorId}`, payload)
  return data
}

/** 查询因子模板实例在指定交易日的横截面（含指数中文名），不传日期时自动选最新 */
export async function fetchFactorCrossSection(
  factorId: string,
  tradeDate?: string,
  params?: FactorParams,
): Promise<CrossSectionResponse> {
  const query: Record<string, string> = {}
  if (tradeDate) {
    query.trade_date = tradeDate
  }
  const encoded = encodeParams(params)
  if (encoded) {
    query.params = encoded
  }
  const { data } = await apiClient.get<CrossSectionResponse>(
    `/factors/${factorId}/cross-section`,
    { params: query },
  )
  return data
}

/** 查询因子模板实例在单指数上的时间序列 */
export async function fetchFactorTimeSeries(
  factorId: string,
  indexCode: string,
  startDate: string,
  endDate: string,
  params?: FactorParams,
): Promise<FactorRow[]> {
  const query: Record<string, string> = {
    index_code: indexCode,
    start_date: startDate,
    end_date: endDate,
  }
  const encoded = encodeParams(params)
  if (encoded) {
    query.params = encoded
  }
  const { data } = await apiClient.get<FactorRow[]>(`/factors/${factorId}/values`, {
    params: query,
  })
  return data
}

/** 查询因子模板实例的 IC 分析（IC 时间序列和汇总统计） */
export async function fetchFactorIC(
  factorId: string,
  startDate: string,
  endDate: string,
  forwardDays = 1,
  minCrossSectionN?: number,
  params?: FactorParams,
): Promise<ICResponse> {
  const query: Record<string, string | number> = {
    start_date: startDate,
    end_date: endDate,
    forward_days: forwardDays,
  }
  if (minCrossSectionN !== undefined) {
    query.min_cross_section_n = minCrossSectionN
  }
  const encoded = encodeParams(params)
  if (encoded) {
    query.params = encoded
  }
  const { data } = await apiClient.get<ICResponse>(`/factors/${factorId}/ic`, { params: query })
  return data
}

/** 查询因子间截面 Rank 相关性矩阵（按各模板默认参数口径） */
export async function fetchFactorCorrelation(
  tradeDate: string,
  factorIds?: string[],
  minCrossSectionN?: number,
): Promise<CorrelationResponse> {
  const params: Record<string, string | string[] | number> = { trade_date: tradeDate }
  if (factorIds && factorIds.length > 0) {
    params.factor_ids = factorIds
  }
  if (minCrossSectionN !== undefined) {
    params.min_cross_section_n = minCrossSectionN
  }
  const { data } = await apiClient.get<CorrelationResponse>('/factors/correlation', { params })
  return data
}
