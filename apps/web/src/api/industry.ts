import { apiClient } from './client'

/** 申万一级行业摘要 */
export interface IndustryIndexSummary {
  industry_code: string
  name_cn: string
  is_benchmark_excluded: boolean
}

/** RRG 单点数据 */
export interface IndustryRRGPoint {
  trade_date: string
  industry_code: string
  name_cn: string
  rs_ratio: number | null
  rs_momentum: number | null
  quadrant: number | null
}

/** 扩散单点数据 */
export interface IndustryDiffusionPoint {
  trade_date: string
  industry_code: string
  name_cn: string
  value: number | null
}

/** 轮动单日选择 */
export interface IndustryRotationSelection {
  trade_date: string
  selected_codes: string[]
  weights: Record<string, number>
}

/** 行业轮动查询参数 */
export interface RotationParams {
  signal: 'quadrant' | 'diffusion' | 'diffusion_rrg'
  topN: number
  keepQuadrants: number[]
  lookbackRatio: number
  lookbackMom: number
  smoothWindow: number
  diffusionLookback: number
}

/** 拉取申万一级行业目录 */
export async function fetchIndustryIndexes(): Promise<IndustryIndexSummary[]> {
  const { data } = await apiClient.get<IndustryIndexSummary[]>('/industry/indexes')
  return data
}

/**
 * 拉取 RRG 序列。
 *
 * @param start - 起始日期 YYYY-MM-DD
 * @param end - 截止日期 YYYY-MM-DD
 * @param codes - 行业代码列表
 * @param params - RRG 参数
 * @returns RRG 序列点
 */
export async function fetchRRG(
  start: string,
  end: string,
  codes: string[],
  params: Pick<RotationParams, 'lookbackRatio' | 'lookbackMom' | 'smoothWindow'>,
): Promise<IndustryRRGPoint[]> {
  const { data } = await apiClient.get<{ points: IndustryRRGPoint[] }>('/industry/rrg', {
    params: {
      start,
      end,
      industry_codes: codes.join(','),
      lookback_ratio: params.lookbackRatio,
      lookback_mom: params.lookbackMom,
      smooth_window: params.smoothWindow,
    },
  })
  return data.points
}

/**
 * 拉取数量占比扩散序列。
 *
 * @param start - 起始日期 YYYY-MM-DD
 * @param end - 截止日期 YYYY-MM-DD
 * @param codes - 行业代码列表
 * @param params - 扩散参数
 * @returns 扩散序列点
 */
export async function fetchDiffusion(
  start: string,
  end: string,
  codes: string[],
  params: Pick<RotationParams, 'diffusionLookback' | 'smoothWindow'>,
): Promise<IndustryDiffusionPoint[]> {
  const { data } = await apiClient.get<{ points: IndustryDiffusionPoint[] }>(
    '/industry/diffusion',
    {
      params: {
        start,
        end,
        industry_codes: codes.join(','),
        diffusion_lookback: params.diffusionLookback,
        smooth_window: params.smoothWindow,
      },
    },
  )
  return data.points
}

/**
 * 拉取轮动选择（默认月末决策）。
 *
 * @param start - 起始日期 YYYY-MM-DD
 * @param end - 截止日期 YYYY-MM-DD
 * @param codes - 行业代码列表
 * @param params - 轮动参数
 * @returns 逐决策日选择结果
 */
export async function fetchRotationSelections(
  start: string,
  end: string,
  codes: string[],
  params: RotationParams,
): Promise<IndustryRotationSelection[]> {
  const { data } = await apiClient.get<{ selections: IndustryRotationSelection[] }>(
    '/industry/rotation',
    {
      params: {
        start,
        end,
        industry_codes: codes.join(','),
        signal: params.signal,
        top_n: params.topN,
        keep_quadrants: params.keepQuadrants.join(','),
        lookback_ratio: params.lookbackRatio,
        lookback_mom: params.lookbackMom,
        smooth_window: params.smoothWindow,
        diffusion_lookback: params.diffusionLookback,
        monthly: true,
      },
    },
  )
  return data.selections
}
