import { apiClient } from './client'
import type { AllocationResponse, StrategyDetail } from '../types/api'

/** 获取所有已注册策略列表 */
export async function fetchStrategies(): Promise<StrategyDetail[]> {
  const { data } = await apiClient.get<StrategyDetail[]>('/strategies')
  return data
}

/** 获取单个策略详情 */
export async function fetchStrategyDetail(strategyId: string): Promise<StrategyDetail> {
  const { data } = await apiClient.get<StrategyDetail>(`/strategies/${strategyId}`)
  return data
}

/** 运行资产配置决策管线，返回择时、排名、仓位分配结果 */
export async function runAllocation(strategyId: string): Promise<AllocationResponse> {
  const { data } = await apiClient.get<AllocationResponse>(`/strategies/${strategyId}/allocation`)
  return data
}
