import { defineStore } from 'pinia'
import { ref } from 'vue'

import {
  createStrategy,
  deleteStrategy,
  fetchStrategies,
  fetchStrategyDetail,
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
  const loading = ref(false)
  const error = ref<string | null>(null)
  const validationResult = ref<StrategyValidationResult | null>(null)

  /** 加载所有策略摘要 */
  async function loadAll(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      items.value = await fetchStrategies()
    } catch (e) {
      error.value = e instanceof Error ? e.message : '加载策略列表失败'
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
      error.value = e instanceof Error ? e.message : '加载策略详情失败'
    } finally {
      loading.value = false
    }
  }

  /** 创建策略配置 */
  async function create(req: StrategyConfigCreate): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      await createStrategy(req)
      await loadAll()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : '创建策略失败'
      return false
    } finally {
      loading.value = false
    }
  }

  /** 更新策略配置 */
  async function update(strategyId: string, req: StrategyConfigUpdate): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      current.value = await updateStrategy(strategyId, req)
      await loadAll()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : '更新策略失败'
      return false
    } finally {
      loading.value = false
    }
  }

  /** 删除策略配置 */
  async function remove(strategyId: string): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      await deleteStrategy(strategyId)
      await loadAll()
      return true
    } catch (e) {
      error.value = e instanceof Error ? e.message : '删除策略失败'
      return false
    } finally {
      loading.value = false
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
    validationResult,
    loadAll,
    loadOne,
    create,
    update,
    remove,
    validate,
  }
})
