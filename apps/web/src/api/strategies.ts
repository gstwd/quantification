import { apiClient } from './client'
import type {
  AllocationResponse,
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

/** 运行资产配置决策管线 */
export async function runAllocation(strategyId: string): Promise<AllocationResponse> {
  const { data } = await apiClient.get<AllocationResponse>(`/strategies/${strategyId}/allocation`)
  return data
}
