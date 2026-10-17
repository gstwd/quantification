<template>
  <div class="param-editor">
    <label v-for="spec in definition.params" :key="spec.name" class="param-item">
      <span class="param-label" :class="{ changed: isChanged(spec) }">{{ spec.label }}</span>
      <input
        type="number"
        class="num-input"
        :min="spec.minimum"
        :max="spec.maximum"
        :step="spec.kind === 'int' ? 1 : 0.005"
        :value="modelValue[spec.name] ?? spec.default"
        @change="onInput(spec, $event)"
      />
      <span class="param-range">{{ spec.minimum }}~{{ spec.maximum }}</span>
    </label>
    <span v-if="!definition.params.length" class="param-range">无可调参数</span>
    <button v-if="definition.params.length" class="link-btn" type="button" @click="reset">
      恢复研报默认
    </button>
    <span v-if="hasChanges" class="changed-badge">非默认参数：仅供理解原理，不作为晋升依据</span>
  </div>
</template>

<script setup lang="ts">
/**
 * 研究参数编辑器：按定义的 parameter 声明生成数字输入，标记偏离研报默认值的参数。
 */
import { computed } from 'vue'

import type { SignalLabDefinition, SignalLabParam } from '../../types/api'

const props = defineProps<{
  definition: SignalLabDefinition
  modelValue: Record<string, number>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, number>): void
}>()

/** 判断参数是否偏离默认值 */
function isChanged(spec: SignalLabParam): boolean {
  const value = props.modelValue[spec.name]
  return value !== undefined && value !== spec.default
}

const hasChanges = computed(() => props.definition.params.some((spec) => isChanged(spec)))

/** 输入变更：裁剪到合法范围后回写 */
function onInput(spec: SignalLabParam, event: Event): void {
  const raw = Number((event.target as HTMLInputElement).value)
  let value = Number.isFinite(raw) ? raw : spec.default
  if (spec.kind === 'int') value = Math.round(value)
  value = Math.min(spec.maximum, Math.max(spec.minimum, value))
  ;(event.target as HTMLInputElement).value = String(value)
  emit('update:modelValue', { ...props.modelValue, [spec.name]: value })
}

/** 恢复为研报默认参数 */
function reset(): void {
  const next: Record<string, number> = {}
  for (const spec of props.definition.params) next[spec.name] = spec.default
  emit('update:modelValue', next)
}
</script>

<style scoped>
.param-editor {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
}
.param-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}
.param-label {
  color: var(--text-muted);
}
.param-label.changed {
  color: var(--warning);
  font-weight: 600;
}
.param-range {
  color: #64748b;
  font-size: 11px;
}
.num-input {
  width: 84px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 12px;
}
.link-btn {
  background: transparent;
  border: none;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
}
.changed-badge {
  font-size: 11px;
  color: var(--warning);
  background: rgba(245, 158, 11, 0.12);
  border-radius: 10px;
  padding: 2px 8px;
}
</style>
