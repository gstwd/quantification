<template>
  <div class="page">
    <div class="page-header">
      <div class="breadcrumb">
        <RouterLink to="/backtests" class="back-link">← 回测中心</RouterLink>
      </div>
      <h1 class="page-title">新建策略对比</h1>
    </div>

    <div class="form-card">
      <!-- 对比名称 -->
      <div class="form-section">
        <label class="form-label">对比名称（可选）</label>
        <input v-model="form.name" class="form-input" placeholder="例如：动量 vs 价值策略对比" />
      </div>

      <!-- 策略 A -->
      <div class="form-section">
        <label class="form-label">策略 A</label>
        <select v-model="form.strategy_a_id" class="form-select" @change="onStrategyAChange">
          <option value="" disabled>请选择策略 A</option>
          <option v-for="s in strategyStore.items" :key="s.strategy_id" :value="s.strategy_id">
            {{ s.display_name }} ({{ s.strategy_id }})
          </option>
        </select>
      </div>

      <!-- 策略 A 标的范围 -->
      <div v-if="form.strategy_a_id" class="form-section">
        <label class="form-label">策略 A 标的范围</label>
        <!-- 策略已配置标的范围 → 只读展示 -->
        <div v-if="strategyAIndexCodes.length > 0" class="readonly-codes">
          <span class="hint">策略已限定标的范围，不可修改：</span>
          <div class="code-chips">
            <span v-for="code in strategyAIndexCodes" :key="code" class="code-chip">{{ code }}</span>
          </div>
        </div>
        <!-- 策略未配置 → 可选择 -->
        <template v-else>
          <div class="radio-group">
            <label class="radio-label">
              <input v-model="aUniverseMode" type="radio" value="all" />
              全部指数
            </label>
            <label class="radio-label">
              <input v-model="aUniverseMode" type="radio" value="subset" />
              指定指数子集
            </label>
          </div>
          <div v-if="aUniverseMode === 'subset'" class="index-checkboxes">
            <label
              v-for="idx in indexes"
              :key="idx.index_code"
              class="checkbox-label"
            >
              <input
                type="checkbox"
                :value="idx.index_code"
                :checked="form.a_index_codes.includes(idx.index_code)"
                @change="toggleAIndex(idx.index_code)"
              />
              {{ idx.index_code }} {{ idx.index_name }}
            </label>
          </div>
        </template>
      </div>

      <!-- 策略 B -->
      <div class="form-section">
        <label class="form-label">策略 B</label>
        <select v-model="form.strategy_b_id" class="form-select" @change="onStrategyBChange">
          <option value="" disabled>请选择策略 B</option>
          <option v-for="s in strategyStore.items" :key="s.strategy_id" :value="s.strategy_id">
            {{ s.display_name }} ({{ s.strategy_id }})
          </option>
        </select>
        <div v-if="sameStrategyError" class="error-msg">请选择两个不同的策略</div>
      </div>

      <!-- 策略 B 标的范围 -->
      <div v-if="form.strategy_b_id" class="form-section">
        <label class="form-label">策略 B 标的范围</label>
        <!-- 策略已配置标的范围 → 只读展示 -->
        <div v-if="strategyBIndexCodes.length > 0" class="readonly-codes">
          <span class="hint">策略已限定标的范围，不可修改：</span>
          <div class="code-chips">
            <span v-for="code in strategyBIndexCodes" :key="code" class="code-chip">{{ code }}</span>
          </div>
        </div>
        <!-- 策略未配置 → 可选择 -->
        <template v-else>
          <div class="radio-group">
            <label class="radio-label">
              <input v-model="bUniverseMode" type="radio" value="all" />
              全部指数
            </label>
            <label class="radio-label">
              <input v-model="bUniverseMode" type="radio" value="subset" />
              指定指数子集
            </label>
          </div>
          <div v-if="bUniverseMode === 'subset'" class="index-checkboxes">
            <label
              v-for="idx in indexes"
              :key="idx.index_code"
              class="checkbox-label"
            >
              <input
                type="checkbox"
                :value="idx.index_code"
                :checked="form.b_index_codes.includes(idx.index_code)"
                @change="toggleBIndex(idx.index_code)"
              />
              {{ idx.index_code }} {{ idx.index_name }}
            </label>
          </div>
        </template>
      </div>

      <!-- 日期范围 -->
      <div class="form-section">
        <label class="form-label">回测区间</label>
        <div class="date-presets">
          <button
            v-for="p in presets"
            :key="p.label"
            class="preset-btn"
            :class="{ active: form.start_date === p.start && form.end_date === p.end }"
            type="button"
            @click="applyPreset(p)"
          >{{ p.label }}</button>
        </div>
        <div class="form-row">
          <div class="form-section">
            <label class="form-label-sub">起始日期</label>
            <input v-model="form.start_date" type="date" class="form-input" />
          </div>
          <div class="form-section">
            <label class="form-label-sub">结束日期</label>
            <input v-model="form.end_date" type="date" class="form-input" />
          </div>
        </div>
      </div>

      <!-- 基准对比 -->
      <div class="form-section">
        <label class="form-label">基准对比 <HelpTip :text="configHelp('benchmark')" /></label>
        <div class="radio-group">
          <label class="radio-label">
            <input v-model="form.enable_benchmark" type="radio" :value="true" />
            启用基准对比（买入持有参考基准）
          </label>
          <label class="radio-label">
            <input v-model="form.enable_benchmark" type="radio" :value="false" />
            不启用
          </label>
        </div>
        <div v-if="form.enable_benchmark" class="form-row" style="margin-top: 8px;">
          <div class="form-section">
            <label class="form-label">基准指数</label>
            <select v-model="form.benchmark_index_code" class="form-select">
              <option v-for="idx in indexes" :key="idx.index_code" :value="idx.index_code">
                {{ idx.index_code }} {{ idx.index_name }}
              </option>
            </select>
          </div>
        </div>
      </div>

      <div v-if="error" class="error-msg">{{ error }}</div>

      <div class="form-actions">
        <RouterLink to="/backtests" class="btn btn-secondary">取消</RouterLink>
        <button class="btn btn-primary" :disabled="submitting || !isValid" @click="submit">
          {{ submitting ? '提交中...' : '开始对比' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { fetchBenchmarkIndexes } from '../api/market_data'
import { useBacktestStore } from '../stores/backtests'
import { useStrategyStore } from '../stores/strategies'
import type { BenchmarkIndex } from '../types/api'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'
import { getDatePresets } from '../utils/datePresets'
import type { DatePreset } from '../utils/datePresets'

/** 获取配置描述的快捷方法 */
function configHelp(key: string): string {
  return getIndicator('strategy_config', key)?.description ?? ''
}

const router = useRouter()
const store = useBacktestStore()
const strategyStore = useStrategyStore()

const indexes = ref<BenchmarkIndex[]>([])

const form = reactive({
  name: '',
  strategy_a_id: '',
  strategy_b_id: '',
  start_date: '',
  end_date: '',
  a_index_codes: [] as string[],
  b_index_codes: [] as string[],
  enable_benchmark: true,
  benchmark_index_code: '000300',
})

const submitting = ref(false)
const error = ref('')
const aUniverseMode = ref<'all' | 'subset'>('all')
const bUniverseMode = ref<'all' | 'subset'>('all')

/** 日期快捷预设 */
const presets = getDatePresets()

/** 应用日期预设 */
function applyPreset(p: DatePreset) {
  form.start_date = p.start
  form.end_date = p.end
}

const sameStrategyError = computed(
  () =>
    form.strategy_a_id !== '' &&
    form.strategy_b_id !== '' &&
    form.strategy_a_id === form.strategy_b_id,
)

/** 策略 A 已配置的 index_codes（来自策略自身） */
const strategyAIndexCodes = computed(() => {
  if (!form.strategy_a_id) return []
  const s = strategyStore.items.find((it) => it.strategy_id === form.strategy_a_id)
  return s?.index_codes ?? []
})

/** 策略 B 已配置的 index_codes（来自策略自身） */
const strategyBIndexCodes = computed(() => {
  if (!form.strategy_b_id) return []
  const s = strategyStore.items.find((it) => it.strategy_id === form.strategy_b_id)
  return s?.index_codes ?? []
})

const isValid = computed(
  () =>
    form.strategy_a_id !== '' &&
    form.strategy_b_id !== '' &&
    form.strategy_a_id !== form.strategy_b_id &&
    form.start_date !== '' &&
    form.end_date !== '' &&
    form.start_date <= form.end_date &&
    // 每个策略如果未配置标的范围且选了 subset，则必须至少选一个指数
    (strategyAIndexCodes.value.length > 0 || aUniverseMode.value === 'all' || form.a_index_codes.length > 0) &&
    (strategyBIndexCodes.value.length > 0 || bUniverseMode.value === 'all' || form.b_index_codes.length > 0),
)

function toggleAIndex(code: string) {
  const idx = form.a_index_codes.indexOf(code)
  if (idx === -1) form.a_index_codes.push(code)
  else form.a_index_codes.splice(idx, 1)
}

function toggleBIndex(code: string) {
  const idx = form.b_index_codes.indexOf(code)
  if (idx === -1) form.b_index_codes.push(code)
  else form.b_index_codes.splice(idx, 1)
}

/** 切换策略 A 时重置标的范围选择 */
function onStrategyAChange() {
  form.a_index_codes = []
  aUniverseMode.value = 'all'
}

/** 切换策略 B 时重置标的范围选择 */
function onStrategyBChange() {
  form.b_index_codes = []
  bUniverseMode.value = 'all'
}

/** 当用户切换为"全部指数"时清空已选指数 */
watch(aUniverseMode, (val) => {
  if (val === 'all') form.a_index_codes = []
})

watch(bUniverseMode, (val) => {
  if (val === 'all') form.b_index_codes = []
})

async function submit() {
  error.value = ''
  submitting.value = true
  try {
    const summary = await store.submitComparison({
      strategy_a_id: form.strategy_a_id,
      strategy_b_id: form.strategy_b_id,
      start_date: form.start_date,
      end_date: form.end_date,
      a_index_codes: form.a_index_codes,
      b_index_codes: form.b_index_codes,
      enable_benchmark: form.enable_benchmark,
      benchmark_index_code: form.benchmark_index_code,
      name: form.name || null,
    })
    router.push(`/backtests/comparison/${summary.comparison_id}`)
  } catch (e: unknown) {
    error.value = e instanceof Error ? e.message : '提交失败，请重试'
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  if (strategyStore.items.length === 0) strategyStore.loadAll()
  try {
    indexes.value = await fetchBenchmarkIndexes()
  } catch {
    indexes.value = []
  }
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-header { display: flex; flex-direction: column; gap: 6px; }
.breadcrumb { font-size: 13px; }
.back-link { color: var(--text-muted); text-decoration: none; }
.back-link:hover { color: var(--accent); }
.page-title { font-size: 22px; font-weight: 700; }

.form-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 28px;
  display: flex;
  flex-direction: column;
  gap: 24px;
  max-width: 640px;
}

.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

.form-section { display: flex; flex-direction: column; gap: 8px; }
.form-label { font-size: 13px; font-weight: 600; color: var(--text); }
.form-label-sub { font-size: 12px; font-weight: 500; color: var(--text-muted); }

/* 日期快捷选择按钮 */
.date-presets { display: flex; gap: 6px; flex-wrap: wrap; }
.preset-btn {
  padding: 3px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.preset-btn:hover { background: var(--surface); color: var(--text); border-color: var(--accent); }
.preset-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }

.form-select, .form-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  font-size: 13px;
  color: var(--text);
  outline: none;
  transition: border-color 0.15s;
}
.form-select:focus, .form-input:focus { border-color: var(--accent); }

.radio-group { display: flex; flex-direction: column; gap: 8px; }
.radio-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-muted);
  cursor: pointer;
}
.radio-label input { accent-color: var(--accent); }

.index-checkboxes {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 200px;
  overflow-y: auto;
}
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-muted);
  cursor: pointer;
}
.checkbox-label input { accent-color: var(--accent); }

/* 只读标的范围展示 */
.readonly-codes {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.readonly-codes .hint {
  font-size: 12px;
  color: var(--text-muted);
}
.code-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.code-chip {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
  font-family: monospace;
  background: rgba(59,130,246,0.1);
  color: #3B82F6;
  border: 1px solid rgba(59,130,246,0.25);
}

.error-msg {
  padding: 10px 14px;
  background: rgba(239,68,68,0.1);
  border: 1px solid rgba(239,68,68,0.3);
  border-radius: var(--radius-sm);
  color: var(--danger);
  font-size: 13px;
}

.form-actions { display: flex; gap: 10px; justify-content: flex-end; }

.btn {
  padding: 8px 18px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border);
  transition: background 0.15s, opacity 0.15s;
}
.btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-primary:hover:not(:disabled) { opacity: 0.9; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { background: var(--surface); color: var(--text); }
.btn-secondary:hover { background: var(--surface-2); }
</style>
