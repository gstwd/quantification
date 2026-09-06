import { apiClient } from './client'

/** 申万一级行业摘要 */
export interface IndustryIndexSummary {
  industry_code: string
  name_cn: string
  is_benchmark_excluded: boolean
}

/** 行业数据管理列表摘要（元数据 + 成分数 + 质量快照 + 最新行情） */
export interface IndustrySummaryItem {
  industry_code: string
  name_cn: string
  is_benchmark_excluded: boolean
  member_count: number
  data_start_date: string | null
  data_end_date: string | null
  bar_count: number | null
  missing_day_count: number | null
  quality_checked_at: string | null
  latest_trade_date: string | null
  latest_close: number | null
  latest_change_pct: number | null
}

/** 行业详情页数据质量 */
export interface IndustryQualityDetail {
  industry_code: string
  data_start_date: string | null
  data_end_date: string | null
  bar_count: number | null
  missing_day_count: number | null
  quality_checked_at: string | null
  total: number
  min_date: string | null
  max_date: string | null
  missing_open: number
  missing_high: number
  missing_low: number
  missing_close: number
  incomplete_rows: number
  incomplete_ratio: number
  change_pct_null: number
  change_pct_null_rate: number
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

/** 调试页数据问题条目 */
export interface IndustryLabIssue {
  level: 'warn' | 'error' | 'info'
  code: string
  message: string
  industry_codes: string[]
  count: number | null
  sample_dates: string[]
}

/** 单行业 RRG 数据覆盖与有效区间 */
export interface IndustryRRGCoverageItem {
  industry_code: string
  name_cn: string
  data_start_date: string | null
  data_end_date: string | null
  expected_input_days: number
  present_input_days: number
  missing_input_days: number
  output_days: number
  leading_nan_days: number
  valid_count: number
  valid_from: string | null
  valid_until: string | null
}

/** RRG 查询元信息 */
export interface IndustryRRGMeta {
  requested_start: string
  requested_end: string
  effective_start: string | null
  effective_end: string | null
  market_trading_days: number
  warmup_required: number
  max_range_days: number
  range_exceeded: boolean
  issues: IndustryLabIssue[]
  coverage: IndustryRRGCoverageItem[]
  notes: string[]
}

/** RRG 响应 */
export interface IndustryRRGResponse {
  points: IndustryRRGPoint[]
  warmup_days: number
  meta: IndustryRRGMeta | null
}

/** 扩散单点数据 */
export interface IndustryDiffusionPoint {
  trade_date: string
  industry_code: string
  name_cn: string
  value: number | null
  valid_count: number | null
  member_count: number | null
  coverage: number | null
}

/** 单行业扩散数据覆盖统计 */
export interface IndustryDiffusionCoverageItem {
  industry_code: string
  name_cn: string
  member_count: number
  data_start_date: string | null
  data_end_date: string | null
  output_days: number
  no_sample_days: number
  valid_count: number
  valid_from: string | null
  valid_until: string | null
  avg_sample_count: number | null
}

/** 扩散查询元信息 */
export interface IndustryDiffusionMeta {
  requested_start: string
  requested_end: string
  effective_start: string | null
  effective_end: string | null
  market_trading_days: number
  output_days: number
  max_range_days: number
  range_exceeded: boolean
  issues: IndustryLabIssue[]
  coverage: IndustryDiffusionCoverageItem[]
  rules: string[]
}

/** 扩散响应 */
export interface IndustryDiffusionResponse {
  points: IndustryDiffusionPoint[]
  meta: IndustryDiffusionMeta | null
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

/** 拉取行业数据管理列表摘要 */
export async function fetchIndustrySummaries(): Promise<IndustrySummaryItem[]> {
  const { data } = await apiClient.get<IndustrySummaryItem[]>('/industry/summary')
  return data
}

/** 拉取单个申万一级行业日线 K 线数据 */
export async function fetchIndustryDailyBars(
  industryCode: string,
  params: { startDate?: string; endDate?: string; limit?: number } = {},
): Promise<DailyBarLike[]> {
  const { data } = await apiClient.get<DailyBarLike[]>(
    `/industry/indexes/${industryCode}/bars`,
    {
      params: {
        start_date: params.startDate || undefined,
        end_date: params.endDate || undefined,
        limit: params.limit ?? 250,
      },
    },
  )
  return data
}

/** 拉取单个申万一级行业的数据质量详情 */
export async function fetchIndustryQuality(
  industryCode: string,
): Promise<IndustryQualityDetail> {
  const { data } = await apiClient.get<IndustryQualityDetail>(
    `/industry/indexes/${industryCode}/quality`,
  )
  return data
}

/** 行业日线 K 线明细（与系统 DailyBar 一致，本地最小接口避免循环依赖） */
export interface DailyBarLike {
  trade_date: string
  code: string
  open_price: number | null
  high_price: number | null
  low_price: number | null
  close_price: number | null
  prev_close_price: number | null
  change_pct: number | null
  volume: number | null
  turnover: number | null
  source: string
  ingested_at?: string | null
}

/**
 * 拉取 RRG 序列。
 *
 * @param start - 起始日期 YYYY-MM-DD
 * @param end - 截止日期 YYYY-MM-DD
 * @param codes - 行业代码列表
 * @param params - RRG 参数
 * @param options - 可选超时配置；RRG/扩散为研究计算，默认放宽到 120 秒
 * @returns RRG 序列点
 */
export async function fetchRRG(
  start: string,
  end: string,
  codes: string[],
  params: Pick<RotationParams, 'lookbackRatio' | 'lookbackMom' | 'smoothWindow'>,
  options: { timeout?: number } = {},
): Promise<IndustryRRGResponse> {
  const { data } = await apiClient.get<IndustryRRGResponse>('/industry/rrg', {
    params: {
      start,
      end,
      industry_codes: codes.join(','),
      lookback_ratio: params.lookbackRatio,
      lookback_mom: params.lookbackMom,
      smooth_window: params.smoothWindow,
    },
    timeout: options.timeout ?? 120_000,
  })
  return data
}

/**
 * 拉取数量占比扩散序列。
 *
 * @param start - 起始日期 YYYY-MM-DD
 * @param end - 截止日期 YYYY-MM-DD
 * @param codes - 行业代码列表
 * @param params - 扩散参数
 * @param withCoverage - 是否逐点返回每日覆盖度
 * @param options - 可选超时配置；扩散为研究计算，默认放宽到 120 秒
 * @returns 扩散序列点
 */
export async function fetchDiffusion(
  start: string,
  end: string,
  codes: string[],
  params: Pick<RotationParams, 'diffusionLookback' | 'smoothWindow'>,
  withCoverage = true,
  options: { timeout?: number } = {},
): Promise<IndustryDiffusionResponse> {
  const { data } = await apiClient.get<IndustryDiffusionResponse>('/industry/diffusion', {
    params: {
      start,
      end,
      industry_codes: codes.join(','),
      diffusion_lookback: params.diffusionLookback,
      smooth_window: params.smoothWindow,
      with_coverage: withCoverage,
    },
    timeout: options.timeout ?? 120_000,
  })
  return data
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
