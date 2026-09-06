<template>
  <div class="page">
    <div v-if="loading" class="loading">加载中...</div>
    <template v-else>
      <div class="page-header">
        <div class="header-main">
          <RouterLink to="/industries" class="back-link">← 返回申万行业列表</RouterLink>
          <h1 class="page-title">{{ summary?.name_cn ?? industryCode }}</h1>
          <span class="code-badge">{{ industryCode }}</span>
          <RouterLink to="/tools/rrg-lab" class="research-link">RRG 研究 →</RouterLink>
        </div>
        <div class="stat-row">
          <div class="stat-item">
            <span class="stat-label">最新收盘</span>
            <span class="stat-value">{{ summary?.latest_close?.toFixed(2) ?? '—' }}</span>
            <span class="stat-sub" :class="pctClass(summary?.latest_change_pct)">
              {{ formatPct(summary?.latest_change_pct) }}
            </span>
          </div>
          <div class="stat-item">
            <span class="stat-label">成分股数</span>
            <span class="stat-value">{{ summary?.member_count ?? '—' }}</span>
            <span class="stat-sub">当前有效归属</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">数据截止</span>
            <span class="stat-value mono">{{ summary?.latest_trade_date ?? '—' }}</span>
            <span
              v-if="(summary?.missing_day_count ?? 0) > 0"
              class="stat-sub warn"
            >
              缺失 {{ summary?.missing_day_count }} 天
            </span>
          </div>
        </div>
        <div class="notice">
          缺失数 = “库内首根日线 → 最近交易日”区间内按交易日历统计的缺口；头部截断不计入，
          补全/重拉后仍可能因上游滞后残留尾部缺失。
        </div>
      </div>

      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">数据质量</span>
          <button class="btn-secondary" :disabled="qualityLoading" @click="loadQuality">
            {{ qualityLoading ? '加载中...' : '刷新' }}
          </button>
        </div>
        <div v-if="qualityLoading && !quality" class="chart-placeholder">加载中...</div>
        <div v-else-if="quality" class="quality-body">
          <div class="quality-grid">
            <div class="quality-block">
              <div class="quality-block-title">质量快照</div>
              <div class="quality-row">
                <span>覆盖范围</span>
                <span class="mono">{{ rangeText(quality.data_start_date, quality.data_end_date) }}</span>
              </div>
              <div class="quality-row">
                <span>日线条数</span>
                <span class="mono">{{ quality.bar_count ?? '—' }}</span>
              </div>
              <div class="quality-row">
                <span>缺失交易日</span>
                <span class="mono" :class="(quality.missing_day_count ?? 0) > 0 ? 'text-warn' : 'text-ok'">
                  {{ quality.missing_day_count ?? '—' }}
                </span>
              </div>
              <div class="quality-row">
                <span>检查时间</span>
                <span class="mono">{{ formatTime(quality.quality_checked_at) }}</span>
              </div>
            </div>
            <div class="quality-block">
              <div class="quality-block-title">字段完整性</div>
              <div class="quality-row">
                <span>记录数</span>
                <span class="mono">{{ quality.total }}</span>
              </div>
              <div class="quality-row">
                <span>OHLC 不完整</span>
                <span class="mono" :class="quality.incomplete_rows > 0 ? 'text-warn' : 'text-ok'">
                  {{ quality.incomplete_rows }} 行（{{ (quality.incomplete_ratio * 100).toFixed(1) }}%）
                </span>
              </div>
              <div class="quality-row">
                <span>开/高/低/收缺失</span>
                <span class="mono">
                  {{ quality.missing_open }}/{{ quality.missing_high }}/{{ quality.missing_low }}/{{ quality.missing_close }}
                </span>
              </div>
              <div class="quality-row">
                <span>change_pct 缺失率</span>
                <span class="mono" :class="quality.change_pct_null > 0 ? 'text-warn' : 'text-ok'">
                  {{ (quality.change_pct_null_rate * 100).toFixed(1) }}%
                </span>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="chart-placeholder">质量数据加载失败或无数据</div>
        <div class="quality-actions">
          <button class="btn-secondary" :disabled="taskActive" @click="triggerTask('industry_data_fill')">
            {{ taskRunning('industry_data_fill', '日线补全') }}
          </button>
          <button class="btn-secondary" :disabled="taskActive" @click="triggerTask('industry_quality_check')">
            {{ taskRunning('industry_quality_check', '质量检查') }}
          </button>
          <button class="btn-danger" :disabled="taskActive" @click="triggerTask('industry_data_rebuild')">
            {{ taskRunning('industry_data_rebuild', '全量重拉') }}
          </button>
          <span v-if="taskMessage || taskStatus" class="task-status" :class="taskStatusClass">
            {{ taskMessage || taskStatusText }}
          </span>
        </div>
      </div>

      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">K 线图</span>
          <span v-if="rangeError" class="range-error">{{ rangeError }}</span>
          <div class="range-controls">
            <button
              v-for="preset in rangePresets"
              :key="preset.label"
              class="range-btn"
              :class="{ active: activePreset === preset.label }"
              @click="applyPreset(preset)"
            >
              {{ preset.label }}
            </button>
            <span class="range-sep">|</span>
            <input v-model="barStartDate" type="date" class="date-input" />
            <span class="range-tilde">~</span>
            <input v-model="barEndDate" type="date" class="date-input" />
            <button class="range-btn" @click="applyCustomRange">查询</button>
          </div>
        </div>
        <div v-if="barsError && bars.length > 0" class="inline-error">
          本次加载失败，仍显示上一次数据
          <button class="retry-btn" @click="loadBars()">重试</button>
        </div>
        <div v-if="barsLoading && bars.length === 0" class="chart-placeholder">加载中...</div>
        <div v-else-if="barsError && bars.length === 0" class="chart-placeholder">
          行情数据加载失败
          <button class="retry-btn" @click="loadBars()">重试</button>
        </div>
        <div v-else-if="bars.length === 0" class="chart-placeholder">暂无行情数据</div>
        <div v-else ref="chartEl" class="chart-container"></div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * 申万一级行业详情页。
 *
 * 展示行业基本信息、数据质量快照（含 OHLC/change_pct 字段完整性）与
 * K 线图；支持单行业“质量检查 / 日线补全 / 全量重拉”，并链向 RRG 调试页。
 */

import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import type { ECharts } from 'echarts'

import {
  fetchIndustryDailyBars,
  fetchIndustryQuality,
  fetchIndustrySummaries,
  type DailyBarLike,
  type IndustryQualityDetail,
  type IndustrySummaryItem,
} from '../api/industry'
import {
  fetchRunDetail,
  triggerIndustryFill,
  triggerIndustryQualityCheck,
  triggerIndustryRebuild,
} from '../api/runs'
import type { ResearchRunDetail } from '../types/api'
import { usePolling } from '../composables/usePolling'

const props = defineProps<{ industryCode: string }>()

const summary = ref<IndustrySummaryItem | null>(null)
const quality = ref<IndustryQualityDetail | null>(null)
const bars = ref<DailyBarLike[]>([])
const loading = ref(true)
const qualityLoading = ref(false)
const barsLoading = ref(false)
const barsError = ref(false)
const taskRunId = ref('')
const taskStatus = ref('')
const taskMessage = ref('')
const chartEl = ref<HTMLElement | null>(null)

const barStartDate = ref('')
const barEndDate = ref('')
const activePreset = ref('近60日')
const rangeError = ref('')

let chartInstance: ECharts | null = null
let chartResizeObserver: ResizeObserver | null = null

const rangePresets = [
  { label: '近60日', days: 90 },
  { label: '近1年', days: 400 },
  { label: '近3年', days: 1100 },
  { label: '近2000根', days: 0 },
]

const { start: startTaskPolling } = usePolling<ResearchRunDetail | null>({
  fetcher: async () => {
    if (!taskRunId.value) return null
    try {
      return await fetchRunDetail(taskRunId.value)
    } catch {
      return null
    }
  },
  isDone: (run) => run !== null && ['success', 'failed', 'skipped'].includes(run.status),
  intervalMs: 2000,
  immediate: false,
  onData: (run) => {
    if (!run) return
    if (['success', 'failed', 'skipped'].includes(run.status)) {
      taskStatus.value = run.status
      if (run.status === 'failed') {
        taskMessage.value = run.error_message ?? '任务失败'
      } else {
        taskMessage.value = run.status === 'success' ? '任务完成' : '任务已跳过'
      }
      void refreshAfterTask()
    }
  },
})

const taskActive = computed(() => Boolean(taskRunId.value))
const taskStatusClass = computed(() => {
  if (taskStatus.value === 'failed') return 'status-failed'
  if (taskStatus.value === 'success') return 'status-ok'
  return ''
})
const taskStatusText = computed(() => {
  const labels: Record<string, string> = {
    pending: '排队中',
    running: '执行中',
  }
  return labels[taskStatus.value] ?? ''
})

function formatPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return '—'
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}

function pctClass(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return ''
  return pct >= 0 ? 'rise' : 'fall'
}

function formatTime(ts: string | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function rangeText(start: string | null | undefined, end: string | null | undefined): string {
  if (start && end) return `${start} ~ ${end}`
  return '—'
}

function taskRunning(runType: string, label: string): string {
  if (!taskRunId.value) return label
  const labels: Record<string, string> = {
    industry_quality_check: '检查中...',
    industry_data_fill: '补全中...',
    industry_data_rebuild: '重拉中...',
  }
  return labels[runType] ?? label
}

async function loadQuality(): Promise<void> {
  qualityLoading.value = true
  try {
    quality.value = await fetchIndustryQuality(props.industryCode)
  } catch {
    quality.value = null
  } finally {
    qualityLoading.value = false
  }
}

async function loadSummary(): Promise<void> {
  try {
    const rows = await fetchIndustrySummaries()
    summary.value = rows.find((row) => row.industry_code === props.industryCode) ?? null
  } catch {
    summary.value = null
  }
}

async function loadBars(): Promise<void> {
  barsLoading.value = true
  barsError.value = false
  try {
    if (barStartDate.value && barEndDate.value) {
      bars.value = await fetchIndustryDailyBars(props.industryCode, {
        startDate: barStartDate.value,
        endDate: barEndDate.value,
      })
    } else if (activePreset.value === '近2000根') {
      bars.value = await fetchIndustryDailyBars(props.industryCode, { limit: 2000 })
    } else {
      bars.value = await fetchIndustryDailyBars(props.industryCode, { limit: 250 })
    }
  } catch {
    barsError.value = true
  } finally {
    barsLoading.value = false
  }
}

function applyPreset(preset: { label: string; days: number }): void {
  activePreset.value = preset.label
  rangeError.value = ''
  barStartDate.value = ''
  barEndDate.value = ''
  if (preset.days > 0) {
    const end = new Date()
    const start = new Date()
    start.setDate(start.getDate() - preset.days)
    barStartDate.value = toDateStr(start)
    barEndDate.value = toDateStr(end)
  }
  void loadBars()
}

function applyCustomRange(): void {
  rangeError.value = ''
  if (barStartDate.value && barEndDate.value && barStartDate.value > barEndDate.value) {
    rangeError.value = '起始日期不能晚于结束日期'
    return
  }
  activePreset.value = ''
  void loadBars()
}

function toDateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

async function refreshAfterTask(): Promise<void> {
  await Promise.all([loadSummary(), loadQuality(), loadBars()])
  taskRunId.value = ''
  taskStatus.value = ''
}

async function triggerTask(runType: string): Promise<void> {
  if (taskActive.value) return
  if (runType === 'industry_data_rebuild') {
    if (!window.confirm('全量重拉将删除该行业库内全部日线并从数据源重新拉取，确定继续？')) {
      return
    }
  }
  try {
    const accepted =
      runType === 'industry_data_fill'
        ? await triggerIndustryFill(props.industryCode)
        : runType === 'industry_quality_check'
          ? await triggerIndustryQualityCheck(props.industryCode)
          : await triggerIndustryRebuild(props.industryCode)
    taskRunId.value = accepted.run_id
    taskStatus.value = 'pending'
    taskMessage.value = '任务已提交，排队中...'
    await startTaskPolling()
    await refreshAfterTask()
  } catch {
    taskStatus.value = 'failed'
    taskMessage.value = '触发失败，请重试'
  }
}

async function renderKlineChart(): Promise<void> {
  if (!chartEl.value || bars.value.length === 0) return
  const echarts = await import('echarts')
  if (!chartInstance) {
    chartInstance = echarts.init(chartEl.value, null, { renderer: 'canvas' })
    chartResizeObserver?.disconnect()
    chartResizeObserver = new ResizeObserver(() => chartInstance?.resize())
    chartResizeObserver.observe(chartEl.value)
  }
  const dates = bars.value.map((b) => b.trade_date)
  const candleData = bars.value.map((b) => [
    b.open_price ?? 0,
    b.close_price ?? 0,
    b.low_price ?? 0,
    b.high_price ?? 0,
  ])
  const volumes = bars.value.map((b) => ({
    value: b.volume ?? 0,
    itemStyle: {
      color:
        (b.close_price ?? 0) >= (b.open_price ?? 0)
          ? 'rgba(34,197,94,0.45)'
          : 'rgba(239,68,68,0.45)',
    },
  }))
  chartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#f1f5f9', fontSize: 12 },
    },
    legend: { data: ['K线', '成交量'], textStyle: { color: '#94a3b8' }, top: 4 },
    grid: [
      { left: 60, right: 20, top: 40, bottom: 100 },
      { left: 60, right: 20, top: '72%', bottom: 60 },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
        gridIndex: 0,
      },
      {
        type: 'category',
        data: dates,
        axisLine: { lineStyle: { color: '#334155' } },
        axisLabel: { show: false },
        gridIndex: 1,
      },
    ],
    yAxis: [
      {
        scale: true,
        splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
        gridIndex: 0,
      },
      {
        scale: true,
        splitNumber: 3,
        splitLine: { show: false },
        axisLabel: { show: false },
        gridIndex: 1,
      },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        bottom: 10,
        height: 30,
        borderColor: '#334155',
        fillerColor: 'rgba(59,130,246,0.1)',
        handleStyle: { color: '#3b82f6' },
        textStyle: { color: '#94a3b8' },
      },
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: candleData,
        itemStyle: {
          color: '#22c55e',
          color0: '#ef4444',
          borderColor: '#22c55e',
          borderColor0: '#ef4444',
        },
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes,
      },
    ],
  })
}

watch([bars, chartEl], () => {
  if (!barsLoading.value && bars.value.length > 0 && chartEl.value) {
    void renderKlineChart()
  }
}, { flush: 'post' })

onUnmounted(() => {
  chartResizeObserver?.disconnect()
  chartResizeObserver = null
  chartInstance?.dispose()
  chartInstance = null
})

onMounted(async () => {
  loading.value = true
  await Promise.all([loadSummary(), loadQuality()])
  const preset = rangePresets.find((p) => p.label === '近60日')
  if (preset) applyPreset(preset)
  loading.value = false
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.page-header {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.header-main {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.back-link {
  color: var(--text-muted);
  font-size: 13px;
  text-decoration: none;
}
.back-link:hover {
  color: var(--accent);
}
.page-title {
  font-size: 22px;
  font-weight: 700;
}
.code-badge {
  font-size: 12px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 3px 10px;
  border-radius: 20px;
  font-family: monospace;
}
.research-link {
  margin-left: auto;
  font-size: 13px;
  color: var(--accent);
  text-decoration: none;
}
.stat-row {
  display: flex;
  gap: 34px;
  flex-wrap: wrap;
}
.stat-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.stat-label {
  font-size: 12px;
  color: var(--text-muted);
}
.stat-value {
  font-size: 20px;
  font-weight: 700;
  font-family: monospace;
}
.stat-sub {
  font-size: 12px;
  color: var(--text-muted);
}
.stat-sub.rise {
  color: var(--success, #22c55e);
}
.stat-sub.fall {
  color: var(--danger, #ef4444);
}
.stat-sub.warn {
  color: #fbbf24;
}
.notice {
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.25);
  color: #93c5fd;
  font-size: 12px;
  line-height: 1.6;
}
.chart-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}
.card-title {
  font-weight: 600;
  font-size: 15px;
}
.quality-body {
  margin-bottom: 12px;
}
.quality-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 14px;
}
.quality-block {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}
.quality-block-title {
  font-size: 12px;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.quality-row {
  display: flex;
  justify-content: space-between;
  padding: 3px 0;
  font-size: 13px;
}
.quality-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
}
.task-status {
  font-size: 12px;
  color: var(--text-muted);
}
.task-status.status-ok {
  color: var(--success, #22c55e);
}
.task-status.status-failed {
  color: var(--danger, #ef4444);
}
.range-controls {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.range-btn {
  background: transparent;
  color: var(--text-muted);
  border: 1px solid transparent;
  padding: 4px 9px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
}
.range-btn.active {
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
}
.range-sep {
  color: var(--border);
  margin: 0 4px;
}
.date-input {
  background: var(--surface-2, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 4px;
  padding: 3px 6px;
  font-size: 12px;
}
.range-error {
  color: var(--danger, #ef4444);
  font-size: 12px;
}
.chart-placeholder {
  padding: 70px 0;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}
.chart-container {
  height: 520px;
  width: 100%;
}
.inline-error {
  color: var(--danger, #ef4444);
  font-size: 12px;
  margin-bottom: 8px;
}
.retry-btn {
  margin-left: 10px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--accent);
  border-radius: 4px;
  padding: 2px 10px;
  cursor: pointer;
}
.mono {
  font-family: monospace;
}
.text-warn {
  color: #fbbf24;
}
.text-ok {
  color: var(--success, #22c55e);
}
.btn-secondary {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn-secondary:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-danger {
  background: transparent;
  color: #f87171;
  border: 1px solid rgba(239, 68, 68, 0.4);
  border-radius: var(--radius-sm);
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn-danger:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.loading {
  padding: 80px;
  text-align: center;
  color: var(--text-muted);
}
</style>
