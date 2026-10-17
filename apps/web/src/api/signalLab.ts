import { apiClient } from './client'
import type {
  SignalLabCatalog,
  SignalLabCrossSectionResponse,
  SignalLabTimingResponse,
} from '../types/api'

/** 研究参数取值：参数名 → 数值 */
export type SignalLabParams = Record<string, number>

/** 研究台计算较重的请求超时（毫秒） */
const LAB_TIMEOUT = 120_000

/** 参数序列化为 JSON 查询串，空参数省略 */
function encodeParams(params?: SignalLabParams): string | undefined {
  if (!params || Object.keys(params).length === 0) return undefined
  return JSON.stringify(params)
}

/** 获取研究台目录（因子、信号、指数与研究期边界） */
export async function fetchSignalLabCatalog(): Promise<SignalLabCatalog> {
  const { data } = await apiClient.get<SignalLabCatalog>('/signal-lab/catalog')
  return data
}

/**
 * 即时计算单指数择时信号。
 *
 * @param indexCode - 指数代码
 * @param signal - 信号键
 * @param start - 起始日期 YYYY-MM-DD
 * @param end - 截止日期 YYYY-MM-DD
 * @param params - 信号参数
 * @returns 序列、分量与评估指标
 */
export async function fetchSignalLabTiming(
  indexCode: string,
  signal: string,
  start: string,
  end: string,
  params?: SignalLabParams,
): Promise<SignalLabTimingResponse> {
  const { data } = await apiClient.get<SignalLabTimingResponse>('/signal-lab/timing', {
    params: { index_code: indexCode, signal, start, end, params: encodeParams(params) },
    timeout: LAB_TIMEOUT,
    toast: false,
  })
  return data
}

/**
 * 即时计算市场级信号（GSISI）并在代理指数上评估。
 *
 * @param signal - 市场级信号键
 * @param proxyCode - 代理指数代码
 * @param start - 起始日期
 * @param end - 截止日期
 * @param params - 信号参数
 * @returns 与择时相同结构的结果
 */
export async function fetchSignalLabMarket(
  signal: string,
  proxyCode: string,
  start: string,
  end: string,
  params?: SignalLabParams,
): Promise<SignalLabTimingResponse> {
  const { data } = await apiClient.get<SignalLabTimingResponse>('/signal-lab/market', {
    params: { signal, proxy_code: proxyCode, start, end, params: encodeParams(params) },
    timeout: LAB_TIMEOUT,
    toast: false,
  })
  return data
}

/**
 * 即时计算截面因子的周频 Rank IC、三分组与相关性。
 *
 * @param factor - 因子键
 * @param start - 起始日期
 * @param end - 截止日期
 * @param horizon - 未来收益窗口（交易日）
 * @param indexCodes - 指数池，空数组表示全部活跃指数
 * @param params - 因子参数
 * @returns IC 序列与统计
 */
export async function fetchSignalLabCrossSection(
  factor: string,
  start: string,
  end: string,
  horizon: number,
  indexCodes: string[],
  params?: SignalLabParams,
): Promise<SignalLabCrossSectionResponse> {
  const { data } = await apiClient.get<SignalLabCrossSectionResponse>('/signal-lab/cross-section', {
    params: {
      factor,
      start,
      end,
      horizon,
      index_codes: indexCodes.length ? indexCodes.join(',') : undefined,
      params: encodeParams(params),
    },
    timeout: LAB_TIMEOUT,
    toast: false,
  })
  return data
}
