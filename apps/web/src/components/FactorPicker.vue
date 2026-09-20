<template>
  <!--
    因子选择行组件。

    每行包含：模板下拉选择、别名输入、模板参数输入、权重输入、变换函数下拉、删除按钮。
    用于评分模块和择时模块的因子配置：别名承载策略语义，模板与参数承载计算语义。
  -->
  <div class="factor-row">
    <select
      :value="modelValue.template_id"
      class="fp-select fp-factor"
      @change="onTemplateChange(($event.target as HTMLSelectElement).value)"
    >
      <option value="" disabled>选择因子模板</option>
      <optgroup
        v-for="group in groupedFactors"
        :key="group.category"
        :label="group.category || '其他'"
      >
        <option v-for="f in group.items" :key="f.factor_id" :value="f.factor_id">
          {{ f.factor_id }} — {{ f.name }}
        </option>
      </optgroup>
    </select>

    <input
      :value="modelValue.alias"
      type="text"
      class="fp-input fp-alias"
      placeholder="别名"
      title="策略中的因子引用名，如 trend_fast"
      @input="updateField('alias', ($event.target as HTMLInputElement).value)"
    />

    <template v-for="key in parameterKeys" :key="key">
      <input
        :value="modelValue.params[key]"
        type="number"
        :step="parameterSpec(key)?.type === 'integer' ? 1 : 0.05"
        :min="parameterSpec(key)?.minimum ?? undefined"
        :max="parameterSpec(key)?.maximum ?? undefined"
        class="fp-input fp-param"
        :title="parameterSpec(key)?.description || key"
        @input="updateParam(key, ($event.target as HTMLInputElement).value)"
      />
    </template>

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
 * 用于评分和择时模块中选择模板、命名别名、覆盖模板参数并设置权重与变换函数。
 */

import { computed } from 'vue'

import type { FactorParameterSpec, FactorSpec } from '../types/api'

/** 因子行数据结构：别名 + 模板 + 参数 + 权重 + 变换 */
interface FactorRowValue {
  alias: string
  template_id: string
  params: Record<string, number>
  weight: number
  transform?: string
}

const props = defineProps<{
  /** 可用因子模板列表 */
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

/** 按 category 分组模板 */
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

/** 当前模板声明的可覆盖参数名 */
const parameterKeys = computed(() => {
  const template = props.factors.find((f) => f.factor_id === props.modelValue.template_id)
  return Object.keys(template?.parameter_schema ?? {})
})

/** 查询单个参数的声明 */
function parameterSpec(key: string): FactorParameterSpec | undefined {
  const template = props.factors.find((f) => f.factor_id === props.modelValue.template_id)
  return template?.parameter_schema?.[key]
}

/** 切换模板：重置参数为该模板默认值，别名未填时用模板 ID 兜底 */
function onTemplateChange(templateId: string): void {
  const template = props.factors.find((f) => f.factor_id === templateId)
  const params: Record<string, number> = {}
  for (const [key, spec] of Object.entries(template?.parameter_schema ?? {})) {
    params[key] = spec.default
  }
  const alias = props.modelValue.alias || defaultAlias(templateId, params)
  emit('update:modelValue', { ...props.modelValue, template_id: templateId, params, alias })
}

/** 生成默认别名：零参数模板直接用模板 ID，带参数模板拼上参数值 */
function defaultAlias(templateId: string, params: Record<string, number>): string {
  const values = Object.values(params)
  if (values.length === 0) return templateId
  return `${templateId}_${values.join('_')}`
}

function updateField(key: keyof FactorRowValue, value: string | undefined): void {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function updateParam(key: string, raw: string): void {
  const num = raw === '' ? 0 : parseFloat(raw)
  const params = { ...props.modelValue.params, [key]: isNaN(num) ? 0 : num }
  emit('update:modelValue', { ...props.modelValue, params })
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
.fp-alias { flex: 1.2; min-width: 0; }
.fp-param { width: 76px; text-align: center; }
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
