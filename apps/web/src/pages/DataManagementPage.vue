<template>
  <div class="data-management-page">
    <section class="command-center">
      <header class="page-head">
        <div class="head-copy">
          <span class="eyebrow">DATA OPERATIONS</span>
          <h1 class="page-title">数据管理</h1>
          <p class="page-subtitle">集中查看外部数据健康度，并在需要时执行检查、补数与修复。</p>
        </div>
        <div class="head-actions">
          <button class="btn btn-ghost" :disabled="busy" @click="runGlobal('check')">
            <span aria-hidden="true">⌕</span> 检查全部质量
          </button>
          <button class="btn btn-primary" :disabled="busy" @click="runGlobal('sync_latest')">
            <span aria-hidden="true">↻</span> 同步全部数据
          </button>
        </div>
      </header>

      <div v-if="overview" class="health-strip">
        <div class="health-summary">
          <span class="health-orb" :class="overallHealthTone" aria-hidden="true">{{ overallHealthIcon }}</span>
          <div>
            <span class="summary-label">整体健康度</span>
            <strong>{{ overallHealthLabel }}</strong>
            <p>{{ overallHealthDescription }}</p>
          </div>
        </div>
        <div class="health-stats" aria-label="数据集健康统计">
          <button class="health-stat healthy" :class="{ active: statusFilter === 'healthy' }" @click="setStatusFilter('healthy')">
            <strong>{{ overview.healthy_count }}</strong><span>健康</span>
          </button>
          <button class="health-stat warning" :class="{ active: statusFilter === 'warning' }" @click="setStatusFilter('warning')">
            <strong>{{ overview.warning_count }}</strong><span>需关注</span>
          </button>
          <button class="health-stat error" :class="{ active: statusFilter === 'error' }" @click="setStatusFilter('error')">
            <strong>{{ overview.error_count }}</strong><span>异常</span>
          </button>
          <button class="health-stat unknown" :class="{ active: statusFilter === 'unknown' }" @click="setStatusFilter('unknown')">
            <strong>{{ overview.unknown_count }}</strong><span>待检查</span>
          </button>
        </div>
        <div class="schedule-status">
          <span class="schedule-icon" aria-hidden="true">◷</span>
          <div><span>自动同步</span><strong>{{ overview.schedule_time }}</strong></div>
        </div>
      </div>
    </section>

    <div v-if="error" class="notice error">{{ error }}</div>
    <div v-if="overview && hasNoSnapshot" class="notice snapshot-hint">
      <span class="hint-icon" aria-hidden="true">◔</span>
      <div class="hint-copy">
        <strong>尚未生成健康快照</strong>
        <p>健康快照不会在后端启动时自动生成，只会在手动执行「检查 / 同步」或数据摄取任务完成后更新。点击右侧按钮即可生成首次快照。</p>
      </div>
      <button class="btn-mini" :disabled="busy" @click="runGlobal('check')">立即检查全部质量</button>
    </div>
    <div v-if="polling" class="notice polling">
      <span class="spinner" aria-hidden="true"></span>
      数据维护任务执行中，完成后自动刷新健康状态…
    </div>
    <div v-if="resultNotice" class="notice success">{{ resultNotice }}</div>

    <section v-if="overview" class="dataset-section">
      <div class="section-head">
        <div>
          <span class="section-kicker">DATASETS</span>
          <h2 class="section-title">受管数据集 <em>{{ filteredDatasets.length }} / {{ overview.datasets.length }}</em></h2>
        </div>
        <div class="dataset-toolbar">
          <div class="filter-group" aria-label="按状态筛选数据集">
            <button :class="{ active: statusFilter === 'all' }" @click="setStatusFilter('all')">全部</button>
            <button :class="{ active: statusFilter === 'error' }" @click="setStatusFilter('error')">异常优先</button>
            <button :class="{ active: statusFilter === 'warning' }" @click="setStatusFilter('warning')">需关注</button>
          </div>
          <span class="section-hint">选择数据集查看分区明细</span>
        </div>
      </div>
      <div v-if="loading" class="loading-state">加载数据健康状态…</div>
      <div v-else class="dataset-grid">
        <article
          v-for="item in filteredDatasets"
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
              <div><strong>{{ item.display_name }}</strong><span class="card-source">{{ item.source_name ?? item.source_label }}</span></div>
            </div>
            <div class="card-labels"><span class="freq-chip">{{ item.frequency }}</span><span class="status-badge" :class="`status-${item.health_status}`">{{ statusText(item.health_status) }}</span></div>
          </header>

          <div class="card-metrics">
            <div class="metric">
              <span>数据最新至</span>
              <strong>{{ item.latest_date ?? '—' }}</strong>
              <em>目标 {{ item.expected_date ?? '按发布周期' }}</em>
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

          <p class="card-issue" :class="{ none: item.health_status === 'healthy' }">
            <span aria-hidden="true">{{ item.health_status === 'healthy' ? '✓' : '!' }}</span>{{ formatIssue(item) }}
          </p>

          <footer class="card-footer" @click.stop>
            <span class="last-run muted">
              <template v-if="item.last_run_id">
                最近检查 · {{ runStatusText(item.last_run_status) }}<em>{{ formatClock(item.last_checked_at) }}</em>
              </template>
              <template v-else>尚未执行统一检查</template>
            </span>
            <div class="mini-actions">
              <button v-if="supports(item, 'check')" class="btn-mini" :disabled="busy" @click="runOperation('check', item.dataset_key)">检查</button>
              <button v-if="supports(item, 'sync_latest')" class="btn-mini" :disabled="busy" @click="runOperation('sync_latest', item.dataset_key)">补最新</button>
              <button v-if="supports(item, 'repair_gaps')" class="btn-mini" :disabled="busy" @click="runOperation('repair_gaps', item.dataset_key)">修复缺口</button>
              <button v-if="supports(item, 'rebuild')" class="btn-mini danger" :disabled="busy" @click="runOperation('rebuild', item.dataset_key)">全量重拉</button>
            </div>
          </footer>
        </article>
        <div v-if="filteredDatasets.length === 0" class="empty-filter-state">
          <span aria-hidden="true">✓</span><strong>没有符合当前筛选的数据集</strong><button class="btn-mini" @click="setStatusFilter('all')">查看全部</button>
        </div>
      </div>
    </section>

    <!-- 分区详情 -->
    <section v-if="detail" class="detail-section card-panel">
      <div class="section-head">
        <div>
          <h2 class="section-title">{{ detail.dataset.display_name }}</h2>
          <p class="section-rules muted"><span>检查定义：</span>{{ formatQualityRules(detail.quality_rules) }}</p>
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
                  <button v-if="supports(detail.dataset, 'check')" class="btn-mini" :disabled="busy" @click="runOperation('check', detail.dataset.dataset_key, item.partition_key)">检查</button>
                  <button v-if="supports(detail.dataset, 'sync_latest')" class="btn-mini" :disabled="busy" @click="runOperation('sync_latest', detail.dataset.dataset_key, item.partition_key)">补最新</button>
                  <button v-if="supports(detail.dataset, 'repair_gaps')" class="btn-mini" :disabled="busy" @click="runOperation('repair_gaps', detail.dataset.dataset_key, item.partition_key)">修复</button>
                  <button v-if="supports(detail.dataset, 'rebuild')" class="btn-mini danger" :disabled="busy" @click="runOperation('rebuild', detail.dataset.dataset_key, item.partition_key)">重拉</button>
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
const resultNotice = ref<string | null>(null)
const statusFilter = ref<'all' | 'healthy' | 'warning' | 'error' | 'unknown'>('all')
const route = useRoute()

/** 是否有维护任务正在执行。 */
const busy = computed(() => submitting.value || polling.value)

/**
 * 是否尚未生成任何健康快照。
 *
 * 快照仅在手动触发或数据摄取任务执行后生成，后端启动不再自动检查，
 * 因此首次部署时快照行数为 0，页面需要给出友好提示而不是显示成异常。
 * 字段缺失（前端已更新、后端未重启）时按 false 处理，回退到按数据集
 * 状态展示的旧逻辑，避免误报"无快照"。
 */
const hasNoSnapshot = computed(() => overview.value?.snapshot_count === 0)

/** 筛选后的数据集保持异常、告警优先，便于先处理需要关注的内容。 */
const filteredDatasets = computed(() => {
  if (!overview.value) return []
  const priority: Record<string, number> = { error: 0, warning: 1, unknown: 2, healthy: 3, unsupported: 4 }
  return overview.value.datasets
    .filter((item) => statusFilter.value === 'all' || item.health_status === statusFilter.value)
    .slice()
    .sort((left, right) => priority[left.health_status] - priority[right.health_status])
})

const overallHealthLabel = computed(() => {
  if (!overview.value) return '等待检查'
  if (hasNoSnapshot.value) return '尚未生成健康快照'
  if (overview.value.error_count > 0) return '需要处理异常'
  if (overview.value.warning_count > 0) return '运行中，需关注'
  if (overview.value.unknown_count > 0) return '等待首次检查'
  return '所有数据集健康'
})
const overallHealthTone = computed(() => {
  if (!overview.value) return 'unknown'
  if (hasNoSnapshot.value) return 'unknown'
  if (overview.value.error_count > 0) return 'error'
  if (overview.value.warning_count > 0) return 'warning'
  if (overview.value.unknown_count > 0) return 'unknown'
  return 'healthy'
})
const overallHealthIcon = computed(() => ({ healthy: '✓', warning: '!', error: '×', unknown: '·' })[overallHealthTone.value])
const overallHealthDescription = computed(() => {
  if (!overview.value) return ''
  const count = overview.value.datasets.length
  if (hasNoSnapshot.value) return `共 ${count} 个数据集尚无快照，手动检查或数据同步后自动生成。`
  if (overview.value.error_count > 0) return `${overview.value.error_count} 个数据集异常，建议优先检查。`
  if (overview.value.warning_count > 0) return `${overview.value.warning_count} 个数据集需关注，共管理 ${count} 个数据集。`
  if (overview.value.unknown_count > 0) return `${overview.value.unknown_count} 个数据集尚未生成质量快照。`
  return `${count} 个数据集均已通过最近一次质量检查。`
})

function setStatusFilter(status: typeof statusFilter.value): void {
  statusFilter.value = statusFilter.value === status && status !== 'all' ? 'all' : status
}

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

/** 判断数据集是否声明支持某项维护操作。 */
function supports(item: { supported_operations: string[] }, operation: DataManagementOperation): boolean {
  return item.supported_operations.includes(operation)
}

/** 将质量规则安全格式化，兼容旧接口字符串和异常的逐字符数组。 */
function formatQualityRules(rules: string[] | string | null | undefined): string {
  if (!rules) return '暂无检查定义'
  if (typeof rules === 'string') return rules
  if (rules.length === 0) return '暂无检查定义'
  // 兼容历史响应把单条规则拆成字符数组的情况，避免渲染成“代；码；…”。
  if (rules.length > 1 && rules.every((rule) => [...rule].length <= 1)) {
    return rules.join('')
  }
  return rules.join('；')
}

/** 将 UTC 时间串展示为本地可读时间。 */
function formatClock(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

/** 将结构化健康问题转换为简短、可读的表格说明。 */
function formatIssue(item: DataSetDetailResponse['dataset']): string {
  const summary = item.issue_summary
  if (!summary) return '尚未完成质量检查'
  const lastError = summary.last_error
  if (typeof lastError === 'string') return lastError
  const reasonLabels: Record<string, string> = {
    empty: '无可用数据',
    stale: '数据已过期',
    missing_dates: '存在日期缺口',
    invalid_values: '存在异常值',
    warnings: '存在数据告警',
  }
  const warnings = typeof summary.warning_count === 'number' ? summary.warning_count : 0
  const errors = typeof summary.error_count === 'number' ? summary.error_count : item.invalid_count
  const missing = typeof summary.missing_count === 'number' ? summary.missing_count : item.missing_count
  const details = [
    `缺口 ${missing}`,
    `错误 ${errors}`,
    warnings > 0 ? `告警 ${warnings}` : '',
  ].filter(Boolean)
  const reasons = Array.isArray(summary.reasons)
    ? summary.reasons
      .filter((value): value is string => typeof value === 'string')
      .map((reason) => reasonLabels[reason] ?? reason)
    : []
  const sample = Array.isArray(summary.missing_dates_sample)
    ? summary.missing_dates_sample.filter((value): value is string => typeof value === 'string')
    : []
  if (sample.length > 0) {
    reasons.push(`缺口样例：${sample.join('、')}${summary.missing_dates_truncated ? '…' : ''}`)
  }
  if (missing === 0 && errors === 0 && warnings === 0) return `已检查：${details.join(' · ')}`
  return [...details, ...reasons].filter(Boolean).join(' · ')
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
  resultNotice.value = null
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
      const metrics = run.metrics ?? {}
      const inserted = typeof metrics.records_inserted === 'number' ? metrics.records_inserted : null
      const repaired = typeof metrics.gaps_repaired === 'number' ? metrics.gaps_repaired : null
      const found = typeof metrics.gaps_found === 'number' ? metrics.gaps_found : null
      const updated = typeof metrics.records_updated === 'number' ? metrics.records_updated : null
      const skipped = typeof metrics.records_skipped === 'number' ? metrics.records_skipped : null
      const failed = Array.isArray(metrics.failed_partitions) ? metrics.failed_partitions.length : null
      if (inserted !== null || repaired !== null || updated !== null || found !== null) {
        resultNotice.value = `任务${run.status === 'success' ? '完成' : '结束'}：插入 ${inserted ?? 0} · 更新 ${updated ?? 0} · 跳过 ${skipped ?? 0} · 发现缺口 ${found ?? 0} · 修复缺口 ${repaired ?? 0}${failed ? ` · 失败分区 ${failed}` : ''}`
      }
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
  gap: 18px;
  max-width: 1480px;
  padding: 4px 0 32px;
}
.command-center {
  overflow: hidden;
  background: linear-gradient(115deg, #17233a 0%, var(--surface) 52%, #192640 100%);
  border: 1px solid color-mix(in srgb, var(--accent) 25%, var(--border));
  border-radius: 16px;
  box-shadow: var(--shadow);
}
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  padding: 28px 30px 24px;
}
.eyebrow, .section-kicker { color: #7db0ff; font-size: 10px; font-weight: 750; letter-spacing: .13em; }
.page-title { margin: 5px 0 3px; font-size: 30px; font-weight: 720; letter-spacing: .01em; }
.page-subtitle, .muted { color: var(--text-muted); font-size: 13px; }
.page-subtitle { max-width: 580px; }
.head-actions { display: flex; gap: 10px; flex-shrink: 0; }
.head-actions .btn { display: inline-flex; align-items: center; gap: 6px; }

.health-strip { display: grid; grid-template-columns: 1.25fr 1.65fr auto; border-top: 1px solid rgba(148, 163, 184, .16); }
.health-summary, .schedule-status { display: flex; align-items: center; gap: 12px; padding: 18px 30px; }
.health-summary { border-right: 1px solid rgba(148, 163, 184, .16); }
.health-orb { display: inline-flex; align-items: center; justify-content: center; width: 42px; height: 42px; border-radius: 13px; font-size: 20px; font-weight: 800; }
.health-orb.healthy { color: #5ee798; background: rgba(34, 197, 94, .15); }
.health-orb.warning { color: #fbbf24; background: rgba(245, 158, 11, .14); }
.health-orb.error { color: #f87171; background: rgba(239, 68, 68, .15); }
.health-orb.unknown { color: #94a3b8; background: rgba(148, 163, 184, .13); }
.summary-label, .schedule-status span { display: block; color: var(--text-muted); font-size: 11px; }
.health-summary strong { display: block; margin-top: 1px; font-size: 15px; }
.health-summary p { margin-top: 2px; color: var(--text-muted); font-size: 11px; }
.health-stats { display: grid; grid-template-columns: repeat(4, 1fr); align-items: stretch; }
.health-stat { display: flex; flex-direction: column; justify-content: center; gap: 2px; padding: 12px 14px; border: 0; border-right: 1px solid rgba(148, 163, 184, .13); color: var(--text); background: transparent; text-align: left; transition: background .15s ease; }
.health-stat:hover, .health-stat.active { background: rgba(255, 255, 255, .055); }
.health-stat strong { font-size: 20px; line-height: 1; }.health-stat span { font-size: 11px; color: var(--text-muted); }
.health-stat.healthy strong { color: #4ade80; }.health-stat.warning strong { color: #fbbf24; }.health-stat.error strong { color: #fb7185; }.health-stat.unknown strong { color: #cbd5e1; }
.schedule-status { min-width: 160px; padding-left: 20px; }
.schedule-status strong { display: block; max-width: 150px; font-size: 12px; line-height: 1.35; }
.schedule-icon { display: inline-flex !important; align-items: center; justify-content: center; width: 28px; height: 28px; border: 1px solid rgba(125, 176, 255, .35); border-radius: 9px; color: #7db0ff !important; font-size: 16px !important; }

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
.notice.success { color: #15803d; background: rgba(34, 197, 94, .08); }
.notice.snapshot-hint {
  align-items: flex-start;
  gap: 12px;
  padding: 13px 16px;
  color: var(--text);
  background: rgba(59, 130, 246, .07);
  border: 1px solid rgba(59, 130, 246, .22);
}
.notice.snapshot-hint .hint-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  border-radius: 8px;
  color: #7db0ff;
  background: rgba(59, 130, 246, .16);
  font-size: 15px;
}
.notice.snapshot-hint .hint-copy { flex: 1; min-width: 0; }
.notice.snapshot-hint strong { display: block; font-size: 13px; }
.notice.snapshot-hint p { margin: 3px 0 0; color: var(--text-muted); font-size: 12px; line-height: 1.5; }
.notice.snapshot-hint .btn-mini { flex-shrink: 0; align-self: center; }
.spinner {
  width: 13px;
  height: 13px;
  border: 2px solid rgba(59, 130, 246, .25);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}
.section-head > div { min-width: 0; flex: 1; }
.section-title { margin: 2px 0 0; font-size: 19px; font-weight: 680; }
.section-title em { color: var(--text-muted); font-size: 12px; font-style: normal; font-weight: 500; }
.section-hint { font-size: 12px; color: var(--text-muted); }
.dataset-toolbar { display: flex; align-items: center; gap: 13px; }
.filter-group { display: flex; padding: 3px; background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px; }
.filter-group button { padding: 4px 8px; border: 0; border-radius: 5px; color: var(--text-muted); background: transparent; font-size: 11px; transition: color .15s ease, background .15s ease; }
.filter-group button:hover { color: var(--text); }.filter-group button.active { color: var(--text); background: rgba(255, 255, 255, .12); }
.section-rules { margin: 4px 0 0; line-height: 1.5; word-break: normal; overflow-wrap: break-word; }
.section-rules span { color: var(--text); }
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
  grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  gap: 12px;
}
.dataset-card {
  display: flex;
  flex-direction: column;
  gap: 13px;
  padding: 17px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 5px 18px rgba(0, 0, 0, .09);
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
.card-title strong { display: block; font-size: 15px; line-height: 1.35; }
.card-labels { display: flex; align-items: center; gap: 5px; }
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
.card-source { display: block; margin-top: 1px; color: var(--text-muted); font-size: 11px; }
.card-metrics {
  display: grid;
  grid-template-columns: 1.25fr .85fr .85fr;
  gap: 8px;
}
.metric {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 9px 10px;
  background: var(--surface-2);
  border-radius: var(--radius-sm);
}
.metric span { font-size: 11px; color: var(--text-muted); }
.metric strong { font-size: 12px; line-height: 1.35; word-break: break-all; }.metric em { color: var(--text-muted); font-size: 10px; font-style: normal; }
.card-issue {
  margin: 0;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  min-height: 39px;
  padding: 8px 10px;
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
  padding-top: 12px;
  border-top: 1px dashed var(--border);
}
.last-run { display: flex; flex-direction: column; font-size: 10px; line-height: 1.4; }
.last-run em { font-style: normal; opacity: .75; }

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
.empty-filter-state { grid-column: 1 / -1; display: flex; align-items: center; justify-content: center; gap: 9px; min-height: 140px; border: 1px dashed var(--border); border-radius: 12px; color: var(--text-muted); }.empty-filter-state > span { color: var(--success); font-size: 18px; }.empty-filter-state strong { color: var(--text); font-size: 13px; }

.card-panel {
  padding: 20px;
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
  .health-strip { grid-template-columns: 1fr 1.4fr; }.schedule-status { grid-column: 1 / -1; border-top: 1px solid rgba(148, 163, 184, .16); }.dataset-toolbar { gap: 8px; }
}
@media (max-width: 760px) {
  .page-head { flex-direction: column; padding: 22px 20px 18px; }
  .head-actions { width: 100%; }
  .head-actions .btn { flex: 1; }
  .health-strip { grid-template-columns: 1fr; }.health-summary { padding: 16px 20px; border-right: 0; }.health-stats { border-top: 1px solid rgba(148, 163, 184, .16); }.health-stat { padding: 12px 10px; }.schedule-status { padding: 14px 20px; }.section-head, .dataset-toolbar { align-items: flex-start; flex-direction: column; }.section-hint { display: none; }
  .notice.snapshot-hint { flex-direction: column; align-items: flex-start; }
  .notice.snapshot-hint .btn-mini { align-self: stretch; }
  .dataset-grid { grid-template-columns: 1fr; }
  .card-footer { flex-direction: column; align-items: stretch; }
  .mini-actions { justify-content: flex-start; }
}
</style>
