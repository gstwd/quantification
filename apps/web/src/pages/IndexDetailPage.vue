<template>
  <div class="page">
    <div v-if="loading" class="loading">加载中...</div>
    <template v-else>
      <div class="page-header">
        <div class="header-main">
          <RouterLink to="/indexes" class="back-link">← 返回指数列表</RouterLink>
          <h1 class="page-title">{{ indexName }}</h1>
          <span class="code-badge">{{ indexCode }}</span>
        </div>
        <div class="stat-row">
          <div v-if="latestBar" class="stat-item">
            <span class="stat-label">最新收盘</span>
            <span class="stat-value">{{ latestBar.close_price?.toFixed(2) ?? '—' }}</span>
            <span class="stat-sub" :class="changePctClass">
              {{ formatPct(latestBar.change_pct) }}
            </span>
          </div>
          <div v-if="latestValuation" class="stat-item">
            <span class="stat-label">PE(TTM) <HelpTip :text="mktHelp('pe')" /></span>
            <span class="stat-value">{{ latestValuation.pe?.toFixed(1) ?? '—' }}</span>
            <span class="stat-sub">分位 {{ latestValuation.pe_percentile?.toFixed(0) ?? '—' }}%</span>
          </div>
          <div v-if="latestValuation" class="stat-item">
            <span class="stat-label">PB <HelpTip :text="mktHelp('pb')" /></span>
            <span class="stat-value">{{ latestValuation.pb?.toFixed(2) ?? '—' }}</span>
            <span class="stat-sub">分位 {{ latestValuation.pb_percentile?.toFixed(0) ?? '—' }}%</span>
          </div>
          <div v-if="latestValuation" class="stat-item">
            <span class="stat-label">股息率 <HelpTip :text="mktHelp('dividend_yield')" /></span>
            <span class="stat-value">{{ latestValuation.dividend_yield?.toFixed(2) ?? '—' }}%</span>
          </div>
        </div>
        <div v-if="freshnessMsg" class="freshness" :class="{ stale: isStale }">
          {{ freshnessMsg }}
          <span v-if="isStale" class="stale-badge">数据滞后</span>
        </div>
        <div v-if="noDataMsg" class="no-valuation">{{ noDataMsg }}</div>
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
            >{{ preset.label }}</button>
            <span class="range-sep">|</span>
            <input type="date" class="date-input" v-model="barStartDate" :min="dateRange.min_date ?? undefined" :max="dateRange.max_date ?? undefined" />
            <span class="range-tilde">~</span>
            <input type="date" class="date-input" v-model="barEndDate" :min="dateRange.min_date ?? undefined" :max="dateRange.max_date ?? undefined" />
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
          <br />
          <button class="retry-btn" @click="loadBars()">重试</button>
        </div>
        <div v-else-if="bars.length === 0" class="chart-placeholder">暂无行情数据</div>
        <div v-else ref="chartEl" class="chart-container"></div>
      </div>

      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">PE / PB 历史分位</span>
          <div class="range-controls">
            <button
              v-for="preset in rangePresets"
              :key="'v-' + preset.label"
              class="range-btn"
              :class="{ active: valuationActivePreset === preset.label }"
              @click="applyValuationPreset(preset)"
            >{{ preset.label }}</button>
          </div>
        </div>
        <div v-if="valuationError && valuation.length > 0" class="inline-error">
          本次加载失败，仍显示上一次数据
          <button class="retry-btn" @click="loadValuation(currentValuationRange)">重试</button>
        </div>
        <div v-if="valuationLoading && valuation.length === 0" class="chart-placeholder">加载中...</div>
        <div v-else-if="valuationError && valuation.length === 0" class="chart-placeholder">
          估值数据加载失败
          <br />
          <button class="retry-btn" @click="loadValuation(currentValuationRange)">重试</button>
        </div>
        <div v-else-if="valuation.length === 0" class="chart-placeholder">暂无估值数据（仅沪深300/上证50/中证500 支持）</div>
        <div v-else ref="valuationChartEl" class="chart-container"></div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import {
  fetchIndexDailyBars,
  fetchIndexDateRange,
  fetchIndexSummaries,
  fetchIndexValuation,
  fetchPreviousTradingDay,
} from '../api/market_data'
import type { DailyBar, DateRange, IndexSummary, IndexValuation } from '../types/api'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'

/** 获取指标描述的快捷方法 */
function mktHelp(key: string): string {
  return getIndicator('market_data', key)?.description ?? ''
}

const props = defineProps<{ indexCode: string }>()

const route = useRoute()
const router = useRouter()

const bars = ref<DailyBar[]>([])
const valuation = ref<IndexValuation[]>([])
const barsLoading = ref(false)
const valuationLoading = ref(false)
const barsError = ref(false)
const valuationError = ref(false)
const loading = ref(true)
const indexName = ref(props.indexCode)

const chartEl = ref<HTMLElement | null>(null)
const valuationChartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let chartInstance: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let valuationChartInstance: any = null
let chartResizeObserver: ResizeObserver | null = null
let valuationResizeObserver: ResizeObserver | null = null

/** 页头最新快照（独立于图表区间，一次性拉取后保持不变） */
const snapshot = ref<IndexSummary | null>(null)
/** 前一交易日（来自交易日历，用于数据新鲜度判断） */
const prevTradingDay = ref<string | null>(null)

/** 最新收盘（来自独立快照，不随图表区间变化） */
const latestBar = computed(() =>
  snapshot.value
    ? { close_price: snapshot.value.close_price, change_pct: snapshot.value.change_pct }
    : null,
)

/** 最新估值（来自独立快照，不随图表区间变化） */
const latestValuation = computed(() =>
  snapshot.value
    ? {
        pe: snapshot.value.pe,
        pe_percentile: snapshot.value.pe_percentile,
        pb: snapshot.value.pb,
        pb_percentile: snapshot.value.pb_percentile,
        dividend_yield: snapshot.value.dividend_yield,
      }
    : null,
)

const changePctClass = computed(() => {
  const pct = latestBar.value?.change_pct
  if (pct === null || pct === undefined) return ''
  return pct >= 0 ? 'text-rise' : 'text-fall'
})

/** 页头数据缺失提示：完全无数据 / 有行情无估值 / 正常时不显示 */
const noDataMsg = computed(() => {
  if (!latestBar.value && !latestValuation.value) return '暂无数据'
  if (!latestValuation.value) return '暂无估值数据'
  return ''
})

/** 数据新鲜度提示文案（行情/估值各自的数据截至日期） */
const freshnessMsg = computed(() => {
  const s = snapshot.value
  if (!s) return ''
  const parts: string[] = []
  if (s.bar_date) parts.push(`行情截至 ${s.bar_date}`)
  if (s.valuation_date) parts.push(`估值截至 ${s.valuation_date}`)
  return parts.join(' · ')
})

/** 是否数据滞后：任一数据日期早于前一交易日 */
const isStale = computed(() => {
  if (!prevTradingDay.value) return false
  const dates = [snapshot.value?.bar_date, snapshot.value?.valuation_date].filter((d): d is string => !!d)
  return dates.length > 0 && dates.some(d => d < prevTradingDay.value!)
})

/** 日期范围元数据 */
const dateRange = ref<DateRange>({ min_date: null, max_date: null })

/** K 线日期选择器 */
const barStartDate = ref('')
const barEndDate = ref('')
const activePreset = ref('近60日')
/** 自定义区间校验错误 */
const rangeError = ref('')

/** 估值图快捷切换 */
const valuationActivePreset = ref('近60日')

/** 快捷按钮配置 */
const rangePresets = [
  { label: '近30日', days: 30 },
  { label: '近60日', days: 60 },
  { label: '近半年', days: 180 },
  { label: '近1年', days: 365 },
  { label: '全部', days: 0 },
]

/** 当前估值区间参数（由激活的快捷预设推导，供重试复用） */
const currentValuationRange = computed(() => {
  const preset = rangePresets.find(p => p.label === valuationActivePreset.value)
  return calcRange(preset?.days ?? 60)
})

/** 格式化日期为 YYYY-MM-DD（使用本地时区，避免 toISOString 的 UTC 偏移） */
function formatDate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 格式化涨跌幅文本 */
function formatPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return '—'
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}

/** 计算日期范围 */
function calcRange(days: number): { startDate?: string; endDate?: string; limit?: number } {
  if (days === 0) {
    if (dateRange.value.min_date && dateRange.value.max_date) {
      return { startDate: dateRange.value.min_date, endDate: dateRange.value.max_date }
    }
    return { limit: 2000 }
  }
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - days)
  return { startDate: formatDate(start), endDate: formatDate(end) }
}

/** 将当前选择同步到 URL query，便于分享与刷新恢复 */
function syncRangeQuery() {
  const query: Record<string, string> = {}
  if (activePreset.value) {
    query.preset = activePreset.value
  } else {
    if (barStartDate.value) query.start = barStartDate.value
    if (barEndDate.value) query.end = barEndDate.value
  }
  if (valuationActivePreset.value) query.vp = valuationActivePreset.value
  router.replace({ query })
}

/** 从 URL query 恢复用户上次的区间选择 */
function restoreRangeFromQuery() {
  const q = route.query
  const presetLabel = typeof q.preset === 'string' ? q.preset : ''
  const start = typeof q.start === 'string' ? q.start : ''
  const end = typeof q.end === 'string' ? q.end : ''
  const vp = typeof q.vp === 'string' ? q.vp : ''

  const preset = rangePresets.find(p => p.label === presetLabel)
  if (preset) {
    const range = calcRange(preset.days)
    barStartDate.value = range.startDate ?? ''
    barEndDate.value = range.endDate ?? ''
    activePreset.value = preset.label
  } else if (start && end && start <= end) {
    barStartDate.value = start
    barEndDate.value = end
    activePreset.value = ''
  } else {
    const range = calcRange(60)
    barStartDate.value = range.startDate ?? ''
    barEndDate.value = range.endDate ?? ''
    activePreset.value = '近60日'
  }

  const vPreset = rangePresets.find(p => p.label === vp)
  valuationActivePreset.value = vPreset ? vPreset.label : '近60日'
}

/** 应用 K 线快捷日期范围 */
async function applyPreset(preset: { label: string; days: number }) {
  activePreset.value = preset.label
  rangeError.value = ''
  const range = calcRange(preset.days)
  barStartDate.value = range.startDate ?? ''
  barEndDate.value = range.endDate ?? ''
  syncRangeQuery()
  await loadBars()
}

/** 应用自定义日期范围 */
async function applyCustomRange() {
  rangeError.value = ''
  if (barStartDate.value && barEndDate.value && barStartDate.value > barEndDate.value) {
    rangeError.value = '起始日期不能晚于结束日期'
    return
  }
  activePreset.value = ''
  syncRangeQuery()
  await loadBars()
}

/** 应用估值快捷日期范围 */
async function applyValuationPreset(preset: { label: string; days: number }) {
  valuationActivePreset.value = preset.label
  syncRangeQuery()
  await loadValuation(calcRange(preset.days))
}

/** 加载 K 线数据（失败时保留旧数据，仅标记错误） */
async function loadBars() {
  barsLoading.value = true
  barsError.value = false
  try {
    if (barStartDate.value && barEndDate.value) {
      bars.value = await fetchIndexDailyBars(props.indexCode, {
        startDate: barStartDate.value,
        endDate: barEndDate.value,
      })
    } else {
      bars.value = await fetchIndexDailyBars(props.indexCode, { limit: 60 })
    }
  } catch {
    barsError.value = true
  } finally {
    barsLoading.value = false
  }
}

/** 加载估值数据（失败时保留旧数据，仅标记错误） */
async function loadValuation(params: { limit?: number; startDate?: string; endDate?: string } = { limit: 60 }) {
  valuationLoading.value = true
  valuationError.value = false
  try {
    valuation.value = await fetchIndexValuation(props.indexCode, params)
  } catch {
    valuationError.value = true
  } finally {
    valuationLoading.value = false
  }
}

/** 渲染 K 线图（首次初始化并挂载尺寸监听，后续增量更新数据） */
async function renderKlineChart() {
  if (!chartEl.value || bars.value.length === 0) return
  const echarts = await import('echarts')
  if (!chartInstance) {
    chartInstance = echarts.init(chartEl.value, null, { renderer: 'canvas' })
    chartResizeObserver?.disconnect()
    chartResizeObserver = new ResizeObserver(() => chartInstance.resize())
    chartResizeObserver.observe(chartEl.value)
  }
  const dates = bars.value.map(b => b.trade_date)
  const candleData = bars.value.map(b => [b.open_price, b.close_price, b.low_price, b.high_price])
  const volumes = bars.value.map(b => ({
    value: b.volume ?? 0,
    itemStyle: {
      // 成交量按当日涨跌着色，与 K 线颜色保持一致
      color: (b.close_price ?? 0) >= (b.open_price ?? 0) ? 'rgba(34,197,94,0.45)' : 'rgba(239,68,68,0.45)',
    },
  }))
  chartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' }, backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
    legend: { data: ['K线', '成交量'], textStyle: { color: '#94a3b8' }, top: 4 },
    grid: [
      { left: 60, right: 20, top: 40, bottom: 100 },
      { left: 60, right: 20, top: '72%', bottom: 60 },
    ],
    xAxis: [
      { type: 'category', data: dates, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 }, gridIndex: 0 },
      { type: 'category', data: dates, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { show: false }, gridIndex: 1 },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#334155', type: 'dashed' } }, axisLabel: { color: '#94a3b8', fontSize: 11 }, gridIndex: 0 },
      { scale: true, splitNumber: 3, splitLine: { show: false }, axisLabel: { show: false }, gridIndex: 1 },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], bottom: 10, height: 30, borderColor: '#334155', fillerColor: 'rgba(59,130,246,0.1)', handleStyle: { color: '#3b82f6' }, textStyle: { color: '#94a3b8' } },
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: candleData,
        itemStyle: { color: '#22c55e', color0: '#ef4444', borderColor: '#22c55e', borderColor0: '#ef4444' },
      },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volumes,
      },
    ],
  })
}

/** 渲染 PE/PB 分位图（首次初始化并挂载尺寸监听，后续增量更新数据） */
async function renderValuationChart() {
  if (!valuationChartEl.value || valuation.value.length === 0) return
  const echarts = await import('echarts')
  if (!valuationChartInstance) {
    valuationChartInstance = echarts.init(valuationChartEl.value, null, { renderer: 'canvas' })
    valuationResizeObserver?.disconnect()
    valuationResizeObserver = new ResizeObserver(() => valuationChartInstance.resize())
    valuationResizeObserver.observe(valuationChartEl.value)
  }
  const dates = valuation.value.map(v => v.trade_date)
  valuationChartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#f1f5f9', fontSize: 12 },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      formatter: (params: any) => {
        const list = Array.isArray(params) ? params : [params]
        let html = `<div style="margin-bottom:4px">${list[0]?.axisValue ?? ''}</div>`
        for (const p of list) {
          const val = p.value !== null && p.value !== undefined ? Number(p.value).toFixed(1) + '%' : '—'
          html += `<div>${p.color} ${p.seriesName}: ${val}</div>`
        }
        return html
      },
    },
    legend: { data: ['PE分位', 'PB分位'], textStyle: { color: '#94a3b8' }, top: 4 },
    grid: { left: 60, right: 60, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    yAxis: {
      min: 0,
      max: 100,
      splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
      axisLabel: { color: '#94a3b8', fontSize: 11, formatter: '{value}%' },
    },
    series: [
      {
        name: 'PE分位',
        type: 'line',
        data: valuation.value.map(v => v.pe_percentile),
        smooth: true,
        lineStyle: { color: '#3b82f6', width: 2 },
        symbol: 'none',
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.15)' }, { offset: 1, color: 'rgba(59,130,246,0)' }] } },
      },
      {
        name: 'PB分位',
        type: 'line',
        data: valuation.value.map(v => v.pb_percentile),
        smooth: true,
        lineStyle: { color: '#f59e0b', width: 2 },
        symbol: 'none',
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(245,158,11,0.15)' }, { offset: 1, color: 'rgba(245,158,11,0)' }] } },
      },
    ],
  })
}

watch([bars, chartEl], () => {
  if (!barsLoading.value && bars.value.length > 0 && chartEl.value) renderKlineChart()
}, { flush: 'post' })

watch([valuation, valuationChartEl], () => {
  if (!valuationLoading.value && valuation.value.length > 0 && valuationChartEl.value) renderValuationChart()
}, { flush: 'post' })

onMounted(async () => {
  try {
    const [summaries, dr, prevDay] = await Promise.all([
      fetchIndexSummaries().catch(() => [] as IndexSummary[]),
      fetchIndexDateRange(props.indexCode).catch(() => ({ min_date: null, max_date: null })),
      fetchPreviousTradingDay().catch(() => null),
    ])
    const found = summaries.find(i => i.index_code === props.indexCode)
    if (found) indexName.value = found.index_name
    snapshot.value = found ?? null
    dateRange.value = dr
    prevTradingDay.value = prevDay
  } catch {
    // 元数据获取失败不影响主流程
  }

  restoreRangeFromQuery()

  await Promise.all([loadBars(), loadValuation(currentValuationRange.value)])
  loading.value = false
})

onUnmounted(() => {
  chartResizeObserver?.disconnect()
  valuationResizeObserver?.disconnect()
  chartInstance?.dispose()
  valuationChartInstance?.dispose()
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 24px; }
.loading { padding: 60px; text-align: center; color: var(--text-muted); }

.page-header {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.header-main { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.page-title { font-size: 20px; font-weight: 700; }

.code-badge {
  font-family: monospace;
  font-size: 13px;
  font-weight: 700;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 3px 10px;
  border-radius: 20px;
}

.stat-row { display: flex; gap: 32px; flex-wrap: wrap; }
.stat-item { display: flex; flex-direction: column; gap: 2px; }
.stat-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.stat-value { font-size: 22px; font-weight: 700; }
.stat-sub { font-size: 12px; color: var(--text-muted); }
.text-rise { color: #22c55e; }
.text-fall { color: #ef4444; }

.no-valuation { font-size: 13px; color: var(--text-muted); padding: 8px 0; }

.chart-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.card-title { font-size: 14px; font-weight: 600; }
.card-sub { font-size: 12px; }
.text-muted { color: var(--text-muted); }

.range-controls {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  flex-wrap: wrap;
}

.range-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.range-btn:hover { color: var(--text); border-color: var(--accent); }
.range-btn.active { background: rgba(59, 130, 246, 0.15); color: var(--accent); border-color: var(--accent); }

.range-sep { color: var(--border); font-size: 14px; margin: 0 2px; }
.range-tilde { color: var(--text-muted); font-size: 12px; }

.date-input {
  background: var(--surface-2, rgba(255,255,255,0.05));
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 3px 8px;
  font-size: 12px;
  outline: none;
  width: 130px;
  color-scheme: dark;
}
.date-input:focus { border-color: var(--accent); }

.chart-placeholder { padding: 60px; text-align: center; color: var(--text-muted); }
.chart-container { width: 100%; height: 380px; padding: 8px; }
.chart-placeholder .retry-btn { margin-top: 10px; }

.back-link {
  font-size: 13px;
  color: var(--text-muted);
  text-decoration: none;
  white-space: nowrap;
}
.back-link:hover { color: var(--accent); }

.freshness { font-size: 12px; color: var(--text-muted); }
.freshness.stale { color: #fbbf24; }
.stale-badge {
  font-size: 10px;
  font-weight: 600;
  margin-left: 6px;
  padding: 1px 8px;
  border-radius: 20px;
  background: rgba(245, 158, 11, 0.15);
  color: #fbbf24;
}

.range-error { font-size: 12px; color: #f87171; margin-left: 4px; }

.inline-error {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 20px;
  font-size: 12px;
  color: #f87171;
  background: rgba(239, 68, 68, 0.08);
  border-bottom: 1px solid var(--border);
}

.retry-btn {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.35);
  color: #f87171;
  border-radius: var(--radius-sm);
  padding: 2px 12px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.retry-btn:hover { background: rgba(239, 68, 68, 0.22); }
</style>
