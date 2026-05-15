<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">运行记录</h1>
      <div class="actions">
        <button class="btn btn-secondary" :disabled="triggering" @click="trigger('universe')">刷新 ETF 池</button>
        <button class="btn btn-secondary" :disabled="triggering" @click="trigger('ingest')">触发数据摄取</button>
      </div>
    </div>

    <div class="table-wrap">
      <div v-if="loading" class="loading">加载中...</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>Run ID</th>
            <th>类型</th>
            <th>策略</th>
            <th>交易日</th>
            <th>状态</th>
            <th>开始时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in runs" :key="item.run_id">
            <td class="mono text-muted">{{ item.run_id.slice(0, 8) }}…</td>
            <td><span class="type-badge">{{ item.run_type }}</span></td>
            <td class="text-muted mono">{{ item.strategy_id ?? '—' }}</td>
            <td class="text-muted mono">{{ item.trade_date ?? '—' }}</td>
            <td><span class="status-badge" :class="'status-' + item.status">{{ item.status }}</span></td>
            <td class="text-muted">{{ formatTime(item.started_at) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!loading && runs.length === 0" class="empty">暂无运行记录</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchRuns, triggerDailyIngest, triggerUniverseRefresh } from '../api/runs'
import type { ResearchRunSummary } from '../types/api'

const runs = ref<ResearchRunSummary[]>([])
const loading = ref(false)
const triggering = ref(false)

function formatTime(ts: string | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function trigger(type: 'universe' | 'ingest') {
  triggering.value = true
  try {
    if (type === 'universe') await triggerUniverseRefresh()
    else await triggerDailyIngest()
    await load()
  } finally {
    triggering.value = false
  }
}

async function load() {
  loading.value = true
  try { runs.value = await fetchRuns() } finally { loading.value = false }
}

onMounted(load)
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }

.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }
.actions { display: flex; gap: 8px; }

.btn {
  padding: 7px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  font-size: 13px;
  font-weight: 500;
  transition: background 0.15s, border-color 0.15s;
}
.btn-secondary { background: var(--surface); color: var(--text); }
.btn-secondary:hover { background: var(--surface-2); border-color: var(--accent); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.table-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.loading, .empty { padding: 40px; text-align: center; color: var(--text-muted); }

.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  text-align: left;
  padding: 10px 20px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: rgba(0,0,0,0.1);
}
.data-table td { padding: 12px 20px; border-bottom: 1px solid rgba(51,65,85,0.5); }
.data-table tr:last-child td { border-bottom: none; }

.mono { font-family: monospace; font-size: 12px; }
.text-muted { color: var(--text-muted); }

.type-badge {
  font-size: 11px;
  background: var(--surface-2);
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}
.status-pending { background: rgba(148,163,184,0.15); color: var(--text-muted); }
.status-running { background: rgba(59,130,246,0.15); color: #60a5fa; }
.status-completed { background: rgba(34,197,94,0.15); color: var(--success); }
.status-failed { background: rgba(239,68,68,0.15); color: var(--danger); }
</style>
