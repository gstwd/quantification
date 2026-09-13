import { apiClient } from './client'
import type {
  HealthSnapshot,
  LifecycleDetail,
  LifecycleSummary,
  ValidationUsageResponse,
} from '../types/api'

/**
 * 获取全部上线策略的生命周期摘要列表。
 *
 * @returns 生命周期摘要数组（按上线日期倒序）
 */
export async function fetchLifecycles(): Promise<LifecycleSummary[]> {
  const { data } = await apiClient.get<LifecycleSummary[]>('/strategies/lifecycle')
  return data
}

/**
 * 获取单策略的生命周期详情（含冻结快照与最近体检记录）。
 *
 * @param strategyId - 策略 ID
 * @returns 生命周期详情
 */
export async function fetchLifecycleDetail(strategyId: string): Promise<LifecycleDetail> {
  const { data } = await apiClient.get<LifecycleDetail>(`/strategies/${strategyId}/lifecycle`)
  return data
}

/**
 * 人工标记策略上线：冻结配置快照并生成研究期分布。
 *
 * 首次调用若无可复用的研究期回测，会同步执行一次十年回测，耗时较长。
 *
 * @param strategyId - 策略 ID
 * @param payload - 上线日期、备注与成本假设
 * @returns 上线后的生命周期详情
 */
export async function onlineStrategy(
  strategyId: string,
  payload: { live_at?: string | null; note?: string | null; cost_bps?: number | null } = {},
): Promise<LifecycleDetail> {
  const { data } = await apiClient.post<LifecycleDetail>(
    `/strategies/${strategyId}/lifecycle/online`,
    payload,
  )
  return data
}

/**
 * 人工变更生命周期状态（LIVE / SUSPENDED / RETIRED）。
 *
 * @param strategyId - 策略 ID
 * @param payload - 目标状态与说明
 * @returns 更新后的生命周期详情
 */
export async function updateLifecycleStatus(
  strategyId: string,
  payload: { status: 'LIVE' | 'SUSPENDED' | 'RETIRED'; note?: string | null },
): Promise<LifecycleDetail> {
  const { data } = await apiClient.post<LifecycleDetail>(
    `/strategies/${strategyId}/lifecycle/status`,
    payload,
  )
  return data
}

/**
 * 人工触发一次健康体检（同步执行上线后回测并与研究期分布比对）。
 *
 * @param strategyId - 策略 ID
 * @param payload - 可选的单边成本覆盖
 * @returns 新生成的健康快照
 */
export async function refreshLifecycle(
  strategyId: string,
  payload: { cost_bps?: number | null } = {},
): Promise<HealthSnapshot> {
  const { data } = await apiClient.post<HealthSnapshot>(
    `/strategies/${strategyId}/lifecycle/refresh`,
    payload,
  )
  return data
}

/**
 * 获取验证期数据使用留痕列表（哪些回测看过 2026 之后的数据）。
 *
 * @param limit - 返回条数上限
 * @returns 留痕记录与总数
 */
export async function fetchValidationUsage(limit = 200): Promise<ValidationUsageResponse> {
  const { data } = await apiClient.get<ValidationUsageResponse>('/backtests/validation-usage', {
    params: { limit },
  })
  return data
}
