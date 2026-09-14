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
      <div class="filter-card">
        <div class="filter-item strategy-filter">
          <label class="form-label" for="strategy-filter">策略 ID</label>
          <input
            id="strategy-filter"
            v-model.trim="strategyFilter"
            class="form-input"
            type="text"
            placeholder="精确匹配策略 ID"
            @keyup.enter="applyFilters"
          />
        </div>
        <div class="filter-item">
          <label class="form-label" for="status-filter">状态</label>
          <select id="status-filter" v-model="statusFilter" class="form-input">
            <option value="">全部</option>
            <option value="pending">待执行</option>
            <option value="running">执行中</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="cancelled">已取消</option>
          </select>
        </div>
        <div class="filter-item">
          <label class="form-label" for="purpose-filter">用途</label>
          <select id="purpose-filter" v-model="purposeFilter" class="form-input">
            <option value="">全部</option>
            <option value="research">研究期</option>
            <option value="validation">验证期</option>
            <option value="monitor">上线监控</option>
          </select>
        </div>
        <div class="filter-item">
          <label class="form-label" for="calendar-filter">调仓日历</label>
          <select id="calendar-filter" v-model="calendarFilter" class="form-input">
            <option value="">全部</option>
            <option value="upstream">上游数据源</option>
            <option value="database">本地日历快照</option>
            <option value="not_required">每日调仓（不需要）</option>
          </select>
        </div>
        <div class="filter-item">
          <label class="form-label" for="created-from">创建日期起</label>
          <input id="created-from" v-model="createdFrom" class="form-input" type="date" />
        </div>
        <div class="filter-item">
          <label class="form-label" for="created-to">创建日期止</label>
          <input id="created-to" v-model="createdTo" class="form-input" type="date" />
        </div>
        <div class="filter-actions">
          <button class="btn btn-primary" type="button" @click="applyFilters">筛选</button>
          <button class="btn btn-secondary" type="button" @click="clearFilters">重置</button>
        </div>
      </div>
      <div class="table-wrap">
        <div v-if="loading" class="loading">加载中...</div>
        <table v-else class="data-table">
          <thead>
            <tr>
              <th>策略</th>
              <th>日期范围</th>
              <th>状态</th>
              <th>队列</th>
              <th>累计收益</th>
              <th>年化收益</th>
              <th>最大回撤</th>
              <th>夏普</th>
              <th>创建时间</th>
              <th>操作</th>
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
              <td class="text-muted queue-cell">{{ queueLabel(item) }}</td>
              <td :class="returnClass(item.metrics?.cumulative_return_pct)">
                {{ item.metrics ? formatPct(item.metrics.cumulative_return_pct) : '—' }}
              </td>
              <td :class="returnClass(item.metrics?.annualized_return_pct)">
                {{ item.metrics ? formatPct(item.metrics.annualized_return_pct) : '—' }}
              </td>
              <td class="danger">{{ item.metrics ? formatPct(item.metrics.max_drawdown_pct) : '—' }}</td>
              <td>{{ item.metrics ? item.metrics.sharpe_ratio.toFixed(2) : '—' }}</td>
              <td class="text-muted">{{ formatTime(item.created_at) }}</td>
              <td>
                <button
                  v-if="item.status === 'pending' || item.status === 'running'"
                  class="btn btn-secondary btn-sm"
                  type="button"
                  :disabled="cancellingId === item.backtest_id"
                  @click.stop="cancelRun(item.backtest_id)"
                >
                  取消
                </button>
                <button
                  v-else
                  class="btn btn-secondary btn-sm"
                  type="button"
                  :disabled="deletingId === item.backtest_id"
                  @click.stop="removeRun(item)"
                >
                  删除
                </button>
              </td>
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

import { cancelBacktest } from '../api/backtests'
import { useBacktestStore } from '../stores/backtests'
import { toast } from '../stores/toast'
import type { BacktestSummary } from '../types/api'

const store = useBacktestStore()
const loading = ref(false)
const compLoading = ref(false)
const activeTab = ref<'single' | 'comparison'>('single')
const singleOffset = ref(0)
const singlePageSize = 50
const compOffset = ref(0)
const compPageSize = 50
const strategyFilter = ref('')
const statusFilter = ref('')
const purposeFilter = ref('')
const calendarFilter = ref('')
const createdFrom = ref('')
const createdTo = ref('')
const cancellingId = ref('')
const deletingId = ref('')

/** 汇总当前筛选条件为请求参数。 */
function currentFilters() {
  return {
    strategyId: strategyFilter.value || undefined,
    status: statusFilter.value || undefined,
    purpose: purposeFilter.value || undefined,
    calendarSource: calendarFilter.value || undefined,
    createdFrom: createdFrom.value || undefined,
    createdTo: createdTo.value || undefined,
  }
}

/** 队列列文案：排队位置/等待时长/执行耗时/无队列任务。 */
function queueLabel(item: {
  job_status?: string | null
  queued_seconds?: number | null
  elapsed_seconds?: number | null
  queue_position?: number | null
}): string {
  if (item.job_status === 'pending') {
    const position = item.queue_position != null ? `#${item.queue_position + 1}` : ''
    const waited = item.queued_seconds != null ? `已等 ${formatDuration(item.queued_seconds)}` : ''
    return ['排队中', position, waited].filter(Boolean).join(' · ')
  }
  if (item.job_status === 'paused') return '已暂停'
  if (item.job_status === 'running' && item.elapsed_seconds != null) {
    return `执行 ${formatDuration(item.elapsed_seconds)}`
  }
  if (item.job_status === 'cancelled') return '任务已取消'
  return '—'
}

/** 把秒数格式化为紧凑的中文时长。 */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`
  return `${Math.floor(seconds / 3600)} 时 ${Math.floor((seconds % 3600) / 60)} 分`
}

/** 请求取消回测并刷新列表（运行中的任务在安全检查点退出）。 */
async function cancelRun(backtestId: string): Promise<void> {
  cancellingId.value = backtestId
  try {
    const result = await cancelBacktest(backtestId)
    toast.info(result.message)
    await store.loadAll(singleOffset.value, singlePageSize, currentFilters())
  } finally {
    cancellingId.value = ''
  }
}

/** 删除回测记录（C5）：存在 JSONB 引用时后端返回 409，确认后带 force 重试。 */
async function removeRun(item: BacktestSummary): Promise<void> {
  if (!window.confirm(`确认删除回测 ${item.backtest_id}？日结果与对比记录会一并清理。`)) return
  deletingId.value = item.backtest_id
  try {
    const result = await store.remove(item.backtest_id)
    toast.info(result.message)
  } catch (error) {
    const detail = extractErrorDetail(error)
    if (!detail.includes('仍被引用')) {
      toast.error(detail || '删除失败')
      return
    }
    if (!window.confirm(`${detail}\n\n仍要强制删除吗？`)) return
    try {
      const forced = await store.remove(item.backtest_id, true)
      toast.warning(forced.message)
    } catch (forceError) {
      toast.error(extractErrorDetail(forceError) || '强制删除失败')
      return
    }
  } finally {
    deletingId.value = ''
    await store.loadAll(singleOffset.value, singlePageSize, currentFilters())
  }
}

/** 从 axios 错误中提取后端 detail 文案。 */
function extractErrorDetail(error: unknown): string {
  const response = (error as { response?: { data?: { detail?: string } } })?.response
  return response?.data?.detail ?? ''
}

/** 状态中文标签。 */
function statusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待执行',
    running: '执行中',
    success: '成功',
    failed: '失败',
    cancelled: '已取消',
  }
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
  try { await store.loadAll(newOffset, singlePageSize, currentFilters()) } finally { loading.value = false }
}

/** 按当前筛选条件重新加载回测列表。 */
async function applyFilters() {
  if (createdFrom.value && createdTo.value && createdFrom.value > createdTo.value) return
  singleOffset.value = 0
  loading.value = true
  try { await store.loadAll(0, singlePageSize, currentFilters()) } finally { loading.value = false }
}

/** 清空回测列表筛选条件。 */
async function clearFilters() {
  strategyFilter.value = ''
  statusFilter.value = ''
  purposeFilter.value = ''
  calendarFilter.value = ''
  createdFrom.value = ''
  createdTo.value = ''
  await applyFilters()
}

async function goCompPage(newOffset: number) {
  compOffset.value = newOffset
  compLoading.value = true
  try { await store.loadAllComparisons(newOffset, compPageSize) } finally { compLoading.value = false }
}

const comparisonsLoaded = ref(false)

/** 切换 Tab；首次进入对比 Tab 时加载对比列表。 */
async function switchTab(tab: 'single' | 'comparison') {
  activeTab.value = tab
  if (tab === 'comparison' && !comparisonsLoaded.value) {
    compLoading.value = true
    try {
      await store.loadAllComparisons(0, compPageSize)
      comparisonsLoaded.value = true
    } finally {
      compLoading.value = false
    }
  }
}

onMounted(async () => {
  loading.value = true
  try { await store.loadAll(0, singlePageSize, currentFilters()) } finally { loading.value = false }
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }
.header-actions { display: flex; gap: 10px; }

.filter-card {
  display: flex;
  align-items: flex-end;
  gap: 12px;
  padding: 14px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  flex-wrap: wrap;
}
.filter-item { display: flex; flex-direction: column; gap: 6px; min-width: 150px; }
.strategy-filter { min-width: 280px; }
.form-label { font-size: 12px; font-weight: 600; color: var(--text-muted); }
.form-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 7px 10px;
  font-size: 13px;
  color: var(--text);
  outline: none;
}
.form-input:focus { border-color: var(--accent); }
.filter-actions { display: flex; gap: 8px; }
.btn-secondary { background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); }

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
.status-cancelled { background: rgba(148,163,184,0.15); color: var(--text-muted); }
.status-partial { background: rgba(245,158,11,0.15); color: #f59e0b; }

.queue-cell { font-size: 12px; white-space: nowrap; }
.btn-sm { padding: 4px 10px; font-size: 12px; }

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
