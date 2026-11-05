import { getErrorMessage } from '../api/client'
import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  createStrategy,
  deleteStrategy,
  fetchStrategies,
  fetchStrategyDetail,
  starStrategy,
  unstarStrategy,
  updateStrategy,
  validateStrategyConfig,
} from '../api/strategies'
import type {
  StrategyConfigCreate,
  StrategyConfigUpdate,
  StrategyDetail,
  StrategySummary,
  StrategyValidationResult,
} from '../types/api'

/**
 * 策略状态管理。
 *
 * 管理策略列表、当前详情、配置 CRUD 操作和校验状态。
 */
export const useStrategyStore = defineStore('strategies', () => {
  const items = ref<StrategySummary[]>([])
  const current = ref<StrategyDetail | null>(null)
  /** 列表 / 详情加载中（读操作） */
  const loading = ref(false)
  /** 列表 / 详情加载失败文案（读操作）；页面据此显示错误态 */
  const error = ref<string | null>(null)
  /** 创建 / 更新 / 删除 / 星标进行中（写操作），不影响页面主体渲染 */
  const saving = ref(false)
  /** 写操作失败文案：在弹窗或提示条中展示，不能让整页内容消失 */
  const actionError = ref<string | null>(null)
  const validationResult = ref<StrategyValidationResult | null>(null)

  /** 加载所有策略摘要 */
  async function loadAll(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      items.value = await fetchStrategies()
    } catch (e) {
      error.value = getErrorMessage(e, '加载策略列表失败')
    } finally {
      loading.value = false
    }
  }

  /** 加载单个策略详情 */
  async function loadOne(strategyId: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      current.value = await fetchStrategyDetail(strategyId)
    } catch (e) {
      error.value = getErrorMessage(e, '加载策略详情失败')
    } finally {
      loading.value = false
    }
  }

  /** 创建策略配置 */
  async function create(req: StrategyConfigCreate): Promise<boolean> {
    saving.value = true
    actionError.value = null
    try {
      await createStrategy(req)
      await loadAll()
      return true
    } catch (e) {
      actionError.value = getErrorMessage(e, '创建策略失败')
      return false
    } finally {
      saving.value = false
    }
  }

  /** 更新策略配置 */
  async function update(strategyId: string, req: StrategyConfigUpdate): Promise<boolean> {
    saving.value = true
    actionError.value = null
    try {
      current.value = await updateStrategy(strategyId, req)
      await loadAll()
      return true
    } catch (e) {
      actionError.value = getErrorMessage(e, '更新策略失败')
      return false
    } finally {
      saving.value = false
    }
  }

  /** 删除策略配置 */
  async function remove(strategyId: string): Promise<boolean> {
    saving.value = true
    actionError.value = null
    try {
      await deleteStrategy(strategyId)
      await loadAll()
      return true
    } catch (e) {
      actionError.value = getErrorMessage(e, '删除策略失败')
      return false
    } finally {
      saving.value = false
    }
  }

  /** 星标关注策略 */
  async function star(strategyId: string): Promise<boolean> {
    try {
      await starStrategy(strategyId)
      // 乐观更新本地状态
      const item = items.value.find(i => i.strategy_id === strategyId)
      if (item) item.is_starred = true
      if (current.value && current.value.strategy_id === strategyId) {
        current.value.is_starred = true
      }
      return true
    } catch (e) {
      actionError.value = getErrorMessage(e, '星标策略失败')
      return false
    }
  }

  /** 取消星标关注 */
  async function unstar(strategyId: string): Promise<boolean> {
    try {
      await unstarStrategy(strategyId)
      // 乐观更新本地状态
      const item = items.value.find(i => i.strategy_id === strategyId)
      if (item) item.is_starred = false
      if (current.value && current.value.strategy_id === strategyId) {
        current.value.is_starred = false
      }
      return true
    } catch (e) {
      actionError.value = getErrorMessage(e, '取消星标失败')
      return false
    }
  }

  /** 校验策略配置 */
  async function validate(configJson: Record<string, unknown>): Promise<void> {
    try {
      validationResult.value = await validateStrategyConfig(configJson)
    } catch {
      validationResult.value = { valid: false, errors: ['校验请求失败'], warnings: [] }
    }
  }

  return {
    items,
    current,
    loading,
    error,
    saving,
    actionError,
    validationResult,
    loadAll,
    loadOne,
    create,
    update,
    remove,
    star,
    unstar,
    validate,
  }
})
