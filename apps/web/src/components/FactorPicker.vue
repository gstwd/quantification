<template>
  <!--
    因子选择行组件。

    每行包含：因子下拉选择、权重输入、变换函数下拉、删除按钮。
    用于评分模块和择时模块的因子配置。
  -->
  <div class="factor-row">
    <select
      :value="modelValue.factor_id"
      class="fp-select fp-factor"
      @change="updateField('factor_id', ($event.target as HTMLSelectElement).value)"
    >
      <option value="" disabled>选择因子</option>
      <optgroup v-for="group in groupedFactors" :key="group.category" :label="group.category || '其他'">
        <option v-for="f in group.items" :key="f.factor_id" :value="f.factor_id">
          {{ f.factor_id }} — {{ f.name }}
        </option>
      </optgroup>
    </select>

    <div class="fp-weight-wrap">
      <input
        :value="modelValue.weight"
        type="number"
        step="0.1"
        class="fp-input fp-weight"
        placeholder="权重"
        @input="updateNumber('weight', ($event.target as HTMLInputElement).value)"
      />
    </div>

    <select
      :value="modelValue.transform || ''"
      class="fp-select fp-transform"
      @change="updateField('transform', ($event.target as HTMLSelectElement).value || undefined)"
    >
      <option value="">无变换</option>
      <option
        v-for="t in TRANSFORM_OPTIONS"
        :key="t.value"
        :value="t.value"
        :title="t.description"
      >
        {{ t.label }}
      </option>
    </select>

    <button class="fp-remove" @click="$emit('remove')" title="移除">×</button>
  </div>
</template>

<script setup lang="ts">
/**
 * 因子选择行组件。
 *
 * 用于评分和择时模块中选择因子、设置权重和变换函数。
 */

import { computed } from 'vue'

import type { FactorSpec } from '../types/api'

/** 因子行数据结构 */
interface FactorRowValue {
  factor_id: string
  weight: number
  transform?: string
}

const props = defineProps<{
  /** 可用因子列表 */
  factors: FactorSpec[]
  /** 当前行数据 */
  modelValue: FactorRowValue
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: FactorRowValue): void
  (e: 'remove'): void
}>()

/** 内置变换函数选项 */
const TRANSFORM_OPTIONS = [
  { value: 'invert_percentile', label: '反转百分位', description: '100 - value，百分位越低得分越高' },
  { value: 'momentum_score', label: '动量得分', description: '收益率分段映射为 0-100 得分' },
  { value: 'volume_score', label: '量能得分', description: '量比分段映射为 0-100 得分' },
  { value: 'trend_score', label: '趋势得分', description: 'MA 偏离度映射为 0-100 得分' },
  { value: 'clamp_0_100', label: '裁剪 0-100', description: '限制值在 0-100 范围内' },
]

/** 按 category 分组因子 */
const groupedFactors = computed(() => {
  const groups = new Map<string, FactorSpec[]>()
  for (const f of props.factors) {
    if (!f.is_active) continue
    const cat = f.category || '其他'
    if (!groups.has(cat)) groups.set(cat, [])
    groups.get(cat)!.push(f)
  }
  return Array.from(groups.entries()).map(([category, items]) => ({ category, items }))
})

function updateField(key: keyof FactorRowValue, value: string | undefined): void {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function updateNumber(key: keyof FactorRowValue, raw: string): void {
  const num = raw === '' ? 0 : parseFloat(raw)
  emit('update:modelValue', { ...props.modelValue, [key]: isNaN(num) ? 0 : num })
}
</script>

<style scoped>
.factor-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.fp-select, .fp-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 12px;
  color: var(--text);
  outline: none;
  transition: border-color 0.15s;
}
.fp-select:focus, .fp-input:focus { border-color: var(--accent); }

.fp-factor { flex: 2; min-width: 0; }
.fp-weight { width: 72px; text-align: center; }
.fp-transform { flex: 1.2; min-width: 0; }

.fp-remove {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 16px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}
.fp-remove:hover { background: rgba(239,68,68,0.15); color: #f87171; border-color: rgba(239,68,68,0.3); }
</style>
