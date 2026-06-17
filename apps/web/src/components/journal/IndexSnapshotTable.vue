<template>
  <div class="snapshot-table-wrapper">
    <div class="table-toolbar">
      <h3>指数行情快照</h3>
      <button class="btn-secondary btn-sm" :disabled="loading" @click="$emit('refresh')">
        {{ loading ? '刷新中...' : '刷新快照' }}
      </button>
    </div>
    <div class="table-scroll">
      <table class="data-table" v-if="snapshots.length > 0">
        <thead>
          <tr>
            <th>指数</th>
            <th>收盘价</th>
            <th class="sortable" @click="toggleSort('change_pct')">
              涨跌幅
              <span v-if="sortField === 'change_pct'" class="sort-icon">{{ sortDir === 'asc' ? '↑' : '↓' }}</span>
            </th>
            <th>5日</th>
            <th>20日</th>
            <th>60日</th>
            <th>MA20偏离</th>
            <th>MA60偏离</th>
            <th>波动率</th>
            <th>60日回撤</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in sortedSnapshots" :key="row.id">
            <td>
              <span class="index-name">{{ row.index_name }}</span>
              <span class="index-code">{{ row.index_code }}</span>
            </td>
            <td class="num">{{ fmtNum(row.close_price, 2) }}</td>
            <td class="num" :class="colorPct(row.change_pct)">{{ fmtPct(row.change_pct) }}</td>
            <td class="num" :class="colorPct(row.return_5d)">{{ fmtPct(row.return_5d) }}</td>
            <td class="num" :class="colorPct(row.return_20d)">{{ fmtPct(row.return_20d) }}</td>
            <td class="num" :class="colorPct(row.return_60d)">{{ fmtPct(row.return_60d) }}</td>
            <td class="num" :class="colorPct(row.ma_20d_deviation)">{{ fmtPct(row.ma_20d_deviation) }}</td>
            <td class="num" :class="colorPct(row.ma_60d_deviation)">{{ fmtPct(row.ma_60d_deviation) }}</td>
            <td class="num">{{ fmtPct(row.volatility_20d) }}</td>
            <td class="num" :class="colorPct(row.max_drawdown_60d)">{{ fmtPct(row.max_drawdown_60d) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty-state">暂无快照数据</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { IndexSnapshotRow } from '../../types/api'

const props = defineProps<{
  snapshots: IndexSnapshotRow[]
  loading: boolean
}>()

defineEmits<{
  refresh: []
}>()

const sortField = ref<string>('')
const sortDir = ref<'asc' | 'desc'>('desc')

function toggleSort(field: string): void {
  if (sortField.value === field) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortField.value = field
    sortDir.value = 'desc'
  }
}

const sortedSnapshots = computed(() => {
  if (!sortField.value) return props.snapshots
  const dir = sortDir.value === 'asc' ? 1 : -1
  return [...props.snapshots].sort((a, b) => {
    const va = (a as any)[sortField.value] ?? 0
    const vb = (b as any)[sortField.value] ?? 0
    return (va - vb) * dir
  })
})

function fmtNum(v: number | null, decimals: number): string {
  if (v === null || v === undefined) return '--'
  return v.toFixed(decimals)
}

function fmtPct(v: number | null): string {
  if (v === null || v === undefined) return '--'
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

function colorPct(v: number | null): string {
  if (v === null || v === undefined) return ''
  if (v > 0) return 'positive'
  if (v < 0) return 'negative'
  return ''
}
</script>

<style scoped>
.snapshot-table-wrapper {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
}
.table-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.table-toolbar h3 {
  margin: 0;
  font-size: 15px;
  color: var(--text);
}
.btn-secondary {
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  cursor: pointer;
  font-size: 12px;
}
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-secondary:hover:not(:disabled) {
  background: var(--surface-2);
}
.table-scroll { overflow-x: auto; }
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.data-table th {
  padding: 6px 8px;
  text-align: right;
  color: var(--text-muted);
  font-weight: 500;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.data-table th:first-child { text-align: left; }
.data-table th.sortable {
  cursor: pointer;
  user-select: none;
}
.data-table th.sortable:hover { color: var(--accent); }
.sort-icon { font-size: 10px; }
.data-table td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
}
.data-table td:first-child { text-align: left; }
.data-table td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-family: 'Courier New', monospace;
}
.index-name { display: block; font-size: 13px; }
.index-code { display: block; font-size: 10px; color: var(--text-muted); margin-top: 1px; }
.positive { color: #22c55e; }
.negative { color: #ef4444; }
.empty-state {
  text-align: center;
  padding: 32px;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
