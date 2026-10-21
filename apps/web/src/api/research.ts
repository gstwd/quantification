import { apiClient } from './client'
import type {
  ResearchCampaign,
  ResearchCampaignDetail,
  ResearchCandidate,
  ResearchCandidateDetail,
  ResearchHypothesisDetail,
  ResearchLedgerResponse,
  ResearchLineage,
} from '../types/api'

/**
 * 获取全部研究线（试验次数 N、预算与假设状态计数）。
 *
 * @returns 研究线列表（按已用试验次数倒序）
 */
export async function fetchResearchLineages(): Promise<ResearchLineage[]> {
  const { data } = await apiClient.get<ResearchLineage[]>('/research/lineages')
  return data
}

/**
 * 查询研究假设台账。
 *
 * @param params - 研究线中任一策略、指数、标签、状态（逗号分隔）过滤
 * @returns 假设列表；给出策略时附研究线摘要
 */
export async function fetchResearchHypotheses(params: {
  strategyId?: string
  indexCode?: string
  tag?: string
  status?: string
} = {}): Promise<ResearchLedgerResponse> {
  const { data } = await apiClient.get<ResearchLedgerResponse>('/research/hypotheses', {
    params: {
      strategy_id: params.strategyId || undefined,
      index_code: params.indexCode || undefined,
      tag: params.tag || undefined,
      status: params.status || undefined,
    },
  })
  return data
}

/**
 * 获取假设详情（判定标准、挂靠试验、子假设）。
 *
 * @param hypothesisId - 假设 ID
 * @returns 假设详情
 */
export async function fetchResearchHypothesis(hypothesisId: string): Promise<ResearchHypothesisDetail> {
  const { data } = await apiClient.get<ResearchHypothesisDetail>(`/research/hypotheses/${hypothesisId}`)
  return data
}

/**
 * 获取研究战役列表。
 *
 * @param status - 可选状态过滤
 * @returns 战役摘要列表
 */
export async function fetchResearchCampaigns(status?: string): Promise<ResearchCampaign[]> {
  const { data } = await apiClient.get<ResearchCampaign[]>('/research/campaigns', {
    params: { status: status || undefined },
  })
  return data
}

/**
 * 获取研究战役详情（进度、步骤、花费）。
 *
 * @param campaignId - 战役 ID
 * @returns 战役详情
 */
export async function fetchResearchCampaign(campaignId: string): Promise<ResearchCampaignDetail> {
  const { data } = await apiClient.get<ResearchCampaignDetail>(`/research/campaigns/${campaignId}`)
  return data
}

/**
 * 获取待人工确认的优化候选。
 *
 * @param lineageId - 可选研究线过滤
 * @returns 候选摘要列表
 */
export async function fetchResearchCandidates(lineageId?: string): Promise<ResearchCandidate[]> {
  const { data } = await apiClient.get<ResearchCandidate[]>('/research/candidates', {
    params: { lineage_id: lineageId || undefined },
  })
  return data
}

/**
 * 获取候选详情（10 项验收清单与证据）。
 *
 * @param optimizationId - 优化会话 ID
 * @returns 候选详情
 */
export async function fetchResearchCandidate(optimizationId: string): Promise<ResearchCandidateDetail> {
  const { data } = await apiClient.get<ResearchCandidateDetail>(`/research/candidates/${optimizationId}`)
  return data
}
