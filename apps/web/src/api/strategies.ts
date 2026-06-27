import { apiClient } from './client'
import type {
  AllocationResponse,
  StarredSummaryResponse,
  StrategyConfigCreate,
  StrategyConfigUpdate,
  StrategyDetail,
  StrategySummary,
  StrategyValidationResult,
} from '../types/api'

/** 获取所有已启用策略列表 */
export async function fetchStrategies(): Promise<StrategySummary[]> {
  const { data } = await apiClient.get<StrategySummary[]>('/strategies')
  return data
}

/** 获取单个策略详情 */
export async function fetchStrategyDetail(strategyId: string): Promise<StrategyDetail> {
  const { data } = await apiClient.get<StrategyDetail>(`/strategies/${strategyId}`)
  return data
}

/** 创建策略配置 */
export async function createStrategy(req: StrategyConfigCreate): Promise<StrategyDetail> {
  const { data } = await apiClient.post<StrategyDetail>('/strategies', req)
  return data
}

/** 更新策略配置 */
export async function updateStrategy(
  strategyId: string,
  req: StrategyConfigUpdate
): Promise<StrategyDetail> {
  const { data } = await apiClient.put<StrategyDetail>(`/strategies/${strategyId}`, req)
  return data
}

/** 删除策略配置 */
export async function deleteStrategy(strategyId: string): Promise<void> {
  await apiClient.delete(`/strategies/${strategyId}`)
}

/** 校验策略配置 JSON */
export async function validateStrategyConfig(
  configJson: Record<string, unknown>
): Promise<StrategyValidationResult> {
  const { data } = await apiClient.post<StrategyValidationResult>('/strategies/validate', configJson)
  return data
}

/**
 * 运行资产配置决策管线
 * @param strategyId - 策略标识
 * @param tradeDate - 可选，指定交易日（YYYY-MM-DD），不传则使用最新数据
 */
export async function runAllocation(
  strategyId: string,
  tradeDate?: string,
): Promise<AllocationResponse> {
  const { data } = await apiClient.get<AllocationResponse>(
    `/strategies/${strategyId}/allocation`,
    { params: tradeDate ? { trade_date: tradeDate } : {} },
  )
  return data
}

/** 星标关注策略 */
export async function starStrategy(strategyId: string): Promise<void> {
  await apiClient.post(`/strategies/${strategyId}/star`)
}

/** 取消星标关注 */
export async function unstarStrategy(strategyId: string): Promise<void> {
  await apiClient.post(`/strategies/${strategyId}/unstar`)
}

/**
 * 获取所有星标策略的当日执行摘要
 * @param tradeDate - 可选，指定交易日（YYYY-MM-DD），不传则使用今天
 */
export async function fetchStarredSummary(
  tradeDate?: string,
): Promise<StarredSummaryResponse> {
  const { data } = await apiClient.get<StarredSummaryResponse>(
    '/strategies/starred/summary',
    { params: tradeDate ? { trade_date: tradeDate } : {} },
  )
  return data
}
