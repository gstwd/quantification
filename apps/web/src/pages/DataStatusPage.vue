<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">数据状态</h1>
      <button class="btn btn-primary" :disabled="triggering" @click="triggerIngest">
        {{ triggering ? '触发中...' : '触发数据摄取' }}
      </button>
    </div>

    <div v-if="loading" class="loading">加载中...</div>
    <div v-else class="status-grid">
      <div v-for="(val, key) in status" :key="key" class="status-card">
        <div class="status-key">{{ key }}</div>
        <div class="status-val">{{ formatVal(val) }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchSystemStatus, triggerDailyIngest } from '../api/runs'

const status = ref<Record<string, unknown>>({})
const loading = ref(false)
const triggering = ref(false)

function formatVal(val: unknown): string {
  if (val === null || val === undefined) return '—'
  if (typeof val === 'object') return JSON.stringify(val)
  return String(val)
}

async function triggerIngest() {
  triggering.value = true
  try {
    await triggerDailyIngest()
    status.value = await fetchSystemStatus()
  } finally {
    triggering.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try { status.value = await fetchSystemStatus() } finally { loading.value = false }
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }

.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }

.btn {
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  border: none;
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s;
}
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: var(--accent-hover); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.loading { padding: 60px; text-align: center; color: var(--text-muted); }

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
}

.status-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
}

.status-key {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}

.status-val {
  font-size: 14px;
  font-weight: 500;
  word-break: break-all;
  font-family: monospace;
}
</style>
