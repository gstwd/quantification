<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">研究总览</h1>
      <span class="page-subtitle">{{ systemStatus.latest_trade_date ? '最新交易日 ' + systemStatus.latest_trade_date : '' }}</span>
    </div>

    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-label">ETF 总数</div>
        <div class="stat-value">{{ etfStore.items.length || '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">活跃策略</div>
        <div class="stat-value">{{ strategyStore.items.length || '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">今日信号</div>
        <div class="stat-value">{{ signals.length }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">数据频率</div>
        <div class="stat-value">{{ systemStatus.frequency || 'daily' }}</div>
      </div>
    </div>

    <div class="section">
      <div class="section-header">
        <h2 class="section-title">最新三因子信号</h2>
        <span class="section-badge">three_factor_guard</span>
      </div>
      <div v-if="signalStore.loading" class="loading">加载中...</div>
      <div v-else-if="signals.length === 0" class="empty">暂无信号数据</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>ETF 代码</th>
            <th>信号分数</th>
            <th>等级</th>
            <th>标签</th>
            <th>交易日</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in signals" :key="row.etf_code">
            <td>
              <RouterLink :to="`/etfs/${row.etf_code}`" class="code-link">{{ row.etf_code }}</RouterLink>
            </td>
            <td>
              <div class="score-cell">
                <div class="score-bar-bg">
                  <div class="score-bar" :style="{ width: row.signal_score + '%', background: scoreColor(row.signal_level) }"></div>
                </div>
                <span class="score-num">{{ row.signal_score.toFixed(1) }}</span>
              </div>
            </td>
            <td>
              <span class="badge" :class="'badge-' + row.signal_level.toLowerCase()">{{ row.signal_level }}</span>
            </td>
            <td class="text-muted">{{ row.signal_label }}</td>
            <td class="text-muted mono">{{ row.trade_date }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="status-strip" v-if="Object.keys(systemStatus).length">
      <div v-for="(val, key) in statusChips" :key="key" class="status-chip">
        <span class="chip-key">{{ key }}</span>
        <span class="chip-val">{{ val }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchSystemStatus } from '../api/runs'
import { useEtfStore } from '../stores/etfs'
import { useSignalStore } from '../stores/signals'
import { useStrategyStore } from '../stores/strategies'

const systemStatus = ref<Record<string, unknown>>({})
const signalStore = useSignalStore()
const etfStore = useEtfStore()
const strategyStore = useStrategyStore()
const signals = computed(() => signalStore.items)

const statusChips = computed(() => {
  const s = systemStatus.value as Record<string, unknown>
  const keys = ['asset_scope', 'frequency', 'database'] as const
  const result: Record<string, string> = {}
  for (const k of keys) {
    if (s[k] !== undefined) result[k] = String(s[k])
  }
  return result
})

function scoreColor(level: string): string {
  if (level === 'HIGH') return 'var(--signal-high)'
  if (level === 'MID') return 'var(--signal-mid)'
  return 'var(--signal-low)'
}

onMounted(async () => {
  systemStatus.value = await fetchSystemStatus()
  await Promise.all([
    signalStore.loadLatest('three_factor_guard'),
    etfStore.loadAll(),
    strategyStore.loadAll(),
  ])
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 24px; }

.page-header { display: flex; align-items: baseline; gap: 12px; }
.page-title { font-size: 22px; font-weight: 700; color: var(--text); }
.page-subtitle { font-size: 13px; color: var(--text-muted); }

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}

.stat-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--text); }

.section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}

.section-title { font-size: 15px; font-weight: 600; }
.section-badge {
  font-size: 11px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
}

.loading, .empty { padding: 32px 20px; color: var(--text-muted); text-align: center; }

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
}
.data-table td { padding: 12px 20px; border-bottom: 1px solid rgba(51, 65, 85, 0.5); }
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: rgba(255,255,255,0.02); }

.code-link { color: var(--accent); font-family: monospace; font-size: 13px; }
.code-link:hover { text-decoration: underline; }

.score-cell { display: flex; align-items: center; gap: 10px; }
.score-bar-bg { flex: 1; height: 6px; background: var(--surface-2); border-radius: 3px; overflow: hidden; max-width: 100px; }
.score-bar { height: 100%; border-radius: 3px; transition: width 0.3s; }
.score-num { font-size: 13px; font-weight: 600; color: var(--text); min-width: 36px; }

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.badge-high { background: rgba(34, 197, 94, 0.15); color: var(--signal-high); }
.badge-mid { background: rgba(245, 158, 11, 0.15); color: var(--signal-mid); }
.badge-low { background: rgba(148, 163, 184, 0.15); color: var(--signal-low); }

.text-muted { color: var(--text-muted); }
.mono { font-family: monospace; font-size: 12px; }

.status-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.status-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 4px 12px;
  font-size: 12px;
}

.chip-key { color: var(--text-muted); }
.chip-val { color: var(--text); font-weight: 500; }
</style>
