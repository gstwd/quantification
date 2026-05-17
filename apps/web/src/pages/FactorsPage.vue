<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">因子中心</h1>
      <button class="compute-btn" :disabled="computing" @click="compute">
        {{ computing ? '计算中...' : '触发今日计算' }}
      </button>
    </div>

    <div v-if="loading" class="empty">加载中...</div>
    <div v-else-if="specs.length === 0" class="empty">暂无因子数据</div>
    <div v-else class="grid">
      <RouterLink
        v-for="spec in specs"
        :key="spec.factor_id"
        :to="`/factors/${spec.factor_id}`"
        class="factor-card"
      >
        <div class="card-top">
          <span class="factor-id">{{ spec.factor_id }}</span>
          <span class="chip" :class="`chip-${spec.category}`">{{ CATEGORY_LABELS[spec.category] ?? spec.category }}</span>
        </div>
        <h3 class="factor-name">{{ spec.name }}</h3>
        <p class="factor-desc">{{ spec.description }}</p>
        <div class="card-footer">
          <span class="version">v{{ spec.version }}</span>
          <span class="required">{{ spec.required_data.join(', ') }}</span>
        </div>
      </RouterLink>
    </div>

    <div v-if="computeMsg" class="toast">{{ computeMsg }}</div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchFactorSpecs, triggerFactorCompute } from '../api/factors'
import type { FactorSpec } from '../types/api'

const specs = ref<FactorSpec[]>([])
const loading = ref(false)
const computing = ref(false)
const computeMsg = ref('')

const CATEGORY_LABELS: Record<string, string> = {
  volume: '量能',
  momentum: '动量',
  volatility: '波动率',
  flow: '份额流',
  valuation: '估值',
}

/** 触发当日因子计算 */
async function compute() {
  computing.value = true
  computeMsg.value = ''
  try {
    const today = new Date().toISOString().slice(0, 10)
    const res = await triggerFactorCompute(today)
    computeMsg.value = res.message
  } catch {
    computeMsg.value = '触发失败，请稍后重试'
  } finally {
    computing.value = false
    setTimeout(() => { computeMsg.value = '' }, 5000)
  }
}

onMounted(async () => {
  loading.value = true
  try {
    specs.value = await fetchFactorSpecs()
  } catch {
    specs.value = []
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }

.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }

.compute-btn {
  background: rgba(59, 130, 246, 0.15);
  border: 1px solid rgba(59, 130, 246, 0.3);
  color: var(--accent);
  border-radius: var(--radius-sm);
  padding: 7px 16px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s;
}
.compute-btn:hover:not(:disabled) { background: rgba(59, 130, 246, 0.25); }
.compute-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.empty { padding: 60px; text-align: center; color: var(--text-muted); }

.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }

.factor-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  text-decoration: none;
  color: inherit;
  transition: border-color 0.15s;
}
.factor-card:hover { border-color: var(--accent); }

.card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.factor-id {
  font-family: monospace;
  font-size: 11px;
  color: var(--accent);
  background: rgba(59, 130, 246, 0.1);
  padding: 2px 8px;
  border-radius: 20px;
}

.chip { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; white-space: nowrap; }
.chip-volume    { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.chip-momentum  { background: rgba(34, 197, 94, 0.12);  color: #4ade80; }
.chip-volatility{ background: rgba(239, 68, 68, 0.12);  color: #f87171; }
.chip-flow      { background: rgba(139, 92, 246, 0.15); color: #a78bfa; }
.chip-valuation { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }

.factor-name { font-size: 16px; font-weight: 600; }
.factor-desc { font-size: 13px; color: var(--text-muted); line-height: 1.6; flex: 1; }

.card-footer { display: flex; align-items: center; justify-content: space-between; margin-top: auto; }
.version  { font-size: 11px; color: var(--text-muted); }
.required { font-size: 11px; color: var(--text-muted); font-family: monospace; }

.toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 18px;
  font-size: 13px;
  color: var(--accent);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.35);
  animation: slide-up 0.2s ease;
}

@keyframes slide-up {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}
</style>
