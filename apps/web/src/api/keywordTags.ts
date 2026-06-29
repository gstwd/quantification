/** 关键词标签管理 API 客户端。
 *
 * 提供 keyword_tag_config 表的 CRUD 操作。
 */
import { apiClient } from './client'
import type { KeywordTagConfig } from '../types/api'

/** 查询关键词标签列表。
 *
 * @param offset - 偏移量，默认 0
 * @param limit - 每页数量，默认 100
 * @param activeOnly - 仅返回活跃的映射
 * @returns 关键词标签列表
 */
export async function fetchKeywordTags(
  offset = 0,
  limit = 100,
  activeOnly = false,
): Promise<KeywordTagConfig[]> {
  const { data } = await apiClient.get<KeywordTagConfig[]>('/keyword-tags/', {
    params: { offset, limit, active_only: activeOnly },
  })
  return data
}

/** 查询关键词标签总数。
 *
 * @param activeOnly - 仅统计活跃的映射
 * @returns 总数
 */
export async function countKeywordTags(activeOnly = false): Promise<number> {
  const { data } = await apiClient.get<{ total: number }>('/keyword-tags/count', {
    params: { active_only: activeOnly },
  })
  return data.total
}

/** 新增关键词标签映射。
 *
 * @param body - { keyword, tag, is_active?, priority? }
 * @returns 新创建的配置
 */
export async function createKeywordTag(body: {
  keyword: string
  tag: string
  is_active?: boolean
  priority?: number
}): Promise<KeywordTagConfig> {
  const { data } = await apiClient.post<KeywordTagConfig>('/keyword-tags/', body)
  return data
}

/** 更新关键词标签映射。
 *
 * @param id - 配置 ID
 * @param body - 要更新的字段（仅提供需修改的字段）
 * @returns 更新后的配置
 */
export async function updateKeywordTag(
  id: number,
  body: Partial<KeywordTagConfig>,
): Promise<KeywordTagConfig> {
  const { data } = await apiClient.put<KeywordTagConfig>(`/keyword-tags/${id}`, body)
  return data
}

/** 删除关键词标签映射（软删除）。
 *
 * @param id - 配置 ID
 */
export async function deleteKeywordTag(id: number): Promise<void> {
  await apiClient.delete(`/keyword-tags/${id}`)
}

/** 批量导入关键词标签映射。
 *
 * @param items - { keyword, tag, priority } 列表
 * @returns { processed: number }
 */
export async function batchImportKeywordTags(
  items: Array<{ keyword: string; tag: string; priority: number }>,
): Promise<{ processed: number }> {
  const { data } = await apiClient.post<{ processed: number }>(
    '/keyword-tags/batch-import',
    items,
  )
  return data
}
