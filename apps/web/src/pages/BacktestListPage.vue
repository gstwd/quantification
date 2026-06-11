<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">回测中心</h1>
      <RouterLink to="/backtests/new" class="btn btn-primary">新建回测</RouterLink>
    </div>

    <div class="table-wrap">
      <div v-if="loading" class="loading">加载中...</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>策略</th>
            <th>日期范围</th>
            <th>状态</th>
            <th>累计收益</th>
            <th>年化收益</th>
            <th>最大回撤</th>
            <th>夏普</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in store.items"
            :key="item.backtest_id"
            class="clickable"
            @click="$router.push(`/backtests/${item.backtest_id}`)"
          >
            <td class="mono">{{ item.strategy_id }}</td>
            <td class="text-muted">{{ item.start_date }} ~ {{ item.end_date }}</td>
            <td>
              <span class="status-badge" :class="'status-' + item.status">{{ statusLabel(item.status) }}</span>
              <span v-if="item.status === 'running' && item.progress > 0" class="progress-inline">
                <span class="progress-bar-bg">
                  <span class="progress-bar-fill" :style="{ width: item.progress + '%' }"></span>
                </span>
                <span class="progress-pct">{{ item.progress }}%</span>
              </span>
            </td>
            <td :class="returnClass(item.metrics?.cumulative_return_pct)">
              {{ item.metrics ? formatPct(item.metrics.cumulative_return_pct) : '—' }}
            </td>
            <td :class="returnClass(item.metrics?.annualized_return_pct)">
              {{ item.metrics ? formatPct(item.metrics.annualized_return_pct) : '—' }}
            </td>
            <td class="danger">{{ item.metrics ? formatPct(item.metrics.max_drawdown_pct) : '—' }}</td>
            <td>{{ item.metrics ? item.metrics.sharpe_ratio.toFixed(2) : '—' }}</td>
            <td class="text-muted">{{ formatTime(item.created_at) }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="!loading && store.items.length === 0" class="empty">暂无回测记录，点击「新建回测」开始</div>
    </div>

    <div v-if="store.total > pageSize" class="pagination">
      <button class="page-btn" :disabled="offset === 0" @click="goPage(offset - pageSize)">上一页</button>
      <span class="page-info">{{ offset + 1 }}–{{ Math.min(offset + pageSize, store.total) }} / 共 {{ store.total }} 条</span>
      <button class="page-btn" :disabled="offset + pageSize >= store.total" @click="goPage(offset + pageSize)">下一页</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { useBacktestStore } from '../stores/backtests'

const store = useBacktestStore()
const loading = ref(false)
const offset = ref(0)
const pageSize = 50

function statusLabel(status: string): string {
  const map: Record<string, string> = { pending: '待执行', running: '执行中', success: '成功', failed: '失败' }
  return map[status] ?? status
}

function formatPct(v: number): string {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

function returnClass(v?: number): string {
  if (v === undefined || v === null) return ''
  return v >= 0 ? 'success' : 'danger'
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function goPage(newOffset: number) {
  offset.value = newOffset
  loading.value = true
  try { await store.loadAll(newOffset, pageSize) } finally { loading.value = false }
}

onMounted(async () => {
  loading.value = true
  try { await store.loadAll(0, pageSize) } finally { loading.value = false }
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }

.btn {
  padding: 7px 16px;
  border-radius: var(--radius-sm);
  border: none;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
}
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { opacity: 0.9; }

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
  padding: 10px 16px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: rgba(0,0,0,0.1);
}
.data-table td { padding: 12px 16px; border-bottom: 1px solid rgba(51,65,85,0.5); font-size: 13px; }
.data-table tr:last-child td { border-bottom: none; }
.clickable { cursor: pointer; transition: background 0.1s; }
.clickable:hover td { background: rgba(59,130,246,0.05); }

.mono { font-family: monospace; font-size: 12px; }
.text-muted { color: var(--text-muted); }
.success { color: var(--success); font-weight: 600; }
.danger { color: var(--danger); font-weight: 600; }

.badge {
  font-size: 11px;
  background: var(--surface-2);
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: 20px;
}

.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
}
.status-pending { background: rgba(148,163,184,0.15); color: var(--text-muted); }
.status-running { background: rgba(59,130,246,0.15); color: #60a5fa; }
.status-success { background: rgba(34,197,94,0.15); color: var(--success); }
.status-failed { background: rgba(239,68,68,0.15); color: var(--danger); }

/* 进度条 */
.progress-inline { display: inline-flex; align-items: center; gap: 5px; margin-left: 6px; vertical-align: middle; }
.progress-bar-bg { display: inline-block; width: 60px; height: 4px; background: rgba(59,130,246,0.15); border-radius: 2px; overflow: hidden; }
.progress-bar-fill { display: block; height: 100%; background: #60a5fa; border-radius: 2px; transition: width 0.3s; }
.progress-pct { font-size: 11px; color: #60a5fa; }

.pagination { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 8px 0; }
.page-btn {
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s;
}
.page-btn:hover:not(:disabled) { border-color: var(--accent); }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.page-info { font-size: 13px; color: var(--text-muted); }
</style>
