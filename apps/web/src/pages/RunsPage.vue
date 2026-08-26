<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">运行记录</h1>
      <div class="actions">
        <button class="btn btn-secondary" :disabled="refreshing" @click="load(currentOffset)">刷新</button>
      </div>
    </div>

    <!-- 轮询提示 -->
    <div v-if="polling" class="polling-banner">有任务执行中，自动刷新状态...</div>

    <div class="table-wrap">
      <div v-if="loading" class="loading">加载中...</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th></th>
            <th>Run ID</th>
            <th>类型</th>
            <th>策略</th>
            <th>交易日</th>
            <th>状态</th>
            <th>耗时</th>
            <th>开始时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="item in runs" :key="item.run_id">
            <tr class="run-row" @click="toggleDetail(item.run_id)">
              <td class="expand-cell">
                <span class="expand-icon" :class="{ expanded: expandedId === item.run_id }">&#9654;</span>
              </td>
              <td class="mono text-muted">{{ item.run_id.slice(0, 8) }}…</td>
              <td><span class="type-badge">{{ item.run_type }}</span></td>
              <td class="text-muted mono">{{ item.strategy_id ?? '—' }}</td>
              <td class="text-muted mono">{{ item.trade_date ?? '—' }}</td>
              <td><span class="status-badge" :class="'status-' + item.status">{{ statusLabel(item.status) }}</span></td>
              <td class="text-muted mono">{{ formatDuration(item.run_id) }}</td>
              <td class="text-muted">{{ formatTime(item.started_at) }}</td>
              <td>
                <button
                  v-if="item.status === 'failed'"
                  class="btn btn-sm btn-accent"
                  :disabled="retryingId === item.run_id"
                  @click.stop="handleRetry(item.run_id)"
                >重试</button>
              </td>
            </tr>
            <!-- 展开的详情面板 -->
            <tr v-if="expandedId === item.run_id && detailCache[item.run_id]" class="detail-row">
              <td colspan="9">
                <div class="detail-panel">
                  <!-- 错误信息 -->
                  <div v-if="detailCache[item.run_id].error_message" class="detail-section">
                    <div class="section-label">错误信息</div>
                    <pre class="error-block">{{ detailCache[item.run_id].error_message }}</pre>
                  </div>

                  <!-- 运行指标 -->
                  <div v-if="detailCache[item.run_id].metrics" class="detail-section">
                    <div class="section-label">运行指标</div>
                    <div class="metrics-grid">
                      <template v-for="(val, key) in flattenMetrics(detailCache[item.run_id].metrics)" :key="key">
                        <div class="metric-item">
                          <span class="metric-key">{{ key }}</span>
                          <span class="metric-val">{{ val }}</span>
                        </div>
                      </template>
                    </div>
                  </div>

                  <!-- 子项明细 -->
                  <div v-if="itemDetails[item.run_id] && itemDetails[item.run_id].length > 0" class="detail-section">
                    <div class="section-label">子项明细 ({{ itemDetails[item.run_id].length }})</div>
                    <div class="items-table-wrap">
                      <table class="items-table">
                        <thead>
                          <tr>
                            <th>ETF 代码</th>
                            <th>状态</th>
                            <th>说明</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="sub in itemDetails[item.run_id]" :key="sub.id">
                            <td class="mono">{{ sub.etf_code }}</td>
                            <td><span class="status-badge" :class="'status-' + sub.status">{{ sub.status }}</span></td>
                            <td class="text-muted">{{ sub.message ?? '—' }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>

                  <div v-if="!detailCache[item.run_id].error_message && !detailCache[item.run_id].metrics && (!itemDetails[item.run_id] || itemDetails[item.run_id].length === 0)" class="detail-empty">
                    暂无详细信息
                  </div>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <div v-if="!loading && runs.length === 0" class="empty">暂无运行记录</div>
    </div>

    <div v-if="total > pageSize" class="pagination">
      <button class="page-btn" :disabled="currentOffset === 0" @click="load(currentOffset - pageSize)">上一页</button>
      <span class="page-info">{{ currentOffset + 1 }}–{{ Math.min(currentOffset + pageSize, total) }} / 共 {{ total }} 条</span>
      <button class="page-btn" :disabled="currentOffset + pageSize >= total" @click="load(currentOffset + pageSize)">下一页</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchRunDetail, fetchRunItems, fetchRuns, retryRun } from '../api/runs'
import type { ResearchRunDetail, ResearchRunItem, ResearchRunSummary } from '../types/api'
import { usePolling } from '../composables/usePolling'
import { notifySkippedRun } from '../composables/useRunSkipToast'

const runs = ref<ResearchRunSummary[]>([])
const total = ref(0)
const currentOffset = ref(0)
const pageSize = 50
const loading = ref(false)
const refreshing = ref(false)
const retryingId = ref<string | null>(null)

// 展开详情相关
const expandedId = ref<string | null>(null)
const detailCache = ref<Record<string, ResearchRunDetail>>({})
const itemDetails = ref<Record<string, ResearchRunItem[]>>({})
const durationCache = ref<Record<string, number | null>>({})

/** 是否有正在执行的任务 */
const hasActiveRuns = computed(() => runs.value.some((r) => r.status === 'pending' || r.status === 'running'))

/** 统一轮询：每 3 秒刷新列表与展开详情，无活动任务或组件卸载时自动停止 */
const { polling, start: startPolling } = usePolling({
  intervalMs: 3000,
  // 挂载时已立即加载过列表，首次轮询等待一个间隔，避免重复请求和加载闪烁
  immediate: false,
  fetcher: async () => {
    refreshing.value = true
    try {
      await load(currentOffset.value)
      // 更新展开项的详情
      if (expandedId.value && detailCache.value[expandedId.value]) {
        try {
          const detail = await fetchRunDetail(expandedId.value)
          detailCache.value[expandedId.value] = detail
          durationCache.value[detail.run_id] = detail.duration_seconds ?? null
        } catch {
          // 静默失败
        }
      }
    } finally {
      refreshing.value = false
    }
  },
  isDone: () => !hasActiveRuns.value,
})

/** 状态标签 */
function statusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待执行',
    running: '执行中',
    success: '成功',
    skipped: '已跳过',
    failed: '失败',
    completed: '完成',
  }
  return map[status] ?? status
}

/** 格式化时间 */
function formatTime(ts: string | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

/** 格式化耗时 */
function formatDuration(runId: string): string {
  const d = durationCache.value[runId]
  if (d === undefined || d === null) return '—'
  if (d < 60) return `${d.toFixed(1)}s`
  const m = Math.floor(d / 60)
  const s = Math.round(d % 60)
  return `${m}m${s}s`
}

/** 将嵌套 metrics 展开为平铺的 key-value */
function flattenMetrics(metrics: Record<string, unknown> | null | undefined): Record<string, string> {
  if (!metrics) return {}
  const result: Record<string, string> = {}
  for (const [k, v] of Object.entries(metrics)) {
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      for (const [sk, sv] of Object.entries(v as Record<string, unknown>)) {
        result[`${k}.${sk}`] = String(sv)
      }
    } else {
      result[k] = String(v)
    }
  }
  return result
}

/** 重试失败任务 */
async function handleRetry(runId: string) {
  retryingId.value = runId
  try {
    const res = await retryRun(runId)
    notifySkippedRun(res.run_id)
    await load(currentOffset.value)
    startPolling()
  } finally {
    retryingId.value = null
  }
}

/** 展开/收起详情 */
async function toggleDetail(runId: string) {
  if (expandedId.value === runId) {
    expandedId.value = null
    return
  }
  expandedId.value = runId
  // 加载详情（缓存）
  if (!detailCache.value[runId]) {
    try {
      const detail = await fetchRunDetail(runId)
      detailCache.value[runId] = detail
      durationCache.value[runId] = detail.duration_seconds ?? null
    } catch {
      // 静默失败
    }
  }
  // 加载子项明细（缓存）
  if (!itemDetails.value[runId]) {
    try {
      itemDetails.value[runId] = await fetchRunItems(runId)
    } catch {
      itemDetails.value[runId] = []
    }
  }
}

/** 加载列表 */
async function load(offset = 0) {
  loading.value = true
  try {
    const res = await fetchRuns(offset, pageSize)
    runs.value = res.items
    total.value = res.total
    currentOffset.value = offset
    // 同步更新 duration 缓存（列表接口不含 duration，从 detail 缓存读取）
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await load()
  if (hasActiveRuns.value) {
    startPolling()
  }
})
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
.btn:disabled { opacity: 0.5; cursor: not allowed; }

.btn-sm { padding: 4px 10px; font-size: 12px; }
.btn-accent { background: rgba(59,130,246,0.15); color: #60a5fa; border-color: rgba(59,130,246,0.3); }
.btn-accent:hover { background: rgba(59,130,246,0.25); }

.polling-banner {
  padding: 10px 16px;
  background: rgba(59,130,246,0.1);
  border: 1px solid rgba(59,130,246,0.3);
  border-radius: var(--radius-sm);
  color: #60a5fa;
  font-size: 13px;
}

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
.data-table td { padding: 12px 16px; border-bottom: 1px solid rgba(51,65,85,0.5); }
.data-table tr:last-child td { border-bottom: none; }

.run-row { cursor: pointer; transition: background 0.1s; }
.run-row:hover { background: rgba(255,255,255,0.02); }

.expand-cell { width: 24px; padding-right: 0; }
.expand-icon {
  display: inline-block;
  font-size: 10px;
  color: var(--text-muted);
  transition: transform 0.2s;
}
.expand-icon.expanded { transform: rotate(90deg); }

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
}
.status-pending { background: rgba(148,163,184,0.15); color: var(--text-muted); }
.status-running { background: rgba(59,130,246,0.15); color: #60a5fa; }
.status-success { background: rgba(34,197,94,0.15); color: var(--success); }
.status-failed { background: rgba(239,68,68,0.15); color: var(--danger); }
.status-completed { background: rgba(34,197,94,0.15); color: var(--success); }
.status-skipped { background: rgba(148,163,184,0.1); color: var(--text-muted); }

/* 详情面板 */
.detail-row td { padding: 0; border-bottom: 1px solid var(--border); }
.detail-panel {
  padding: 16px 24px;
  background: rgba(0,0,0,0.15);
  border-top: 1px solid rgba(51,65,85,0.3);
}
.detail-section { margin-bottom: 16px; }
.detail-section:last-child { margin-bottom: 0; }
.section-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.error-block {
  background: rgba(239,68,68,0.08);
  border: 1px solid rgba(239,68,68,0.2);
  border-radius: var(--radius-sm);
  padding: 12px 16px;
  font-size: 12px;
  font-family: monospace;
  color: var(--danger);
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
  max-height: 200px;
  overflow-y: auto;
}

.metrics-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.metric-item {
  display: flex;
  align-items: center;
  gap: 6px;
  background: var(--surface-2);
  padding: 4px 10px;
  border-radius: var(--radius-sm);
  font-size: 12px;
}
.metric-key { color: var(--text-muted); font-family: monospace; }
.metric-val { color: var(--text); font-weight: 500; }

.items-table-wrap {
  max-height: 300px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.items-table { width: 100%; border-collapse: collapse; }
.items-table th {
  text-align: left;
  padding: 6px 12px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  background: rgba(0,0,0,0.1);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
}
.items-table td {
  padding: 6px 12px;
  font-size: 12px;
  border-bottom: 1px solid rgba(51,65,85,0.3);
}
.items-table tr:last-child td { border-bottom: none; }

.detail-empty { padding: 12px; text-align: center; color: var(--text-muted); font-size: 13px; }

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
