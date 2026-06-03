import { apiClient } from './client'
import type {
  CrossSectionResponse,
  FactorRow,
  FactorSpec,
  FactorUpdatePayload,
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
): Promise<CrossSectionResponse> {
  const params: Record<string, string> = {}
  if (tradeDate) {
    params.trade_date = tradeDate
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
): Promise<FactorRow[]> {
  const { data } = await apiClient.get<FactorRow[]>(`/factors/${factorId}/values`, {
    params: { etf_code: etfCode, start_date: startDate, end_date: endDate },
  })
  return data
}
