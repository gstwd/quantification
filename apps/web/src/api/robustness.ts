import { apiClient } from './client'
import type { RobustnessDetail, RobustnessListResponse } from '../types/api'

/**
 * 获取稳健性验证批次列表（可按基线策略过滤）。
 *
 * @param params - 返回条数上限与可选策略过滤
 * @returns 批次摘要列表与总数
 */
export async function fetchRobustnessRuns(params: {
  strategyId?: string
  limit?: number
} = {}): Promise<RobustnessListResponse> {
  const { data } = await apiClient.get<RobustnessListResponse>('/robustness', {
    params: {
      limit: params.limit ?? 50,
      strategy_id: params.strategyId || undefined,
    },
  })
  return data
}

/**
 * 获取单个稳健性批次详情（含邻域 Δ、消融边际贡献与 PBO/DSR）。
 *
 * @param robustnessId - 批次 ID
 * @returns 批次详情
 */
export async function fetchRobustnessDetail(robustnessId: string): Promise<RobustnessDetail> {
  const { data } = await apiClient.get<RobustnessDetail>(`/robustness/${robustnessId}`)
  return data
}

/**
 * 取消整批稳健性回测（未开始直接取消，运行中在安全检查点退出）。
 *
 * @param robustnessId - 批次 ID
 * @returns 取消结果
 */
export async function cancelRobustnessRun(
  robustnessId: string,
): Promise<Record<string, unknown>> {
  const { data } = await apiClient.post<Record<string, unknown>>(
    `/robustness/${robustnessId}/cancel`,
  )
  return data
}

/**
 * 暂停批次中尚未开始的任务。
 *
 * @param robustnessId - 批次 ID
 * @returns 暂停结果
 */
export async function pauseRobustnessRun(
  robustnessId: string,
): Promise<Record<string, unknown>> {
  const { data } = await apiClient.post<Record<string, unknown>>(
    `/robustness/${robustnessId}/pause`,
  )
  return data
}

/**
 * 恢复批次中被暂停的任务。
 *
 * @param robustnessId - 批次 ID
 * @returns 恢复结果
 */
export async function resumeRobustnessRun(
  robustnessId: string,
): Promise<Record<string, unknown>> {
  const { data } = await apiClient.post<Record<string, unknown>>(
    `/robustness/${robustnessId}/resume`,
  )
  return data
}
