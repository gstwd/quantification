<template>
  <div class="page">
    <div v-if="loading" class="loading">加载中...</div>
    <template v-else>
      <div class="page-header">
        <div class="header-main">
          <h1 class="page-title">{{ indexName }}</h1>
          <span class="code-badge">{{ indexCode }}</span>
        </div>
        <div class="stat-row">
          <div v-if="latestBar" class="stat-item">
            <span class="stat-label">最新收盘</span>
            <span class="stat-value">{{ latestBar.close_price?.toFixed(2) ?? '—' }}</span>
            <span class="stat-sub" :class="changePctClass">
              {{ latestBar.change_pct !== null ? (latestBar.change_pct >= 0 ? '+' : '') + latestBar.change_pct.toFixed(2) + '%' : '—' }}
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
        <div v-if="!latestValuation && !latestBar" class="no-valuation">暂无数据</div>
        <div v-else class="no-valuation">暂无估值数据</div>
      </div>

      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">K 线图</span>
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
        <div v-if="barsLoading" class="chart-placeholder">加载中...</div>
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
        <div v-if="valuationLoading" class="chart-placeholder">加载中...</div>
        <div v-else-if="valuation.length === 0" class="chart-placeholder">暂无估值数据（仅沪深300/上证50/中证500 支持）</div>
        <div v-else ref="valuationChartEl" class="chart-container"></div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import {
  fetchBenchmarkIndexes,
  fetchIndexDailyBars,
  fetchIndexDateRange,
  fetchIndexValuation,
} from '../api/market_data'
import type { DailyBar, DateRange, IndexValuation } from '../types/api'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'

/** 获取指标描述的快捷方法 */
function mktHelp(key: string): string {
  return getIndicator('market_data', key)?.description ?? ''
}
function fHelp(key: string): string {
  return getIndicator('factors', key)?.description ?? ''
}

const props = defineProps<{ indexCode: string }>()

const bars = ref<DailyBar[]>([])
const valuation = ref<IndexValuation[]>([])
const barsLoading = ref(false)
const valuationLoading = ref(false)
const loading = ref(true)
const indexName = ref(props.indexCode)

const chartEl = ref<HTMLElement | null>(null)
const valuationChartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let chartInstance: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let valuationChartInstance: any = null

const latestValuation = computed(() =>
  valuation.value.length ? valuation.value[valuation.value.length - 1] : null,
)

const latestBar = computed(() =>
  bars.value.length ? bars.value[bars.value.length - 1] : null,
)

const changePctClass = computed(() => {
  const pct = latestBar.value?.change_pct
  if (pct === null || pct === undefined) return ''
  return pct >= 0 ? 'text-rise' : 'text-fall'
})

/** 日期范围元数据 */
const dateRange = ref<DateRange>({ min_date: null, max_date: null })

/** K 线日期选择器 */
const barStartDate = ref('')
const barEndDate = ref('')
const activePreset = ref('近60日')

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

/** 格式化日期为 YYYY-MM-DD */
function formatDate(d: Date): string {
  return d.toISOString().slice(0, 10)
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

/** 应用 K 线快捷日期范围 */
async function applyPreset(preset: { label: string; days: number }) {
  activePreset.value = preset.label
  const range = calcRange(preset.days)
  barStartDate.value = range.startDate ?? ''
  barEndDate.value = range.endDate ?? ''
  await loadBars()
}

/** 应用自定义日期范围 */
async function applyCustomRange() {
  activePreset.value = ''
  await loadBars()
}

/** 应用估值快捷日期范围 */
async function applyValuationPreset(preset: { label: string; days: number }) {
  valuationActivePreset.value = preset.label
  await loadValuation(calcRange(preset.days))
}

/** 加载 K 线数据 */
async function loadBars() {
  barsLoading.value = true
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
    bars.value = []
  } finally {
    barsLoading.value = false
  }
}

/** 加载估值数据 */
async function loadValuation(params: { limit?: number; startDate?: string; endDate?: string } = { limit: 60 }) {
  valuationLoading.value = true
  try {
    valuation.value = await fetchIndexValuation(props.indexCode, params)
  } catch {
    valuation.value = []
  } finally {
    valuationLoading.value = false
  }
}

async function initCharts() {
  const echarts = await import('echarts')

  if (chartEl.value && bars.value.length) {
    chartInstance?.dispose()
    chartInstance = echarts.init(chartEl.value, null, { renderer: 'canvas' })
    const dates = bars.value.map(b => b.trade_date)
    const candleData = bars.value.map(b => [b.open_price, b.close_price, b.low_price, b.high_price])
    const volumes = bars.value.map(b => b.volume ?? 0)
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
          itemStyle: { color: 'rgba(59,130,246,0.5)' },
        },
      ],
    })
  }

  if (valuationChartEl.value && valuation.value.length) {
    valuationChartInstance?.dispose()
    valuationChartInstance = echarts.init(valuationChartEl.value, null, { renderer: 'canvas' })
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
}

watch([bars, valuation, chartEl, valuationChartEl], () => {
  if (!barsLoading.value && !valuationLoading.value) initCharts()
}, { flush: 'post' })

onMounted(async () => {
  try {
    const [indexes, dr] = await Promise.all([
      fetchBenchmarkIndexes(),
      fetchIndexDateRange(props.indexCode).catch(() => ({ min_date: null, max_date: null })),
    ])
    const found = indexes.find(i => i.index_code === props.indexCode)
    if (found) indexName.value = found.index_name
    dateRange.value = dr
  } catch {
    // 元数据获取失败不影响主流程
  }

  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - 60)
  barStartDate.value = formatDate(start)
  barEndDate.value = formatDate(end)

  await Promise.all([loadBars(), loadValuation({ limit: 60 })])
  loading.value = false
})

onUnmounted(() => {
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
</style>
