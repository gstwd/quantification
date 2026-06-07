<template>
  <div class="page">
    <div class="page-header">
      <div class="breadcrumb">
        <RouterLink to="/backtests" class="back-link">← 回测中心</RouterLink>
      </div>
      <h1 class="page-title">新建回测</h1>
    </div>

    <div class="form-card">
      <div class="form-section">
        <label class="form-label">策略</label>
        <select v-model="form.strategy_id" class="form-select">
          <option value="" disabled>请选择策略</option>
          <option v-for="s in strategyStore.items" :key="s.strategy_id" :value="s.strategy_id">
            {{ s.display_name }} ({{ s.strategy_id }})
          </option>
        </select>
      </div>

      <div class="form-row">
        <div class="form-section">
          <label class="form-label">起始日期</label>
          <input v-model="form.start_date" type="date" class="form-input" />
        </div>
        <div class="form-section">
          <label class="form-label">结束日期</label>
          <input v-model="form.end_date" type="date" class="form-input" />
        </div>
      </div>

      <div class="form-section">
        <label class="form-label">标的范围</label>

        <!-- 策略限定指数范围提示 -->
        <div v-if="isUniverseLocked" class="scope-notice">
          <span class="scope-icon">🔒</span>
          <span>此策略专为以下指数设计，标的范围已锁定：</span>
          <span class="scope-codes">{{ strategyIndexCodes.join(', ') }}</span>
        </div>

        <div v-if="loadingStrategy" class="scope-loading">加载策略配置中...</div>

        <div class="radio-group">
          <label class="radio-label" :class="{ disabled: isUniverseLocked }">
            <input v-model="form.universe_mode" type="radio" value="all" :disabled="isUniverseLocked" />
            全部指数
          </label>
          <label class="radio-label">
            <input v-model="form.universe_mode" type="radio" value="subset" :disabled="isUniverseLocked" />
            指定指数子集
          </label>
        </div>
        <div v-if="form.universe_mode === 'subset'" class="etf-checkboxes">
          <label
            v-for="idx in indexes"
            :key="idx.index_code"
            class="checkbox-label"
            :class="{ disabled: isUniverseLocked }"
          >
            <input
              type="checkbox"
              :value="idx.index_code"
              :checked="form.index_codes.includes(idx.index_code)"
              :disabled="isUniverseLocked"
              @change="toggleIndex(idx.index_code)"
            />
            {{ idx.index_code }} {{ idx.index_name }}
          </label>
        </div>
      </div>

      <div class="form-section">
        <label class="form-label">回测模式 <HelpTip :text="configHelp('portfolio')" /></label>
        <div class="radio-group">
          <label class="radio-label">
            <input v-model="form.backtest_mode" type="radio" value="signal" />
            信号评分模式（HIGH/MID/LOW 信号等级）
          </label>
          <label class="radio-label">
            <input v-model="form.backtest_mode" type="radio" value="allocation" />
            资产配置模式（择时 → 轮动 → 仓位分配）
          </label>
        </div>
      </div>

      <div v-if="form.backtest_mode === 'signal'" class="form-section">
        <label class="form-label">组合加权方式</label>
        <div class="radio-group">
          <label class="radio-label">
            <input v-model="form.weighting" type="radio" value="equal" />
            等权（HIGH 信号指数等权持有）
          </label>
          <label class="radio-label">
            <input v-model="form.weighting" type="radio" value="signal_weighted" />
            信号加权（按信号得分加权）
          </label>
        </div>
      </div>

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
          {{ submitting ? '提交中...' : '开始回测' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { fetchBenchmarkIndexes } from '../api/market_data'
import { fetchStrategyDetail } from '../api/strategies'
import { useBacktestStore } from '../stores/backtests'
import { useStrategyStore } from '../stores/strategies'
import type { BenchmarkIndex } from '../types/api'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'

/** 获取配置描述的快捷方法 */
function configHelp(key: string): string {
  return getIndicator('strategy_config', key)?.description ?? ''
}

const router = useRouter()
const store = useBacktestStore()
const strategyStore = useStrategyStore()

const indexes = ref<BenchmarkIndex[]>([])

const form = reactive({
  strategy_id: '',
  start_date: '',
  end_date: '',
  universe_mode: 'all' as 'all' | 'subset',
  index_codes: [] as string[],
  weighting: 'equal' as 'equal' | 'signal_weighted',
  backtest_mode: 'signal' as 'signal' | 'allocation',
  enable_benchmark: true,
  benchmark_index_code: '000300',
})

const submitting = ref(false)
const error = ref('')

/** 当前选中策略的 index_codes 限定（来自 config_json）。非空时表示策略有标的范围限定。 */
const strategyIndexCodes = ref<string[]>([])
/** 正在加载策略详情 */
const loadingStrategy = ref(false)
/** 策略是否限定了标的范围 */
const isUniverseLocked = computed(() => strategyIndexCodes.value.length > 0)

const isValid = computed(() =>
  form.strategy_id !== '' &&
  form.start_date !== '' &&
  form.end_date !== '' &&
  form.start_date <= form.end_date &&
  (form.universe_mode === 'all' || form.index_codes.length > 0),
)

function toggleIndex(code: string) {
  if (isUniverseLocked.value) return
  const idx = form.index_codes.indexOf(code)
  if (idx === -1) form.index_codes.push(code)
  else form.index_codes.splice(idx, 1)
}

/** 监听策略选择，获取策略的 index_codes 限定 */
watch(
  () => form.strategy_id,
  async (strategyId) => {
    strategyIndexCodes.value = []
    if (!strategyId) return

    loadingStrategy.value = true
    try {
      const detail = await fetchStrategyDetail(strategyId)
      const codes = (detail.config_json as Record<string, unknown>)?.index_codes as string[] | undefined
      if (codes && codes.length > 0) {
        strategyIndexCodes.value = codes
        // 强制设为子集模式，使用策略限定的指数
        form.universe_mode = 'subset'
        form.index_codes = [...codes]
      }
    } catch {
      strategyIndexCodes.value = []
    } finally {
      loadingStrategy.value = false
    }
  },
  { immediate: true },
)

async function submit() {
  error.value = ''
  submitting.value = true
  try {
    const summary = await store.submit({
      strategy_id: form.strategy_id,
      start_date: form.start_date,
      end_date: form.end_date,
      universe_mode: form.universe_mode,
      index_codes: form.index_codes,
      weighting: form.weighting,
      backtest_mode: form.backtest_mode,
      enable_benchmark: form.enable_benchmark,
      benchmark_index_code: form.benchmark_index_code,
    })
    router.push(`/backtests/${summary.backtest_id}`)
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

.etf-checkboxes {
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

/* 策略标的范围锁定提示 */
.scope-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: rgba(59,130,246,0.08);
  border: 1px solid rgba(59,130,246,0.25);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: var(--text-muted);
  flex-wrap: wrap;
}
.scope-icon { font-size: 14px; }
.scope-codes { font-weight: 600; color: var(--accent); }
.scope-loading { font-size: 12px; color: var(--text-muted); padding: 4px 0; }

/* 禁用态 */
.radio-label.disabled, .checkbox-label.disabled { opacity: 0.5; cursor: not-allowed; }

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
