<template>
  <div class="page">
    <!-- 页头 -->
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">数据管理</h1>
        <span class="count-badge">{{ datasets.length }} 个数据集</span>
        <span v-if="lastCheckedText" class="header-meta">最近检查 {{ lastCheckedText }}</span>
      </div>
      <div class="header-actions">
        <button class="btn-secondary" :disabled="busy" @click="submitOperation('check')">
          {{ operationLabel('check') }}
        </button>
        <button class="btn-primary" :disabled="busy" @click="submitOperation('sync_latest')">
          {{ operationLabel('sync_latest') }}
        </button>
      </div>
    </div>

    <!-- 全局提示 -->
    <div v-if="error" class="error-tip">{{ error }}</div>
    <div v-if="overview && hasNoSnapshot" class="notice notice-hint">
      <span>
        尚未生成健康快照：快照不会在后端启动时自动生成，只在手动执行检查/同步或数据摄取任务完成后更新。
      </span>
      <button class="btn-secondary btn-sm" :disabled="busy" @click="submitOperation('check')">
        生成首次快照
      </button>
    </div>
    <div v-if="banner" class="refresh-banner" :class="banner.ok ? 'banner-ok' : 'banner-err'">
      {{ banner.text }}
    </div>
    <div v-if="polling" class="polling-banner">
      数据维护任务执行中，完成后自动刷新健康状态…
    </div>

    <div v-if="loading && !overview" class="loading">加载数据健康状态…</div>

    <template v-else-if="overview">
      <!-- 状态汇总：计数即筛选入口 -->
      <div class="summary-card">
        <button
          v-for="tab in statusTabs"
          :key="tab.key"
          class="summary-item"
          :class="{ active: statusFilter === tab.key }"
          @click="toggleStatusFilter(tab.key)"
        >
          <strong :class="`tone-${tab.key}`">{{ tab.count }}</strong>
          <span>{{ tab.label }}</span>
        </button>
        <div class="summary-side">
          <div>
            <span class="k">自动同步</span>
            <span class="v">{{ overview.schedule_time }}</span>
          </div>
          <div>
            <span class="k">最近检查</span>
            <span class="v">{{ lastCheckedText || '尚未检查' }}</span>
          </div>
        </div>
      </div>

      <!-- 主从布局：左侧数据集，右侧摘要与分区明细 -->
      <div class="layout">
        <aside class="list">
          <div
            v-for="item in filteredDatasets"
            :key="item.dataset_key"
            class="list-card"
            :class="{ active: selectedKey === item.dataset_key }"
            @click="selectDataSet(item.dataset_key)"
          >
            <div class="list-top">
              <span class="name">{{ item.display_name }}</span>
              <span class="status-badge" :class="`status-${item.health_status}`">
                {{ statusText(item.health_status) }}
              </span>
            </div>
            <div class="list-sub">{{ item.frequency }} · {{ scopeText(item) }}</div>
            <div class="list-meta">
              <span>最新 {{ item.latest_date ?? '—' }}</span>
              <span :class="{ 'text-warn': issueTotal(item) > 0 }">{{ issueTotalText(item) }}</span>
            </div>
          </div>
          <div v-if="filteredDatasets.length === 0" class="list-empty">
            没有符合当前筛选的数据集
            <button class="btn-mini" @click="toggleStatusFilter('all')">查看全部</button>
          </div>
        </aside>

        <section class="detail">
          <div v-if="!selected" class="card empty-card">
            从左侧选择一个数据集，查看它的检查规则、维护操作与分区明细。
          </div>
          <template v-else>
            <!-- 数据集摘要 -->
            <div class="card">
              <div class="card-head">
                <div class="card-title-group">
                  <span class="card-title">{{ selected.display_name }}</span>
                  <span class="status-badge" :class="`status-${selected.health_status}`">
                    {{ statusText(selected.health_status) }}
                  </span>
                  <span class="type-badge">{{ selected.frequency }}</span>
                  <span class="type-badge">{{ scopeText(selected) }}</span>
                </div>
                <div class="actions">
                  <RouterLink
                    v-if="dataPageFor(selected.dataset_key)"
                    class="btn-mini"
                    :to="dataPageFor(selected.dataset_key)?.path ?? '/'"
                  >
                    查看{{ dataPageFor(selected.dataset_key)?.label }}
                  </RouterLink>
                  <button
                    v-if="supports(selected, 'check')"
                    class="btn-mini"
                    :disabled="busy"
                    @click="submitOperation('check', selected.dataset_key)"
                  >
                    检查
                  </button>
                  <button
                    v-if="supports(selected, 'sync_latest')"
                    class="btn-mini"
                    :disabled="busy"
                    @click="submitOperation('sync_latest', selected.dataset_key)"
                  >
                    补最新
                  </button>
                  <button
                    v-if="supports(selected, 'repair_gaps')"
                    class="btn-mini"
                    :disabled="busy"
                    @click="submitOperation('repair_gaps', selected.dataset_key)"
                  >
                    修复缺口
                  </button>
                  <button
                    v-if="supports(selected, 'rebuild')"
                    class="btn-mini btn-danger"
                    :disabled="busy"
                    @click="openRebuild(selected.dataset_key)"
                  >
                    全量重拉
                  </button>
                </div>
              </div>

              <div class="kv-grid">
                <div>
                  <span class="k">数据范围</span>
                  <span class="v mono">
                    {{ selected.earliest_date ?? '—' }} ~ {{ selected.latest_date ?? '—' }}
                  </span>
                </div>
                <div>
                  <span class="k">目标日期</span>
                  <span class="v mono">{{ selected.expected_date ?? '按发布周期' }}</span>
                </div>
                <div>
                  <span class="k">记录数</span>
                  <span class="v mono">{{ selected.record_count.toLocaleString() }}</span>
                </div>
                <div>
                  <span class="k">缺口 / 异常</span>
                  <span class="v mono" :class="{ 'text-warn': selected.missing_count > 0 || selected.invalid_count > 0 }">
                    {{ selected.missing_count.toLocaleString() }} / {{ selected.invalid_count.toLocaleString() }}
                  </span>
                </div>
                <div>
                  <span class="k">分区</span>
                  <span class="v">{{ partitionSummary(selected) }}</span>
                </div>
                <div>
                  <span class="k">最近实际来源</span>
                  <span class="v">{{ selected.source_name ?? '尚未抓取' }}</span>
                </div>
                <div>
                  <span class="k">最近检查</span>
                  <span class="v">
                    <template v-if="selected.last_checked_at">
                      {{ formatCnTime(selected.last_checked_at) }}
                      <template v-if="selected.last_run_status">
                        （{{ runStatusText(selected.last_run_status) }}）
                      </template>
                    </template>
                    <template v-else-if="selected.last_run_status">
                      尚未成功检查（最近一次{{ runStatusText(selected.last_run_status) }}）
                    </template>
                    <template v-else>尚未检查</template>
                  </span>
                </div>
                <div>
                  <span class="k">最近同步成功</span>
                  <span class="v">{{ formatCnTime(selected.last_success_at) }}</span>
                </div>
                <div class="wide">
                  <span class="k">来源策略</span>
                  <span class="v">{{ selected.source_label }}</span>
                </div>
                <div class="wide">
                  <span class="k">检查定义</span>
                  <span class="v">{{ selected.quality_rules.join('；') || '暂无检查定义' }}</span>
                </div>
              </div>

              <div class="issue-line" :class="{ ok: issueTotal(selected) === 0 && !!selected.last_checked_at }">
                {{ datasetIssueText(selected) }}
              </div>
              <div v-if="syncHint(selected)" class="hint">{{ syncHint(selected) }}</div>
              <div v-if="missingSample(selected).length" class="hint">
                缺口样例：{{ missingSample(selected).join('、') }}{{ missingSampleTruncated(selected) ? ' …' : '' }}
              </div>
            </div>

            <!-- 分区明细 -->
            <div class="card">
              <div class="card-head">
                <div class="card-title-group">
                  <span class="card-title">分区明细</span>
                  <span class="count-badge">{{ detail?.total ?? 0 }} 个</span>
                  <span v-if="selected.partition_label" class="type-badge">
                    按{{ selected.partition_label }}分区
                  </span>
                </div>
              </div>

              <div v-if="selected.partition_label" class="filter-row">
                <input
                  v-model.trim="partitionKeyword"
                  class="form-input filter-input"
                  :placeholder="`${selected.partition_label}代码 / 名称`"
                  @keyup.enter="applyPartitionFilter"
                />
                <select v-model="partitionStatus" class="form-select" @change="applyPartitionFilter">
                  <option value="">全部状态</option>
                  <option value="problem">异常或需关注{{ problemPartitionTotal(selected) ? `（${problemPartitionTotal(selected)}）` : '' }}</option>
                  <option value="error">异常</option>
                  <option value="warning">需关注</option>
                  <option value="unknown">待检查</option>
                  <option value="unsupported">不支持</option>
                  <option value="healthy">健康</option>
                </select>
                <button class="btn-secondary btn-sm" @click="applyPartitionFilter">查询</button>
                <button
                  v-if="hasPartitionFilter"
                  class="btn-mini"
                  @click="clearPartitionFilter"
                >
                  清空筛选
                </button>
                <span v-if="exactPartitionKey" class="exact-tag">
                  仅显示分区 {{ exactPartitionKey }}
                  <button class="exact-tag-close" aria-label="取消分区过滤" @click="clearExactPartition">×</button>
                </span>
              </div>

              <div v-else class="hint">
                该数据集没有独立分区，健康结论以数据集汇总为准。
              </div>

              <div v-if="detailLoading" class="loading">加载分区明细…</div>
              <div v-else-if="!detail || detail.items.length === 0" class="empty">
                {{ detailEmptyText }}
              </div>
              <div v-else class="table-scroll">
                <table class="table">
                  <thead>
                    <tr>
                      <th>{{ selected.partition_label ?? '分区' }}</th>
                      <th>状态</th>
                      <th>数据范围（实际 ~ 目标）</th>
                      <th class="num-cell">记录数</th>
                      <th class="num-cell">缺口</th>
                      <th class="num-cell">异常</th>
                      <th>问题</th>
                      <th>维护</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="item in detail.items" :key="item.partition_key">
                      <td>
                        <strong class="code-mono">{{ item.partition_key }}</strong>
                        <span v-if="item.partition_name && item.partition_name !== item.partition_key" class="block muted-text">
                          {{ item.partition_name }}
                        </span>
                      </td>
                      <td>
                        <span class="status-badge" :class="`status-${item.health_status}`">
                          {{ statusText(item.health_status) }}
                        </span>
                      </td>
                      <td class="mono">
                        {{ item.latest_date ?? '—' }} ~ {{ item.expected_date ?? '—' }}
                      </td>
                      <td class="num-cell">{{ item.record_count.toLocaleString() }}</td>
                      <td class="num-cell" :class="{ 'text-warn': item.missing_count > 0 }">
                        {{ item.missing_count.toLocaleString() }}
                      </td>
                      <td class="num-cell" :class="{ 'text-warn': item.invalid_count > 0 }">
                        {{ item.invalid_count.toLocaleString() }}
                      </td>
                      <td class="issue-cell">{{ partitionIssueText(item) }}</td>
                      <td class="action-cell">
                        <button
                          v-if="supports(selected, 'check')"
                          class="btn-mini"
                          :disabled="busy"
                          @click="submitOperation('check', selected.dataset_key, item.partition_key)"
                        >
                          检查
                        </button>
                        <button
                          v-if="supports(selected, 'sync_latest')"
                          class="btn-mini"
                          :disabled="busy"
                          @click="submitOperation('sync_latest', selected.dataset_key, item.partition_key)"
                        >
                          补最新
                        </button>
                        <button
                          v-if="supports(selected, 'repair_gaps')"
                          class="btn-mini"
                          :disabled="busy"
                          @click="submitOperation('repair_gaps', selected.dataset_key, item.partition_key)"
                        >
                          修复
                        </button>
                        <button
                          v-if="supports(selected, 'rebuild')"
                          class="btn-mini btn-danger"
                          :disabled="busy"
                          @click="openRebuild(selected.dataset_key, item.partition_key)"
                        >
                          重拉
                        </button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div v-if="detail && detail.total > detail.limit" class="pagination">
                <button
                  class="page-btn"
                  :disabled="detail.offset === 0"
                  @click="changePage(-1)"
                >
                  上一页
                </button>
                <span class="page-info">
                  {{ detail.offset + 1 }}–{{ Math.min(detail.offset + detail.limit, detail.total) }}
                  / 共 {{ detail.total }} 个分区
                </span>
                <button
                  class="page-btn"
                  :disabled="detail.offset + detail.limit >= detail.total"
                  @click="changePage(1)"
                >
                  下一页
                </button>
              </div>
            </div>
          </template>
        </section>
      </div>
    </template>

    <!-- 全量重拉确认弹窗 -->
    <div v-if="rebuildTarget" class="modal-overlay" @click.self="closeRebuild">
      <div class="modal">
        <div class="modal-title">全量重拉确认</div>
        <p class="modal-text">
          全量重拉会先抓取并校验数据，校验通过后替换
          <strong>{{ rebuildScopeText }}</strong>
          的现有数据；校验不通过时保留旧数据。该操作耗时长，不要作为常规刷新手段。
        </p>
        <div class="form-group">
          <label class="form-label">请输入确认令牌：{{ rebuildToken }}</label>
          <input
            v-model.trim="rebuildTokenInput"
            class="form-input"
            :placeholder="rebuildToken"
            @keyup.enter="confirmRebuild"
          />
        </div>
        <div v-if="rebuildError" class="form-error">{{ rebuildError }}</div>
        <div class="modal-actions">
          <button class="btn-secondary btn-sm" @click="closeRebuild">取消</button>
          <button class="btn-danger btn-sm" @click="confirmRebuild">确认重拉</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 统一数据管理页面。
 *
 * 采用与系统其它页面一致的主从布局：左侧为受管数据集列表（顶部状态计数即筛选入口），
 * 右侧为选中数据集的摘要卡片与分区明细表格。页面只负责编排统一后台任务
 * （检查 / 补最新 / 修复缺口 / 全量重拉）并跟踪其运行状态，抓取与质量规则由后端
 * `DataManagementService` 负责。
 *
 * 健康快照不会在后端启动时自动生成：首次部署快照为空时给出提示并引导手动执行
 * 首次检查；快照只在手动维护操作或数据摄取任务完成后刷新。
 */

import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { fetchRunDetail } from '../api/runs'
import {
  fetchDataManagementOverview,
  fetchDataSetDetail,
  triggerDataManagementOperation,
} from '../api/dataManagement'
import { usePolling } from '../composables/usePolling'
import { formatCnTime } from '../utils/date'
import type {
  DataManagementOperation,
  DataManagementOverview,
  DataSetDetailResponse,
  DataSetHealthSummary,
  ResearchRunDetail,
} from '../types/api'

/** 健康状态取值，与后端 data_health_snapshot.health_status 对齐。 */
type HealthStatus = 'healthy' | 'warning' | 'error' | 'unknown' | 'unsupported'
/** 状态筛选（含"全部"）。 */
type StatusFilter = 'all' | HealthStatus
/** 分区状态筛选：problem 表示异常 + 需关注。 */
type PartitionFilter = '' | 'problem' | HealthStatus

/** 分区明细分页大小。 */
const PAGE_SIZE = 50
/** 运行终态，用于停止轮询。 */
const TERMINAL_STATUSES = ['success', 'partial_success', 'failed', 'skipped']
/** 失败横幅保留时间（毫秒），成功横幅到点自动消失。 */
const BANNER_TIMEOUT_MS = 8000

/** 状态中文标签。 */
const STATUS_LABELS: Record<StatusFilter, string> = {
  all: '全部',
  healthy: '健康',
  warning: '需关注',
  error: '异常',
  unknown: '待检查',
  unsupported: '不支持',
}

/** 健康问题原因码中文标签。 */
const REASON_LABELS: Record<string, string> = {
  empty: '无可用数据',
  stale: '数据已过期',
  missing_dates: '存在日期缺口',
  invalid_values: '存在异常值',
  warnings: '存在数据告警',
}

/** 操作中文标签。 */
const OPERATION_LABELS: Record<DataManagementOperation, string> = {
  check: '检查',
  sync_latest: '同步最新',
  repair_gaps: '修复缺口',
  rebuild: '全量重拉',
}

/** 数据集对应的数据浏览页面（运维定位后可直接核对数据）。 */
const DATA_PAGE_BY_DATASET: Record<string, { label: string; path: string }> = {
  index_daily_bar: { label: '指数数据', path: '/indexes' },
  index_valuation: { label: '指数数据', path: '/indexes' },
  index_membership: { label: '指数数据', path: '/indexes' },
  industry_universe: { label: '申万行业', path: '/industries' },
  industry_daily_bar: { label: '申万行业', path: '/industries' },
  industry_membership: { label: '申万行业', path: '/industries' },
  stock_universe: { label: '个股数据', path: '/stocks' },
  stock_daily_close: { label: '个股数据', path: '/stocks' },
  stock_daily_basic: { label: '个股数据', path: '/stocks' },
  stock_moneyflow: { label: '个股数据', path: '/stocks' },
  macro_indicator: { label: '宏观指标', path: '/macro' },
}

/** 运行状态中文标签。 */
const RUN_STATUS_LABELS: Record<string, string> = {
  success: '成功',
  partial_success: '部分成功',
  failed: '失败',
  running: '执行中',
  pending: '待执行',
  skipped: '跳过',
}

/** 健康快照 issue_summary 的已知字段。 */
interface HealthIssueSummary {
  partition_count?: number
  error_partitions?: number
  warning_partitions?: number
  warning_count?: number
  reasons?: string[]
  missing_dates_sample?: string[]
  missing_dates_truncated?: boolean
  last_error?: string
  reason?: string
}

/** 数据管理任务单个数据集子项指标。 */
interface DataManageRunItem {
  dataset_key?: string
  status?: string
  records?: number
  records_inserted?: number
  records_updated?: number
  gaps_found?: number
  gaps_repaired?: number
  errors?: string[]
  error?: string
}

/** 数据管理任务运行指标（data_manage_operation / data_sync_all 的 metrics 结构）。 */
interface DataManageRunMetrics {
  success_count?: number
  partial_count?: number
  failed_count?: number
  reason?: string
  items?: DataManageRunItem[]
}

const overview = ref<DataManagementOverview | null>(null)
const detail = ref<DataSetDetailResponse | null>(null)
const selectedKey = ref<string | null>(null)
const loading = ref(false)
const detailLoading = ref(false)
const submitting = ref(false)
const error = ref<string | null>(null)
const banner = ref<{ text: string; ok: boolean } | null>(null)
const statusFilter = ref<StatusFilter>('all')
const activeRunId = ref<string | null>(null)
const activeOperation = ref<DataManagementOperation | null>(null)

const partitionKeyword = ref('')
const appliedKeyword = ref('')
const partitionStatus = ref<PartitionFilter>('')
const exactPartitionKey = ref('')
const detailOffset = ref(0)

const rebuildTarget = ref<{ datasetKey: string; partitionKey?: string } | null>(null)
const rebuildTokenInput = ref('')
const rebuildError = ref('')

const route = useRoute()
let bannerTimer: number | null = null

/** 全量数据集列表。 */
const datasets = computed<DataSetHealthSummary[]>(() => overview.value?.datasets ?? [])

/** 是否有维护任务正在执行（提交中或轮询中）。 */
const busy = computed(() => submitting.value || polling.value)

/** 是否尚未生成任何健康快照（字段缺失时按 false 处理，回退旧逻辑）。 */
const hasNoSnapshot = computed(() => overview.value?.snapshot_count === 0)

/** 当前选中的数据集摘要。 */
const selected = computed<DataSetHealthSummary | null>(
  () => datasets.value.find((item) => item.dataset_key === selectedKey.value) ?? null,
)

/** 状态计数，作为筛选入口。 */
const statusTabs = computed(() => {
  const data = overview.value
  return [
    { key: 'all' as StatusFilter, label: STATUS_LABELS.all, count: data?.datasets.length ?? 0 },
    { key: 'healthy' as StatusFilter, label: STATUS_LABELS.healthy, count: data?.healthy_count ?? 0 },
    { key: 'warning' as StatusFilter, label: STATUS_LABELS.warning, count: data?.warning_count ?? 0 },
    { key: 'error' as StatusFilter, label: STATUS_LABELS.error, count: data?.error_count ?? 0 },
    { key: 'unknown' as StatusFilter, label: STATUS_LABELS.unknown, count: data?.unknown_count ?? 0 },
    {
      key: 'unsupported' as StatusFilter,
      label: STATUS_LABELS.unsupported,
      count: data?.unsupported_count ?? 0,
    },
  ]
})

/** 按状态筛选并按"异常 → 需关注 → 待检查 → 健康 → 不支持"排序。 */
const filteredDatasets = computed(() => {
  const priority: Record<HealthStatus, number> = {
    error: 0,
    warning: 1,
    unknown: 2,
    healthy: 3,
    unsupported: 4,
  }
  return datasets.value
    .filter((item) => statusFilter.value === 'all' || item.health_status === statusFilter.value)
    .slice()
    .sort((left, right) => priority[left.health_status] - priority[right.health_status])
})

/** 全部数据集中最近一次检查时间。 */
const lastCheckedText = computed(() => {
  const times = datasets.value
    .map((item) => item.last_checked_at)
    .filter((value): value is string => typeof value === 'string')
    .sort()
  return times.length ? formatCnTime(times[times.length - 1]) : ''
})

/** 是否设置了任一分区筛选条件。 */
const hasPartitionFilter = computed(
  () => !!appliedKeyword.value || partitionStatus.value !== '' || !!exactPartitionKey.value,
)

/** 当前重拉操作的范围描述。 */
const rebuildScopeText = computed(() => {
  const target = rebuildTarget.value
  if (!target) return ''
  return target.partitionKey
    ? `${target.datasetKey} 的分区 ${target.partitionKey}`
    : `${target.datasetKey} 数据集`
})

/** 当前重拉操作要求的确认令牌（与后端校验规则一致）。 */
const rebuildToken = computed(() => {
  const target = rebuildTarget.value
  if (!target) return ''
  return `REBUILD:${target.datasetKey}:${target.partitionKey ?? 'ALL'}`
})

/** 分区明细空态文案，区分"无分区 / 未检查 / 筛选无结果"。 */
const detailEmptyText = computed(() => {
  if (!selected.value) return ''
  if (hasPartitionFilter.value) return '没有符合筛选条件的分区，可清空筛选后重试。'
  if (!selected.value.partition_label) return '该数据集没有独立分区，健康结论以数据集汇总为准。'
  if (!selected.value.last_checked_at) return '该数据集尚未生成分区快照，请先执行检查。'
  return '该数据集当前没有分区快照。'
})

/**
 * 读取 issue_summary 中的已知字段。
 *
 * 后端结构化摘要包含汇总行（分区数/异常分区/告警分区）与分区行（原因/缺口样例）
 * 两套键，这里统一收口并忽略未知键，避免在模板里直接索引 unknown。
 *
 * @param item - 数据集或分区摘要
 * @returns 归一化后的问题摘要
 */
function readIssue(item: DataSetHealthSummary): HealthIssueSummary {
  const raw = item.issue_summary
  if (!raw) return {}
  const result: HealthIssueSummary = {}
  if (typeof raw.partition_count === 'number') result.partition_count = raw.partition_count
  if (typeof raw.error_partitions === 'number') result.error_partitions = raw.error_partitions
  if (typeof raw.warning_partitions === 'number') result.warning_partitions = raw.warning_partitions
  if (typeof raw.warning_count === 'number') result.warning_count = raw.warning_count
  if (typeof raw.last_error === 'string') result.last_error = raw.last_error
  if (typeof raw.reason === 'string') result.reason = raw.reason
  if (typeof raw.missing_dates_truncated === 'boolean') {
    result.missing_dates_truncated = raw.missing_dates_truncated
  }
  if (Array.isArray(raw.reasons)) {
    result.reasons = raw.reasons.filter((value): value is string => typeof value === 'string')
  }
  if (Array.isArray(raw.missing_dates_sample)) {
    result.missing_dates_sample = raw.missing_dates_sample.filter(
      (value): value is string => typeof value === 'string',
    )
  }
  return result
}

/** 返回状态中文标签。 */
function statusText(status: string): string {
  return STATUS_LABELS[status as StatusFilter] ?? status
}

/** 返回运行状态中文标签。 */
function runStatusText(status: string | null): string {
  return status ? RUN_STATUS_LABELS[status] ?? status : '—'
}

/** 分区单位说明（无分区数据集返回固定文案）。 */
function scopeText(item: DataSetHealthSummary): string {
  return item.partition_label ? `${item.partition_label}分区` : '无分区'
}

/** 数据集问题总量：优先用分区口径（异常 + 需关注分区数），否则退回缺口 + 异常。 */
function issueTotal(item: DataSetHealthSummary): number {
  const issue = readIssue(item)
  if (typeof issue.error_partitions === 'number' || typeof issue.warning_partitions === 'number') {
    return (issue.error_partitions ?? 0) + (issue.warning_partitions ?? 0)
  }
  return item.missing_count + item.invalid_count
}

/** 列表卡片右上角的问题摘要文案。 */
function issueTotalText(item: DataSetHealthSummary): string {
  const issue = readIssue(item)
  if (typeof issue.error_partitions === 'number' || typeof issue.warning_partitions === 'number') {
    return `异常分区 ${issue.error_partitions ?? 0} · 关注 ${issue.warning_partitions ?? 0}`
  }
  return `缺口 ${item.missing_count.toLocaleString()} · 异常 ${item.invalid_count.toLocaleString()}`
}

/** 问题分区总数（异常 + 需关注），用于筛选下拉的提示数量。 */
function problemPartitionTotal(item: DataSetHealthSummary): number {
  const issue = readIssue(item)
  return (issue.error_partitions ?? 0) + (issue.warning_partitions ?? 0)
}

/** 数据集级分区规模描述。 */
function partitionSummary(item: DataSetHealthSummary): string {
  if (!item.partition_label) return '无分区'
  const issue = readIssue(item)
  if (typeof issue.partition_count !== 'number') return '尚未统计'
  const problems = problemPartitionTotal(item)
  return `${issue.partition_count} 个${problems ? `（异常/关注 ${problems}）` : ''}`
}

/** 数据集级问题说明行。 */
function datasetIssueText(item: DataSetHealthSummary): string {
  const issue = readIssue(item)
  if (issue.last_error) return `最近维护失败：${issue.last_error}`
  if (issue.reason) {
    // 汇总行在没有任何分区行时由后端写入 {"reason": "尚未检查"}
    return issue.reason === '尚未检查'
      ? '该数据集尚无分区快照，可点击「检查」生成。'
      : issue.reason
  }
  if (!item.last_checked_at) return '尚未执行统一检查，可点击「检查」生成该数据集的健康快照。'
  const numbers: string[] = []
  if (item.missing_count > 0) numbers.push(`缺口 ${item.missing_count.toLocaleString()}`)
  if (item.invalid_count > 0) numbers.push(`异常 ${item.invalid_count.toLocaleString()}`)
  if (issue.warning_count) numbers.push(`告警 ${issue.warning_count.toLocaleString()}`)
  const reasons = (issue.reasons ?? []).map((reason) => REASON_LABELS[reason] ?? reason)
  const text = [...numbers, ...reasons].join(' · ')
  return text || '最近一次检查未发现缺口或异常字段。'
}

/** 分区级问题说明文案。 */
function partitionIssueText(item: DataSetHealthSummary): string {
  const issue = readIssue(item)
  if (issue.last_error) return issue.last_error
  const numbers: string[] = []
  if (item.missing_count > 0) numbers.push(`缺口 ${item.missing_count.toLocaleString()}`)
  if (item.invalid_count > 0) numbers.push(`异常 ${item.invalid_count.toLocaleString()}`)
  if (issue.warning_count) numbers.push(`告警 ${issue.warning_count}`)
  const reasons = (issue.reasons ?? []).map((reason) => REASON_LABELS[reason] ?? reason)
  const text = [...numbers, ...reasons].join(' · ')
  return text || (item.last_checked_at ? '正常' : '尚未检查')
}

/** 缺口样例（最多展示 5 个）。 */
function missingSample(item: DataSetHealthSummary): string[] {
  return (readIssue(item).missing_dates_sample ?? []).slice(0, 5)
}

/**
 * 最近一次同步尝试未成功时的提示。
 *
 * `last_synced_at` 在每次同步尝试（含失败）时写入，`last_success_at` 只在成功时
 * 写入；前者更新则说明最近一次同步没有成功，这类失败在别处不可见。
 *
 * @param item - 数据集摘要
 * @returns 提示文案，无异常时返回空串
 */
function syncHint(item: DataSetHealthSummary): string {
  const { last_synced_at: syncedAt, last_success_at: successAt } = item
  if (!syncedAt) return ''
  if (successAt && syncedAt <= successAt) return ''
  return `最近一次同步尝试（${formatCnTime(syncedAt)}）未成功，可重新执行「补最新」或查看运行记录。`
}

/** 缺口样例是否被截断。 */
function missingSampleTruncated(item: DataSetHealthSummary): boolean {
  return readIssue(item).missing_dates_truncated === true
}

/** 数据集是否声明支持某项维护操作。 */
function supports(item: DataSetHealthSummary, operation: DataManagementOperation): boolean {
  return item.supported_operations.includes(operation)
}

/** 数据集对应的数据浏览页面。 */
function dataPageFor(datasetKey: string): { label: string; path: string } | null {
  return DATA_PAGE_BY_DATASET[datasetKey] ?? null
}

/** 操作按钮文案：执行中的操作显示进度态。 */
function operationLabel(operation: DataManagementOperation): string {
  const running = busy.value && activeOperation.value === operation
  if (running) return operation === 'check' ? '检查中…' : '同步中…'
  return operation === 'check' ? '检查全部质量' : '同步全部数据'
}

/** 切换状态筛选，重复点击同一状态视为取消筛选。 */
function toggleStatusFilter(status: StatusFilter): void {
  statusFilter.value = statusFilter.value === status && status !== 'all' ? 'all' : status
}

/** 选择数据集并加载其分区明细。 */
async function selectDataSet(datasetKey: string): Promise<void> {
  selectedKey.value = datasetKey
  partitionKeyword.value = ''
  appliedKeyword.value = ''
  partitionStatus.value = ''
  exactPartitionKey.value = ''
  detailOffset.value = 0
  await loadDetail()
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

/** 读取当前数据集的分区明细（服务端筛选 + 分页）。 */
async function loadDetail(): Promise<void> {
  const datasetKey = selectedKey.value
  if (!datasetKey) return
  detailLoading.value = true
  try {
    detail.value = await fetchDataSetDetail(datasetKey, detailOffset.value, PAGE_SIZE, {
      partitionKey: exactPartitionKey.value || undefined,
      problemOnly: partitionStatus.value === 'problem',
      healthStatus: partitionStatus.value === 'problem' ? undefined : partitionStatus.value || undefined,
      keyword: appliedKeyword.value || undefined,
    })
  } catch (cause) {
    detail.value = null
    error.value = cause instanceof Error ? cause.message : '数据集详情加载失败'
  } finally {
    detailLoading.value = false
  }
}

/** 应用分区筛选条件并回到第一页。 */
function applyPartitionFilter(): void {
  appliedKeyword.value = partitionKeyword.value.trim()
  detailOffset.value = 0
  void loadDetail()
}

/** 清空全部分区筛选条件。 */
function clearPartitionFilter(): void {
  partitionKeyword.value = ''
  appliedKeyword.value = ''
  partitionStatus.value = ''
  exactPartitionKey.value = ''
  detailOffset.value = 0
  void loadDetail()
}

/** 取消深链带入的单分区精确过滤。 */
function clearExactPartition(): void {
  exactPartitionKey.value = ''
  detailOffset.value = 0
  void loadDetail()
}

/** 切换分区明细分页。 */
function changePage(direction: number): void {
  const current = detail.value
  if (!current) return
  detailOffset.value = Math.max(0, current.offset + direction * current.limit)
  void loadDetail()
}

/** 打开全量重拉确认弹窗。 */
function openRebuild(datasetKey: string, partitionKey?: string): void {
  rebuildTarget.value = { datasetKey, partitionKey }
  rebuildTokenInput.value = ''
  rebuildError.value = ''
}

/** 关闭全量重拉确认弹窗。 */
function closeRebuild(): void {
  rebuildTarget.value = null
  rebuildTokenInput.value = ''
  rebuildError.value = ''
}

/** 校验确认令牌后提交全量重拉。 */
function confirmRebuild(): void {
  const target = rebuildTarget.value
  if (!target) return
  if (rebuildTokenInput.value !== rebuildToken.value) {
    rebuildError.value = `确认令牌不匹配，请完整输入 ${rebuildToken.value}`
    return
  }
  const partitionKey = target.partitionKey
  const datasetKey = target.datasetKey
  closeRebuild()
  void submitOperation('rebuild', datasetKey, partitionKey)
}

/** 展示结果横幅：成功提示自动消失，失败提示保留到下一次操作。 */
function showBanner(text: string, ok: boolean): void {
  if (bannerTimer !== null) {
    window.clearTimeout(bannerTimer)
    bannerTimer = null
  }
  banner.value = { text, ok }
  if (ok) {
    bannerTimer = window.setTimeout(() => {
      banner.value = null
      bannerTimer = null
    }, BANNER_TIMEOUT_MS)
  }
}

/** 从运行记录中读取数据管理任务指标。 */
function readRunMetrics(raw: Record<string, unknown> | null | undefined): DataManageRunMetrics {
  if (!raw) return {}
  const result: DataManageRunMetrics = {}
  if (typeof raw.success_count === 'number') result.success_count = raw.success_count
  if (typeof raw.partial_count === 'number') result.partial_count = raw.partial_count
  if (typeof raw.failed_count === 'number') result.failed_count = raw.failed_count
  if (typeof raw.reason === 'string') result.reason = raw.reason
  if (Array.isArray(raw.items)) {
    result.items = raw.items.filter(
      (value): value is DataManageRunItem => typeof value === 'object' && value !== null,
    )
  }
  return result
}

/** 数据集键转显示名。 */
function datasetName(datasetKey: string | undefined): string {
  if (!datasetKey) return '未知数据集'
  return datasets.value.find((item) => item.dataset_key === datasetKey)?.display_name ?? datasetKey
}

/** 汇总一次运行的结果文案。 */
function summarizeRun(run: ResearchRunDetail): { text: string; ok: boolean } {
  const metrics = readRunMetrics(run.metrics)
  const operation = activeOperation.value ? OPERATION_LABELS[activeOperation.value] : '数据维护'
  if (run.status === 'skipped') {
    return { text: `${operation}任务已跳过：${metrics.reason ?? '已有同类任务在执行'}`, ok: false }
  }
  if (run.status === 'failed') {
    return { text: `${operation}任务失败：${run.error_message ?? '所有数据集维护操作均失败'}`, ok: false }
  }
  const items = metrics.items ?? []
  const parts = [`成功 ${metrics.success_count ?? 0}`]
  if (metrics.partial_count) parts.push(`部分成功 ${metrics.partial_count}`)
  if (metrics.failed_count) parts.push(`失败 ${metrics.failed_count}`)
  const inserted = items.reduce((sum, item) => sum + (item.records_inserted ?? 0), 0)
  const updated = items.reduce((sum, item) => sum + (item.records_updated ?? 0), 0)
  const found = items.reduce((sum, item) => sum + (item.gaps_found ?? 0), 0)
  const repaired = items.reduce((sum, item) => sum + (item.gaps_repaired ?? 0), 0)
  if (inserted) parts.push(`新增记录 ${inserted.toLocaleString()}`)
  if (updated) parts.push(`更新记录 ${updated.toLocaleString()}`)
  if (found) parts.push(`发现缺口 ${found.toLocaleString()}`)
  if (repaired) parts.push(`修复缺口 ${repaired.toLocaleString()}`)
  const failures = items
    .filter((item) => item.status === 'failed')
    .slice(0, 3)
    .map((item) => {
      const reason = item.errors?.[0] ?? item.error
      return `${datasetName(item.dataset_key)}${reason ? `（${reason}）` : ''}`
    })
  if (failures.length) parts.push(`失败数据集：${failures.join('、')}`)
  return {
    text: `${operation}任务完成：${parts.join(' · ')}`,
    ok: (metrics.failed_count ?? 0) === 0,
  }
}

/** 提交统一数据维护操作并跟踪运行进度。 */
async function submitOperation(
  operation: DataManagementOperation,
  datasetKey?: string,
  partitionKey?: string,
): Promise<void> {
  submitting.value = true
  activeOperation.value = operation
  error.value = null
  try {
    const accepted = await triggerDataManagementOperation({
      operation,
      dataset_key: datasetKey,
      partition_key: partitionKey,
      // 数据集/分区级"补最新"要显式请求上游，全局同步由各数据集节流规则决定
      force: operation === 'sync_latest' && datasetKey !== undefined,
      confirmation_token: operation === 'rebuild' ? rebuildTokenFor(datasetKey, partitionKey) : undefined,
    })
    activeRunId.value = accepted.run_id
    if (accepted.status === 'already_running') {
      showBanner('已有同范围的数据维护任务在执行，正在跟踪该任务的进度…', true)
    }
    await start()
  } catch (cause) {
    showBanner(cause instanceof Error ? cause.message : '数据维护任务提交失败', false)
  } finally {
    submitting.value = false
    activeOperation.value = null
  }
}

/** 构造全量重拉确认令牌（与后端校验规则一致）。 */
function rebuildTokenFor(datasetKey?: string, partitionKey?: string): string {
  return `REBUILD:${datasetKey ?? ''}:${partitionKey ?? 'ALL'}`
}

/** 轮询当前任务，终态时刷新总览与详情并给出结果反馈。 */
const { start, polling } = usePolling<ResearchRunDetail>({
  fetcher: async () => fetchRunDetail(activeRunId.value ?? ''),
  isDone: (run) => TERMINAL_STATUSES.includes(run.status),
  intervalMs: 2000,
  onData: (run) => {
    if (!TERMINAL_STATUSES.includes(run.status)) return
    const summary = summarizeRun(run)
    showBanner(summary.text, summary.ok)
    void loadOverview()
    if (selectedKey.value) void loadDetail()
  },
})

/** 默认选中第一个需要关注的数据集，其次第一个数据集。 */
function defaultDatasetKey(): string | null {
  const priority: Record<HealthStatus, number> = {
    error: 0,
    warning: 1,
    unknown: 2,
    healthy: 3,
    unsupported: 4,
  }
  const sorted = datasets.value
    .slice()
    .sort((left, right) => priority[left.health_status] - priority[right.health_status])
  return sorted.length ? sorted[0].dataset_key : null
}

onMounted(async () => {
  await loadOverview()
  const datasetKey = typeof route.query.dataset === 'string' ? route.query.dataset : ''
  if (datasetKey && datasets.value.some((item) => item.dataset_key === datasetKey)) {
    selectedKey.value = datasetKey
    exactPartitionKey.value = typeof route.query.partition === 'string' ? route.query.partition : ''
    await loadDetail()
    return
  }
  const fallback = defaultDatasetKey()
  if (fallback) await selectDataSet(fallback)
})

onUnmounted(() => {
  if (bannerTimer !== null) {
    window.clearTimeout(bannerTimer)
    bannerTimer = null
  }
})
</script>

<style scoped>
/* ── 页面骨架（与系统其它页面一致） ── */
.page {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-actions {
  display: flex;
  gap: 8px;
}
.page-title {
  font-size: 22px;
  font-weight: 700;
}
.count-badge {
  font-size: 11px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
}
.header-meta {
  font-size: 12px;
  color: var(--text-muted);
}

/* ── 提示与横幅 ── */
.notice {
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.25);
  color: #93c5fd;
  font-size: 12px;
  line-height: 1.5;
}
.notice-hint {
  display: flex;
  align-items: center;
  gap: 12px;
}
.notice-hint > span {
  flex: 1;
}
.error-tip {
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: rgba(239, 68, 68, 0.12);
  color: var(--danger);
  font-size: 13px;
}
.refresh-banner {
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  line-height: 1.5;
}
.banner-ok {
  background: rgba(34, 197, 94, 0.1);
  border: 1px solid rgba(34, 197, 94, 0.3);
  color: var(--success);
}
.banner-err {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: var(--danger);
}
.polling-banner {
  padding: 10px 16px;
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.3);
  border-radius: var(--radius-sm);
  color: #60a5fa;
  font-size: 13px;
}

/* ── 状态汇总 ── */
.summary-card {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 10px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.summary-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 76px;
  padding: 6px 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  text-align: left;
  transition: background 0.15s, border-color 0.15s;
}
.summary-item:hover {
  background: var(--surface-2);
}
.summary-item.active {
  border-color: var(--accent);
  background: rgba(59, 130, 246, 0.12);
}
.summary-item strong {
  font-size: 18px;
  font-family: ui-monospace, SFMono-Regular, monospace;
  line-height: 1.1;
}
.summary-item span {
  font-size: 11px;
  color: var(--text-muted);
}
.tone-all {
  color: var(--text);
}
.tone-healthy {
  color: var(--success);
}
.tone-warning {
  color: var(--warning);
}
.tone-error {
  color: var(--danger);
}
.tone-unknown,
.tone-unsupported {
  color: var(--text-muted);
}
.summary-side {
  display: flex;
  gap: 20px;
  margin-left: auto;
  padding-right: 6px;
}
.summary-side .k {
  display: block;
  font-size: 11px;
  color: var(--text-muted);
}
.summary-side .v {
  font-size: 12px;
}

/* ── 主从布局 ── */
.layout {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 16px;
  align-items: start;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.list-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  transition: border-color 0.15s;
}
.list-card:hover {
  border-color: color-mix(in srgb, var(--accent) 45%, var(--border));
}
.list-card.active {
  border-color: var(--accent);
}
.list-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.list-top .name {
  font-size: 13px;
  font-weight: 600;
}
.list-sub {
  font-size: 12px;
  color: var(--text-muted);
}
.list-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
  color: var(--text-muted);
}
.list-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 32px 16px;
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  color: var(--text-muted);
  font-size: 13px;
}
.detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

/* ── 卡片 ── */
.card {
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.empty-card {
  color: var(--text-muted);
  font-size: 13px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.card-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.card-title {
  font-weight: 600;
}
.type-badge {
  font-size: 11px;
  background: var(--surface-2);
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
}
.actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
}
.kv-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 10px;
}
.kv-grid .wide {
  grid-column: 1 / -1;
}
.k {
  display: block;
  font-size: 12px;
  color: var(--text-muted);
}
.v {
  font-size: 14px;
}
.issue-line {
  margin-top: 12px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  background: rgba(245, 158, 11, 0.08);
  color: #fbbf24;
  font-size: 12px;
  line-height: 1.5;
}
.issue-line.ok {
  background: rgba(34, 197, 94, 0.08);
  color: #4ade80;
}
.hint {
  margin-top: 10px;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.5;
}

/* ── 分区筛选 ── */
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.filter-input {
  width: 240px;
}
.form-input,
.form-select {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 7px 10px;
  font-size: 13px;
  outline: none;
}
.form-select {
  background: var(--bg);
}
.exact-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border: 1px solid rgba(59, 130, 246, 0.4);
  border-radius: 20px;
  font-size: 11px;
  color: #60a5fa;
}
.exact-tag-close {
  background: transparent;
  border: none;
  color: inherit;
  font-size: 13px;
  line-height: 1;
}

/* ── 表格 ── */
.table-scroll {
  max-height: 560px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.table th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 8px 10px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.table td {
  padding: 8px 10px;
  border-bottom: 1px solid rgba(51, 65, 85, 0.5);
  vertical-align: top;
}
.table tr:last-child td {
  border-bottom: none;
}
.code-mono {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-weight: 600;
  color: var(--accent);
}
.mono {
  font-family: ui-monospace, SFMono-Regular, monospace;
  font-size: 12px;
  color: var(--text-muted);
}
.num-cell {
  font-family: ui-monospace, SFMono-Regular, monospace;
  text-align: right;
  white-space: nowrap;
}
.text-warn {
  color: #fbbf24;
}
.issue-cell {
  color: var(--text-muted);
  min-width: 180px;
}
.action-cell {
  white-space: nowrap;
}
.block {
  display: block;
}
.muted-text {
  color: var(--text-muted);
  font-size: 11px;
}

/* ── 状态徽标 ── */
.status-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}
.status-healthy {
  background: rgba(34, 197, 94, 0.15);
  color: var(--success);
}
.status-warning {
  background: rgba(245, 158, 11, 0.15);
  color: var(--warning);
}
.status-error {
  background: rgba(239, 68, 68, 0.15);
  color: var(--danger);
}
.status-unknown,
.status-unsupported {
  background: var(--surface-2);
  color: var(--text-muted);
}

/* ── 按钮 ── */
.btn-primary,
.btn-secondary,
.btn-danger {
  padding: 7px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  transition: background 0.15s, border-color 0.15s, opacity 0.15s;
}
.btn-primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}
.btn-primary:hover:not(:disabled) {
  opacity: 0.9;
}
.btn-secondary:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--accent);
}
.btn-danger {
  color: var(--danger);
  border-color: rgba(239, 68, 68, 0.4);
  background: var(--surface);
}
.btn-danger:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.12);
}
.btn-primary:disabled,
.btn-secondary:disabled,
.btn-danger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-sm {
  padding: 4px 10px;
  font-size: 12px;
}
.btn-mini {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--radius-sm);
  padding: 3px 8px;
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn-mini + .btn-mini {
  margin-left: 6px;
}
.btn-mini:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.btn-mini:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-mini.btn-danger {
  color: var(--danger);
  border-color: rgba(239, 68, 68, 0.4);
}
.btn-mini.btn-danger:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.12);
  color: var(--danger);
}

/* ── 空态 / 加载 / 分页 ── */
.loading,
.empty {
  padding: 40px;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding-top: 14px;
}
.page-btn {
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  transition: border-color 0.15s;
}
.page-btn:hover:not(:disabled) {
  border-color: var(--accent);
}
.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.page-info {
  font-size: 13px;
  color: var(--text-muted);
}

/* ── 重拉确认弹窗 ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.6);
}
.modal {
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 520px;
  max-width: 92vw;
  padding: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.modal-title {
  font-size: 16px;
  font-weight: 700;
}
.modal-text {
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-muted);
}
.modal-text strong {
  color: var(--text);
}
.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.form-label {
  font-size: 12px;
  color: var(--text-muted);
  font-family: ui-monospace, SFMono-Regular, monospace;
  word-break: break-all;
}
.form-error {
  color: var(--danger);
  font-size: 12px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
}

/* ── 响应式 ── */
@media (max-width: 1080px) {
  .layout {
    grid-template-columns: 1fr;
  }
  .summary-card {
    flex-wrap: wrap;
  }
  .summary-side {
    width: 100%;
    margin-left: 0;
    padding: 6px 10px 0;
    border-top: 1px solid var(--border);
  }
}
@media (max-width: 760px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .header-actions {
    width: 100%;
  }
  .header-actions .btn-primary,
  .header-actions .btn-secondary {
    flex: 1;
  }
  .summary-item {
    min-width: 64px;
    padding: 6px 8px;
  }
  .filter-input,
  .filter-row .form-select {
    width: 100%;
  }
  .modal {
    width: 100%;
  }
}
</style>
