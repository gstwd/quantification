<template>
  <div class="rule-group-editor">
    <div class="radio-row">
      <label class="radio-opt">
        <input type="radio" :checked="modelValue.logic === 'AND'" @change="setLogic('AND')" /> AND（全部满足）
      </label>
      <label class="radio-opt">
        <input type="radio" :checked="modelValue.logic === 'OR'" @change="setLogic('OR')" /> OR（任一满足）
      </label>
    </div>
    <div v-for="(rule, i) in modelValue.rules" :key="i" class="filter-rule-row">
      <select
        :value="rule.factor"
        class="fp-select fr-factor"
        @change="updateRule(i, { factor: ($event.target as HTMLSelectElement).value })"
      >
        <option value="" disabled>选择因子</option>
        <option v-for="opt in factorOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
      </select>
      <select
        :value="rule.op"
        class="fp-select fr-op"
        @change="onOpChange(i, ($event.target as HTMLSelectElement).value)"
      >
        <option value="gt">大于 (>)</option>
        <option value="lt">小于 (<)</option>
        <option value="gte">大于等于 (≥)</option>
        <option value="lte">小于等于 (≤)</option>
        <option value="eq">等于 (=)</option>
        <option value="neq">不等于 (≠)</option>
        <option value="between">区间 (between)</option>
      </select>
      <select
        :value="rule.missing_strategy ?? 'fail'"
        class="fp-select fr-missing"
        title="因子值缺失时的处理方式"
        @change="updateRule(i, { missing_strategy: ($event.target as HTMLSelectElement).value })"
      >
        <option value="fail">缺失:不满足</option>
        <option value="pass">缺失:满足</option>
      </select>
      <template v-if="rule.op === 'between'">
        <input
          :value="Array.isArray(rule.value) ? rule.value[0] : ''"
          type="number"
          step="any"
          class="fp-input fr-value"
          placeholder="最小值"
          @input="updateBetween(i, 0, ($event.target as HTMLInputElement).value)"
        />
        <span class="fr-sep">~</span>
        <input
          :value="Array.isArray(rule.value) ? rule.value[1] : ''"
          type="number"
          step="any"
          class="fp-input fr-value"
          placeholder="最大值"
          @input="updateBetween(i, 1, ($event.target as HTMLInputElement).value)"
        />
      </template>
      <input
        v-else
        :value="typeof rule.value === 'number' ? rule.value : ''"
        type="number"
        step="any"
        class="fp-input fr-value"
        placeholder="值"
        @input="updateRule(i, { value: parseFloat(($event.target as HTMLInputElement).value) || 0 })"
      />
      <button class="fp-remove" title="移除" @click="removeRule(i)">×</button>
    </div>
    <span v-if="modelValue.groups.length > 0" class="threshold-hint">
      另含 {{ modelValue.groups.length }} 个嵌套子规则组：表单不支持编辑，保存时原样保留（用 CLI 或配置 JSON 修改）
    </span>
    <button class="add-btn" @click="addRule">+ 添加规则</button>
  </div>
</template>

<script setup lang="ts">
/**
 * 规则组编辑器：AND / OR + 规则列表（固定值或区间比较）。
 *
 * 用于择时的条件仓位上限等只比较固定值的规则组；嵌套子组以不透明数据保留，
 * 表单只展示数量提示。修改时整体发出新的规则组对象，不直接改动 props。
 */

/** 单条规则 */
export interface RuleValue {
  factor: string
  op: string
  value: number | number[]
  compare_to?: string
  missing_strategy?: string
}

/** 规则组（groups 为不透明的嵌套子组，原样保留） */
export interface RuleGroupValue {
  logic: string
  rules: RuleValue[]
  groups: unknown[]
}

const props = defineProps<{
  /** 当前规则组 */
  modelValue: RuleGroupValue
  /** 可选因子 */
  factorOptions: Array<{ value: string; label: string }>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: RuleGroupValue): void
}>()

/** 发出更新后的规则组 */
function emitGroup(patch: Partial<RuleGroupValue>): void {
  emit('update:modelValue', { ...props.modelValue, ...patch })
}

function setLogic(logic: string): void {
  emitGroup({ logic })
}

function updateRule(i: number, patch: Partial<RuleValue>): void {
  const rules = props.modelValue.rules.map((rule, index) => (index === i ? { ...rule, ...patch } : rule))
  emitGroup({ rules })
}

/** 切换操作符：区间与单值之间切换时重置比较值 */
function onOpChange(i: number, op: string): void {
  const rule = props.modelValue.rules[i]
  const wasBetween = rule.op === 'between'
  const value = op === 'between' ? (wasBetween ? rule.value : [0, 0]) : wasBetween ? 0 : rule.value
  updateRule(i, { op, value })
}

function updateBetween(i: number, index: number, raw: string): void {
  const rule = props.modelValue.rules[i]
  const arr = Array.isArray(rule.value) ? [...rule.value] : [0, 0]
  arr[index] = raw === '' ? 0 : parseFloat(raw)
  updateRule(i, { value: arr })
}

function addRule(): void {
  emitGroup({
    rules: [...props.modelValue.rules, { factor: '', op: 'gte', value: 0, missing_strategy: 'fail' }],
  })
}

function removeRule(i: number): void {
  emitGroup({ rules: props.modelValue.rules.filter((_, index) => index !== i) })
}
</script>

<style scoped>
.rule-group-editor {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.radio-row { display: flex; gap: 16px; }
.radio-opt { font-size: 12px; color: var(--text); display: flex; align-items: center; gap: 4px; }
.filter-rule-row { display: flex; align-items: center; gap: 8px; }
.fp-select, .fp-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 12px;
  color: var(--text);
  outline: none;
}
.fr-factor { flex: 2; min-width: 0; }
.fr-op { flex: 1.2; min-width: 0; }
.fr-missing { flex: 1; min-width: 0; }
.fr-value { width: 90px; text-align: center; }
.fr-sep { font-size: 12px; color: var(--text-secondary); }
.fp-remove {
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  background: transparent;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
}
.add-btn {
  align-self: flex-start;
  border: 1px dashed var(--border);
  background: transparent;
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  font-size: 12px;
  color: var(--accent);
  cursor: pointer;
}
.threshold-hint { font-size: 11px; color: var(--text-secondary); }
</style>
