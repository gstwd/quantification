<template>
  <div class="composite-form">
    <div class="module-desc">
      组合策略按目标权重合成多个普通策略的持仓后统一记账：成员各自保留择时、风控与调仓日程，
      成员之间方向相反的交易被轧差，成员权重随净值漂移、在再平衡日调回目标。成员权重与现金权重之和须为 100%。
    </div>

    <div class="member-table">
      <div v-for="(member, i) in members" :key="i" class="member-row">
        <select v-model="member.strategy_id" class="fp-select member-select">
          <option value="">选择成员策略</option>
          <option
            v-for="item in candidates"
            :key="item.strategy_id"
            :value="item.strategy_id"
          >
            {{ item.display_name }}（{{ item.strategy_id }}）
          </option>
        </select>
        <input v-model.number="member.weight" type="number" min="1" max="100" class="fp-input weight-input" />
        <span class="unit">%</span>
        <button class="btn-link" type="button" @click="removeMember(i)">移除</button>
      </div>
      <button class="btn-link" type="button" :disabled="members.length >= 10" @click="addMember">+ 添加成员</button>
    </div>

    <div class="row">
      <label class="sub-label">现金权重</label>
      <input v-model.number="cashWeight" type="number" min="0" max="99" class="fp-input weight-input" />
      <span class="unit">%</span>
      <span :class="['total', totalOk ? 'ok' : 'bad']">合计 {{ total.toFixed(1) }}%</span>
    </div>

    <div class="row">
      <label class="sub-label">成员权重再平衡</label>
      <select v-model="frequency" class="fp-select">
        <option value="daily">每日（恒定比例）</option>
        <option value="weekly">每周</option>
        <option value="biweekly">双周</option>
        <option value="monthly">每月</option>
      </select>
      <select v-if="frequency === 'weekly' || frequency === 'biweekly'" v-model.number="dayOfWeek" class="fp-select">
        <option :value="0">周一</option>
        <option :value="1">周二</option>
        <option :value="2">周三</option>
        <option :value="3">周四</option>
        <option :value="4">周五</option>
      </select>
      <select v-if="frequency === 'biweekly'" v-model="weekParity" class="fp-select" title="以 2016-01-04 所在周为第 1 周连续计数">
        <option value="odd">奇数周</option>
        <option value="even">偶数周</option>
      </select>
      <input
        v-if="frequency === 'monthly'"
        v-model.number="dayOfMonth"
        type="number"
        min="1"
        max="31"
        class="fp-input weight-input"
        title="超出当月天数时按当月最后一日处理"
      />
    </div>

    <ul v-if="errors.length > 0" class="form-errors">
      <li v-for="err in errors" :key="err">{{ err }}</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
/**
 * 组合策略配置表单：编辑成员、权重、现金权重与成员再平衡日程，输出 kind=composite 的 config_json。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { fetchStrategies } from '../api/strategies'
import type { CompositeConfigJson, StrategySummary } from '../types/api'

const props = defineProps<{
  /** 当前 config_json（组合策略） */
  modelValue: Record<string, unknown>
  /** 正在编辑的组合策略自身 ID（成员候选中排除） */
  selfId?: string
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: Record<string, unknown>): void }>()

interface MemberRow {
  strategy_id: string
  /** 百分数 */
  weight: number
}

const strategies = ref<StrategySummary[]>([])
const members = ref<MemberRow[]>([])
const cashWeight = ref(0)
const frequency = ref('daily')
const dayOfWeek = ref(4)
const dayOfMonth = ref(1)
const weekParity = ref('even')
/** 原配置是否显式声明了调仓日（未声明且未改动时保存继续省略，保持配置哈希不变） */
const dayOfWeekDeclared = ref(false)
const dayOfMonthDeclared = ref(false)
let selfUpdating = false

/** 成员候选：排除自身与变体草稿（组合策略在提交时由后端拒绝嵌套） */
const candidates = computed(() =>
  strategies.value.filter(item => item.strategy_id !== props.selfId && !item.is_variant),
)

const total = computed(() => members.value.reduce((sum, m) => sum + (m.weight || 0), 0) + (cashWeight.value || 0))
const totalOk = computed(() => Math.abs(total.value - 100) < 1e-6)

const errors = computed((): string[] => {
  const errs: string[] = []
  if (members.value.length === 0) errs.push('至少需要 1 个成员策略')
  const ids = members.value.map(m => m.strategy_id)
  if (ids.some(id => !id)) errs.push('有成员未选择策略')
  if (new Set(ids).size !== ids.length) errs.push('成员策略不能重复')
  if (members.value.some(m => !(m.weight > 0))) errs.push('成员权重必须大于 0')
  if (!totalOk.value) errs.push(`成员权重与现金权重之和须为 100%（当前 ${total.value.toFixed(1)}%）`)
  return errs
})

/** 从 modelValue 回显表单 */
function init(): void {
  const config = props.modelValue as Partial<CompositeConfigJson>
  members.value = (config.members ?? []).map(m => ({
    strategy_id: m.strategy_id,
    weight: Math.round(m.weight * 10000) / 100,
  }))
  if (members.value.length === 0) members.value = [{ strategy_id: '', weight: 50 }, { strategy_id: '', weight: 50 }]
  cashWeight.value = Math.round((config.cash_weight ?? 0) * 10000) / 100
  frequency.value = config.rebalance?.frequency ?? 'daily'
  dayOfWeekDeclared.value = typeof config.rebalance?.day_of_week === 'number'
  dayOfMonthDeclared.value = typeof config.rebalance?.day_of_month === 'number'
  dayOfWeek.value = config.rebalance?.day_of_week ?? 4
  dayOfMonth.value = config.rebalance?.day_of_month ?? 1
  weekParity.value = config.rebalance?.week_parity ?? 'even'
}

/** 生成 config_json */
function build(): Record<string, unknown> {
  const rebalance: Record<string, unknown> = { frequency: frequency.value }
  const weekly = frequency.value === 'weekly' || frequency.value === 'biweekly'
  // 后端缺省调仓日：周度/双周 4（周五）、月度 1；原配置未声明且未改动时保持省略
  if (weekly && (dayOfWeekDeclared.value || dayOfWeek.value !== 4)) rebalance.day_of_week = dayOfWeek.value
  if (frequency.value === 'biweekly') rebalance.week_parity = weekParity.value
  if (frequency.value === 'monthly' && (dayOfMonthDeclared.value || dayOfMonth.value !== 1)) {
    rebalance.day_of_month = dayOfMonth.value
  }
  const config: CompositeConfigJson = {
    kind: 'composite',
    schema_version: '2',
    members: members.value.map(m => ({ strategy_id: m.strategy_id, weight: Math.round(m.weight * 100) / 10000 })),
    rebalance: rebalance as CompositeConfigJson['rebalance'],
  }
  if (cashWeight.value > 0) config.cash_weight = Math.round(cashWeight.value * 100) / 10000
  return config as unknown as Record<string, unknown>
}

function addMember(): void {
  members.value.push({ strategy_id: '', weight: 0 })
}

function removeMember(index: number): void {
  members.value.splice(index, 1)
}

watch(
  [members, cashWeight, frequency, dayOfWeek, dayOfMonth, weekParity],
  () => {
    selfUpdating = true
    emit('update:modelValue', build())
    Promise.resolve().then(() => { selfUpdating = false })
  },
  { deep: true },
)

watch(
  () => props.modelValue,
  () => { if (!selfUpdating) init() },
)

onMounted(async () => {
  init()
  try {
    strategies.value = await fetchStrategies()
  } catch {
    strategies.value = []
  }
})

defineExpose({ errors })
</script>

<style scoped>
.composite-form { display: flex; flex-direction: column; gap: 12px; }
.member-table { display: flex; flex-direction: column; gap: 8px; }
.member-row, .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.member-select { flex: 1; min-width: 220px; }
.weight-input { width: 90px; }
.unit { color: var(--text-muted); }
.total.ok { color: var(--success, #2e7d32); }
.total.bad { color: var(--danger, #c62828); }
.form-errors { margin: 0; padding-left: 18px; color: var(--danger, #c62828); font-size: 13px; }
.btn-link { background: none; border: none; color: var(--accent); cursor: pointer; padding: 0; }
.btn-link:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
