<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">回测中心</h1>
      <div class="header-actions">
        <RouterLink v-if="activeTab === 'single'" to="/backtests/new" class="btn btn-primary">新建回测</RouterLink>
        <RouterLink v-if="activeTab === 'comparison'" to="/backtests/comparison/new" class="btn btn-accent">策略对比</RouterLink>
      </div>
    </div>

    <!-- Tab 切换 -->
    <div class="tab-bar">
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'single' }"
        @click="switchTab('single')"
      >
        单个回测
      </button>
      <button
        class="tab-btn"
        :class="{ active: activeTab === 'comparison' }"
        @click="switchTab('comparison')"
      >
        策略对比
      </button>
    </div>

    <!-- 单个回测列表 -->
    <template v-if="activeTab === 'single'">
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

      <div v-if="store.total > singlePageSize" class="pagination">
        <button class="page-btn" :disabled="singleOffset === 0" @click="goSinglePage(singleOffset - singlePageSize)">上一页</button>
        <span class="page-info">{{ singleOffset + 1 }}–{{ Math.min(singleOffset + singlePageSize, store.total) }} / 共 {{ store.total }} 条</span>
        <button class="page-btn" :disabled="singleOffset + singlePageSize >= store.total" @click="goSinglePage(singleOffset + singlePageSize)">下一页</button>
      </div>
    </template>

    <!-- 策略对比列表 -->
    <template v-if="activeTab === 'comparison'">
      <div class="table-wrap">
        <div v-if="compLoading" class="loading">加载中...</div>
        <table v-else class="data-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>策略 A</th>
              <th>策略 B</th>
              <th>日期范围</th>
              <th>状态</th>
              <th>A 累计收益</th>
              <th>B 累计收益</th>
              <th>收益差</th>
              <th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="c in store.comparisons"
              :key="c.comparison_id"
              class="clickable"
              @click="$router.push(`/backtests/comparison/${c.comparison_id}`)"
            >
              <td>{{ c.name || '—' }}</td>
              <td class="mono">{{ c.strategy_a_id }}</td>
              <td class="mono">{{ c.strategy_b_id }}</td>
              <td class="text-muted">{{ c.start_date }} ~ {{ c.end_date }}</td>
              <td>
                <span class="status-badge" :class="'status-' + c.status">{{ compStatusLabel(c.status) }}</span>
                <span v-if="c.status === 'running' && c.progress > 0" class="progress-inline">
                  <span class="progress-bar-bg">
                    <span class="progress-bar-fill" :style="{ width: c.progress + '%' }"></span>
                  </span>
                  <span class="progress-pct">{{ c.progress }}%</span>
                </span>
              </td>
              <td :class="returnClass(c.comparison_metrics?.a_cumulative_return_pct)">
                {{ c.comparison_metrics ? formatPct(c.comparison_metrics.a_cumulative_return_pct) : '—' }}
              </td>
              <td :class="returnClass(c.comparison_metrics?.b_cumulative_return_pct)">
                {{ c.comparison_metrics ? formatPct(c.comparison_metrics.b_cumulative_return_pct) : '—' }}
              </td>
              <td :class="returnClass(c.comparison_metrics?.cumulative_return_diff_pct)">
                {{ c.comparison_metrics ? formatPct(c.comparison_metrics.cumulative_return_diff_pct) : '—' }}
              </td>
              <td class="text-muted">{{ formatTime(c.created_at) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!compLoading && store.comparisons.length === 0" class="empty">暂无策略对比记录，点击「策略对比」开始</div>
      </div>

      <div v-if="store.comparisonsTotal > compPageSize" class="pagination">
        <button class="page-btn" :disabled="compOffset === 0" @click="goCompPage(compOffset - compPageSize)">上一页</button>
        <span class="page-info">{{ compOffset + 1 }}–{{ Math.min(compOffset + compPageSize, store.comparisonsTotal) }} / 共 {{ store.comparisonsTotal }} 条</span>
        <button class="page-btn" :disabled="compOffset + compPageSize >= store.comparisonsTotal" @click="goCompPage(compOffset + compPageSize)">下一页</button>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { useBacktestStore } from '../stores/backtests'

const store = useBacktestStore()
const loading = ref(false)
const compLoading = ref(false)
const activeTab = ref<'single' | 'comparison'>('single')
const singleOffset = ref(0)
const singlePageSize = 50
const compOffset = ref(0)
const compPageSize = 50

function statusLabel(status: string): string {
  const map: Record<string, string> = { pending: '待执行', running: '执行中', success: '成功', failed: '失败' }
  return map[status] ?? status
}

function compStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待执行', running: '执行中', success: '成功', failed: '失败', partial: '部分成功',
  }
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

async function goSinglePage(newOffset: number) {
  singleOffset.value = newOffset
  loading.value = true
  try { await store.loadAll(newOffset, singlePageSize) } finally { loading.value = false }
}

async function goCompPage(newOffset: number) {
  compOffset.value = newOffset
  compLoading.value = true
  try { await store.loadAllComparisons(newOffset, compPageSize) } finally { compLoading.value = false }
}

function switchTab(tab: 'single' | 'comparison') {
  activeTab.value = tab
}

onMounted(async () => {
  loading.value = true
  try { await store.loadAll(0, singlePageSize) } finally { loading.value = false }
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }
.header-actions { display: flex; gap: 10px; }

/* Tab 切换 */
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 2px solid var(--border);
  margin-bottom: -4px;
}
.tab-btn {
  padding: 8px 20px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-muted);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s;
}
.tab-btn:hover { color: var(--text); }
.tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }

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
.btn-accent { background: var(--surface-2); color: var(--accent); border: 1px solid var(--accent); }
.btn-accent:hover { background: rgba(59,130,246,0.1); }

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
.status-partial { background: rgba(245,158,11,0.15); color: #f59e0b; }

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
