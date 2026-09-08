<template>
  <div class="data-management-page">
    <!-- 页面头 -->
    <header class="page-head">
      <div class="head-copy">
        <h1 class="page-title">数据管理</h1>
        <p class="page-subtitle">非新闻外部数据的健康状态、质量检查与维护入口</p>
      </div>
      <div class="head-actions">
        <button
          class="btn btn-ghost"
          :disabled="submitting || polling"
          @click="runGlobal('check')"
        >
          检查全部质量
        </button>
        <button
          class="btn btn-primary"
          :disabled="submitting || polling"
          @click="runGlobal('sync_latest')"
        >
          同步全部数据
        </button>
      </div>
    </header>

    <div v-if="error" class="notice error">{{ error }}</div>
    <div v-if="polling" class="notice polling">
      <span class="spinner" aria-hidden="true"></span>
      数据维护任务执行中，完成后自动刷新健康状态…
    </div>

    <!-- 健康概览卡片 -->
    <section v-if="overview" class="overview-grid">
      <article class="stat-card healthy">
        <span class="stat-icon">✓</span>
        <div><strong>{{ overview.healthy_count }}</strong><span>健康</span></div>
      </article>
      <article class="stat-card warning">
        <span class="stat-icon">!</span>
        <div><strong>{{ overview.warning_count }}</strong><span>需关注</span></div>
      </article>
      <article class="stat-card error">
        <span class="stat-icon">×</span>
        <div><strong>{{ overview.error_count }}</strong><span>异常</span></div>
      </article>
      <article class="stat-card unknown">
        <span class="stat-icon">?</span>
        <div><strong>{{ overview.unknown_count }}</strong><span>待检查</span></div>
      </article>
      <article class="stat-card schedule">
        <span class="stat-icon">↻</span>
        <div class="schedule-copy">
          <strong>自动同步</strong>
          <span>{{ overview.schedule_time }}</span>
        </div>
      </article>
    </section>

    <!-- 数据集卡片 -->
    <section v-if="overview" class="dataset-section">
      <div class="section-head">
        <h2 class="section-title">受管数据集</h2>
        <span class="section-hint">点击卡片查看分区并执行单对象维护</span>
      </div>
      <div v-if="loading" class="loading-state">加载数据健康状态…</div>
      <div v-else class="dataset-grid">
        <article
          v-for="item in overview.datasets"
          :key="item.dataset_key"
          class="dataset-card"
          :class="{
            selected: selectedKey === item.dataset_key,
            [`tone-${item.health_status}`]: true,
          }"
          @click="selectDataSet(item.dataset_key)"
        >
          <header class="card-top">
            <div class="card-title">
              <span class="status-dot" aria-hidden="true"></span>
              <strong>{{ item.display_name }}</strong>
              <span class="freq-chip">{{ item.frequency }}</span>
            </div>
            <span class="status-badge" :class="`status-${item.health_status}`">
              {{ statusText(item.health_status) }}
            </span>
          </header>

          <div class="card-source">
            {{ item.source_name ?? item.source_label }}
            <span v-if="item.partition_label" class="muted">
              · 按{{ item.partition_label }}分
            </span>
          </div>

          <div class="card-metrics">
            <div class="metric">
              <span>实际 / 目标</span>
              <strong>{{ item.latest_date ?? '—' }}<em>/ {{ item.expected_date ?? '按发布周期' }}</em></strong>
            </div>
            <div class="metric">
              <span>记录数</span>
              <strong>{{ item.record_count.toLocaleString() }}</strong>
            </div>
            <div class="metric">
              <span>缺口 / 异常</span>
              <strong>{{ item.missing_count }} / {{ item.invalid_count }}</strong>
            </div>
          </div>

          <p class="card-issue" :class="{ none: !formatIssue(item) }">
            {{ formatIssue(item) || '暂无已知问题' }}
          </p>

          <footer class="card-footer" @click.stop>
            <span class="last-run muted">
              <template v-if="item.last_run_id">
                {{ runStatusText(item.last_run_status) }}
                <em>{{ formatClock(item.last_checked_at) }}</em>
              </template>
              <template v-else>尚未执行统一检查</template>
            </span>
            <div class="mini-actions">
              <button class="btn-mini" :disabled="busy" @click="runOperation('check', item.dataset_key)">检查</button>
              <button class="btn-mini" :disabled="busy" @click="runOperation('sync_latest', item.dataset_key)">补最新</button>
              <button class="btn-mini" :disabled="busy" @click="runOperation('repair_gaps', item.dataset_key)">修复缺口</button>
              <button class="btn-mini danger" :disabled="busy" @click="runOperation('rebuild', item.dataset_key)">全量重拉</button>
            </div>
          </footer>
        </article>
      </div>
    </section>

    <!-- 分区详情 -->
    <section v-if="detail" class="detail-section card-panel">
      <div class="section-head">
        <div>
          <h2 class="section-title">{{ detail.dataset.display_name }}</h2>
          <p class="section-rules muted">{{ detail.quality_rules.join('；') }}</p>
        </div>
        <span v-if="detail.total > 0" class="section-badge">{{ detail.total }} 个分区</span>
      </div>

      <div v-if="detail.items.length === 0" class="no-partition">
        <div class="no-partition-title">该数据集无独立分区</div>
        <div class="summary-rows">
          <div class="summary-row">
            <span>健康状态</span>
            <strong>{{ statusText(detail.dataset.health_status) }}</strong>
          </div>
          <div class="summary-row">
            <span>数据范围</span>
            <strong>{{ detail.dataset.earliest_date ?? '—' }} ~ {{ detail.dataset.latest_date ?? '—' }}</strong>
          </div>
          <div class="summary-row">
            <span>目标日期</span>
            <strong>{{ detail.dataset.expected_date ?? '按发布周期' }}</strong>
          </div>
          <div class="summary-row">
            <span>记录 / 缺口 / 异常</span>
            <strong>{{ detail.dataset.record_count.toLocaleString() }} / {{ detail.dataset.missing_count }} / {{ detail.dataset.invalid_count }}</strong>
          </div>
          <div class="summary-row">
            <span>最近检查</span>
            <strong>{{ formatClock(detail.dataset.last_checked_at) || '—' }}</strong>
          </div>
        </div>
      </div>

      <div v-else class="partition-table-wrap">
        <table class="partition-table">
          <thead>
            <tr>
              <th>分区</th>
              <th>状态</th>
              <th>实际 / 目标日期</th>
              <th>记录数</th>
              <th>问题</th>
              <th>维护操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in detail.items" :key="item.partition_key">
              <td>
                <strong>{{ item.partition_key }}</strong>
                <span v-if="item.partition_name && item.partition_name !== item.partition_key" class="muted block">
                  {{ item.partition_name }}
                </span>
              </td>
              <td>
                <span class="status-badge" :class="`status-${item.health_status}`">
                  {{ statusText(item.health_status) }}
                </span>
              </td>
              <td class="mono">{{ item.latest_date ?? '—' }}<em>/ {{ item.expected_date ?? '按发布周期' }}</em></td>
              <td class="mono">{{ item.record_count.toLocaleString() }}</td>
              <td class="issue-cell">{{ formatIssue(item) || '正常' }}</td>
              <td>
                <div class="mini-actions" @click.stop>
                  <button class="btn-mini" :disabled="busy" @click="runOperation('check', detail.dataset.dataset_key, item.partition_key)">检查</button>
                  <button class="btn-mini" :disabled="busy" @click="runOperation('sync_latest', detail.dataset.dataset_key, item.partition_key)">补最新</button>
                  <button class="btn-mini" :disabled="busy" @click="runOperation('repair_gaps', detail.dataset.dataset_key, item.partition_key)">修复</button>
                  <button class="btn-mini danger" :disabled="busy" @click="runOperation('rebuild', detail.dataset.dataset_key, item.partition_key)">重拉</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <footer v-if="detail.total > detail.limit" class="pagination">
        <button class="btn-mini" :disabled="detail.offset === 0" @click="changePage(-1)">上一页</button>
        <span>{{ Math.floor(detail.offset / detail.limit) + 1 }} / {{ Math.ceil(detail.total / detail.limit) }}</span>
        <button class="btn-mini" :disabled="detail.offset + detail.limit >= detail.total" @click="changePage(1)">下一页</button>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * 统一数据管理页面。
 *
 * 展示非新闻外部数据集的当前健康快照，并通过统一后台任务执行质量检查、
 * 补最新、修复缺口和安全全量重拉。数据集级"补最新"与分区级操作会显式
 * 请求上游（force），全局/自动同步由调度器按各数据集节流规则执行。
 */

import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { fetchRunDetail } from '../api/runs'
import {
  fetchDataManagementOverview,
  fetchDataSetDetail,
  triggerDataManagementOperation,
} from '../api/dataManagement'
import { usePolling } from '../composables/usePolling'
import type {
  DataManagementOperation,
  DataManagementOverview,
  DataSetDetailResponse,
  ResearchRunDetail,
} from '../types/api'

const overview = ref<DataManagementOverview | null>(null)
const detail = ref<DataSetDetailResponse | null>(null)
const selectedKey = ref<string | null>(null)
const selectedPartitionKey = ref<string | null>(null)
const detailOffset = ref(0)
const activeRunId = ref<string | null>(null)
const loading = ref(false)
const submitting = ref(false)
const error = ref<string | null>(null)
const route = useRoute()

/** 是否有维护任务正在执行。 */
const busy = computed(() => submitting.value || polling.value)

/** 读取数据管理总览。 */
async function loadOverview(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    overview.value = await fetchDataManagementOverview()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '数据管理总览加载失败'
  } finally {
    loading.value = false
  }
}

/** 选择数据集并加载其分区快照。 */
async function selectDataSet(datasetKey: string, partitionKey?: string): Promise<void> {
  selectedKey.value = datasetKey
  selectedPartitionKey.value = partitionKey ?? null
  detailOffset.value = 0
  await loadDetail()
  if (datasetKey) {
    document.querySelector('.detail-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

/** 加载当前选中数据集的一个详情页。 */
async function loadDetail(): Promise<void> {
  if (!selectedKey.value) return
  try {
    detail.value = await fetchDataSetDetail(
      selectedKey.value,
      detailOffset.value,
      50,
      selectedPartitionKey.value ?? undefined,
    )
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '数据集详情加载失败'
  }
}

/** 切换当前数据集的分区分页。 */
async function changePage(direction: number): Promise<void> {
  if (!detail.value) return
  detailOffset.value = Math.max(0, detailOffset.value + direction * detail.value.limit)
  await loadDetail()
}

/** 返回健康状态对应的中文标签。 */
function statusText(status: string): string {
  const labels: Record<string, string> = {
    healthy: '健康',
    warning: '需关注',
    error: '异常',
    unknown: '待检查',
    unsupported: '不支持',
  }
  return labels[status] ?? status
}

/** 返回运行状态的简短标签。 */
function runStatusText(status: string | null): string {
  const labels: Record<string, string> = {
    success: '成功',
    partial_success: '部分成功',
    failed: '失败',
    running: '执行中',
    pending: '待执行',
    skipped: '跳过',
  }
  return status ? labels[status] ?? status : '—'
}

/** 将 UTC 时间串展示为本地可读时间。 */
function formatClock(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

/** 将结构化健康问题转换为简短、可读的表格说明。 */
function formatIssue(item: DataSetDetailResponse['dataset']): string {
  const summary = item.issue_summary
  if (!summary) return ''
  const lastError = summary.last_error
  if (typeof lastError === 'string') return lastError
  const reasonLabels: Record<string, string> = {
    empty: '无可用数据',
    stale: '数据已过期',
    missing_dates: '存在日期缺口',
    invalid_values: '存在异常值',
    warnings: '存在数据告警',
  }
  const reasons = summary.reasons
  if (Array.isArray(reasons)) {
    const readable = reasons
      .filter((item): item is string => typeof item === 'string')
      .map((reason) => reasonLabels[reason] ?? reason)
    if (readable.length > 0) return readable.join('；')
  }
  const warnings = summary.warning_count
  const errors = summary.error_count ?? item.invalid_count
  return [
    `缺口 ${item.missing_count}`,
    `错误 ${typeof errors === 'number' ? errors : item.invalid_count}`,
    typeof warnings === 'number' && warnings > 0 ? `告警 ${warnings}` : '',
  ].filter(Boolean).join(' · ')
}

/** 提交全局数据维护操作。 */
async function runGlobal(operation: Extract<DataManagementOperation, 'sync_latest' | 'check'>): Promise<void> {
  await runOperation(operation)
}

/** 提交数据集或单分区维护操作；单对象补最新时显式请求上游。 */
async function runOperation(
  operation: DataManagementOperation,
  datasetKey?: string,
  partitionKey?: string,
): Promise<void> {
  let confirmationToken: string | undefined
  if (operation === 'rebuild') {
    if (!datasetKey) return
    const expected = `REBUILD:${datasetKey}:${partitionKey ?? 'ALL'}`
    const entered = window.prompt(`全量重拉会在抓取校验成功后替换现有数据。请输入 ${expected} 确认。`)
    if (entered !== expected) return
    confirmationToken = expected
  }
  submitting.value = true
  try {
    const accepted = await triggerDataManagementOperation({
      operation,
      dataset_key: datasetKey,
      partition_key: partitionKey,
      force: operation === 'sync_latest' && datasetKey !== undefined,
      confirmation_token: confirmationToken,
    })
    activeRunId.value = accepted.run_id
    await start()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '数据维护任务提交失败'
  } finally {
    submitting.value = false
  }
}

/** 轮询后台任务，结束后刷新全局与当前详情。 */
const { start, polling } = usePolling<ResearchRunDetail>({
  fetcher: async () => fetchRunDetail(activeRunId.value ?? ''),
  isDone: (run) => ['success', 'partial_success', 'failed', 'skipped'].includes(run.status),
  intervalMs: 2000,
  onData: (run) => {
    if (['success', 'partial_success', 'failed', 'skipped'].includes(run.status)) {
      void loadOverview()
      if (selectedKey.value) void loadDetail()
    }
  },
})

onMounted(async () => {
  await loadOverview()
  const datasetKey = typeof route.query.dataset === 'string' ? route.query.dataset : null
  const partitionKey = typeof route.query.partition === 'string' ? route.query.partition : undefined
  if (datasetKey) {
    await selectDataSet(datasetKey, partitionKey)
  }
})
</script>

<style scoped>
.data-management-page {
  display: flex;
  flex-direction: column;
  gap: 22px;
  padding-bottom: 28px;
}

/* 头部 */
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}
.page-title { margin: 0; font-size: 26px; font-weight: 700; letter-spacing: .2px; }
.page-subtitle, .muted { color: var(--text-muted); font-size: 13px; }
.head-actions { display: flex; gap: 10px; flex-shrink: 0; }

/* 通知 */
.notice {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.notice.error { color: var(--danger); background: rgba(239, 68, 68, .08); }
.notice.polling { color: var(--accent); background: rgba(59, 130, 246, .08); }
.spinner {
  width: 13px;
  height: 13px;
  border: 2px solid rgba(59, 130, 246, .25);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* 概览统计 */
.overview-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 12px;
}
.stat-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 2px 12px rgba(15, 23, 42, .04);
}
.stat-card div { display: flex; flex-direction: column; gap: 2px; }
.stat-card strong { font-size: 23px; line-height: 1.1; }
.stat-card span { font-size: 12px; color: var(--text-muted); }
.stat-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 10px;
  font-weight: 700;
  font-size: 15px;
}
.stat-card.healthy .stat-icon { background: rgba(22, 163, 74, .12); color: #16a34a; }
.stat-card.warning .stat-icon { background: rgba(217, 119, 6, .12); color: #d97706; }
.stat-card.error .stat-icon { background: rgba(220, 38, 38, .12); color: #dc2626; }
.stat-card.unknown .stat-icon { background: var(--surface-2); color: var(--text-muted); }
.stat-card.schedule { grid-column: span 1; }
.stat-card.schedule .stat-icon { background: rgba(59, 130, 246, .1); color: var(--accent); }
.schedule-copy span { max-width: 100%; line-height: 1.35; }

/* 数据集卡片 */
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}
.section-title { margin: 0; font-size: 17px; font-weight: 650; }
.section-hint { font-size: 12px; color: var(--text-muted); }
.section-rules { margin: 4px 0 0; }
.section-badge {
  padding: 4px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 999px;
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
}
.dataset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 14px;
}
.dataset-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 2px 12px rgba(15, 23, 42, .04);
  cursor: pointer;
  transition: border-color .16s ease, transform .16s ease, box-shadow .16s ease;
}
.dataset-card:hover {
  border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
  transform: translateY(-1px);
  box-shadow: 0 6px 18px rgba(15, 23, 42, .08);
}
.dataset-card.selected {
  border-color: var(--accent);
  box-shadow: 0 0 0 2px rgba(59, 130, 246, .18);
}
.card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.card-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.card-title strong { font-size: 15px; }
.freq-chip {
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--text-muted);
  font-size: 11px;
  white-space: nowrap;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-muted);
  flex-shrink: 0;
}
.tone-healthy .status-dot { background: #16a34a; }
.tone-warning .status-dot { background: #d97706; }
.tone-error .status-dot { background: #dc2626; }
.tone-unknown .status-dot, .tone-unsupported .status-dot { background: var(--text-muted); }
.card-source { font-size: 12px; color: var(--text-muted); }
.card-metrics {
  display: grid;
  grid-template-columns: 1.4fr 1fr 1fr;
  gap: 10px;
}
.metric {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}
.metric span { font-size: 11px; color: var(--text-muted); }
.metric strong { font-size: 12px; line-height: 1.35; word-break: break-all; }
.metric em { font-style: normal; opacity: .75; }
.card-issue {
  margin: 0;
  padding: 7px 10px;
  border-radius: var(--radius-sm);
  background: rgba(217, 119, 6, .08);
  color: #b45309;
  font-size: 12px;
  line-height: 1.45;
}
.card-issue.none {
  background: rgba(22, 163, 74, .07);
  color: #15803d;
}
.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-top: auto;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.last-run { display: flex; flex-direction: column; font-size: 11px; line-height: 1.4; }
.last-run em { font-style: normal; opacity: .75; }

/* 状态徽章与按钮 */
.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
}
.status-healthy { color: #15803d; background: rgba(22, 163, 74, .12); }
.status-warning { color: #b45309; background: rgba(217, 119, 6, .13); }
.status-error { color: #dc2626; background: rgba(220, 38, 38, .12); }
.status-unknown, .status-unsupported { color: var(--text-muted); background: var(--surface-2); }
.mini-actions { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
.btn-mini {
  padding: 5px 9px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface-2);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
  transition: border-color .15s ease, background .15s ease, color .15s ease;
}
.btn-mini:hover:not(:disabled) { border-color: var(--accent); background: rgba(59, 130, 246, .08); }
.btn-mini:disabled { opacity: .5; cursor: not-allowed; }
.btn-mini.danger { color: var(--danger); border-color: rgba(239, 68, 68, .4); }
.btn-mini.danger:hover:not(:disabled) { background: rgba(239, 68, 68, .08); }

/* 详情面板 */
.card-panel {
  padding: 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.no-partition {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}
.no-partition-title { font-weight: 600; font-size: 14px; }
.summary-rows { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
.summary-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.summary-row span { font-size: 11px; color: var(--text-muted); }
.summary-row strong { font-size: 13px; }
.partition-table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius-sm); }
.partition-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.partition-table th, .partition-table td {
  padding: 10px 12px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
}
.partition-table th { color: var(--text-muted); font-weight: 600; background: var(--surface-2); white-space: nowrap; }
.partition-table tbody tr:last-child td { border-bottom: 0; }
.partition-table tbody tr:hover { background: rgba(59, 130, 246, .04); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; }
.mono em { font-style: normal; opacity: .7; }
.block { display: block; }
.issue-cell { color: var(--text-muted); min-width: 180px; }
.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 14px;
  color: var(--text-muted);
  font-size: 13px;
}

@media (max-width: 1080px) {
  .overview-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat-card.schedule { grid-column: span 2; }
}
@media (max-width: 760px) {
  .page-head { flex-direction: column; }
  .head-actions { width: 100%; }
  .head-actions .btn { flex: 1; }
  .overview-grid { grid-template-columns: 1fr 1fr; }
  .dataset-grid { grid-template-columns: 1fr; }
  .card-footer { flex-direction: column; align-items: stretch; }
  .mini-actions { justify-content: flex-start; }
}
</style>
