<template>
  <div class="page">
    <div v-if="store.loading" class="loading">加载中...</div>
    <template v-else-if="store.current">
      <div class="page-header">
        <div class="header-main">
          <h1 class="page-title">{{ store.current.name_cn }}</h1>
          <div class="header-badges">
            <span class="code-badge">{{ store.current.etf_code }}</span>
            <span class="badge" :class="store.current.exchange === 'SSE' ? 'badge-sse' : 'badge-szse'">{{ store.current.exchange }}</span>
            <span class="badge badge-category">{{ store.current.category }}</span>
          </div>
        </div>
        <div class="header-meta">
          <div class="meta-item">
            <span class="meta-label">跟踪指数</span>
            <span class="meta-val">{{ store.current.tracking_index_name }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">基金公司</span>
            <span class="meta-val">{{ store.current.fund_company || '—' }}</span>
          </div>
          <div class="meta-item" v-if="latestShare">
            <span class="meta-label">规模 (亿)</span>
            <span class="meta-val">{{ latestShare.aum?.toFixed(1) ?? '—' }}</span>
          </div>
          <div class="meta-item" v-if="latestShare">
            <span class="meta-label">份额 (亿份)</span>
            <span class="meta-val">{{ latestShare.shares_total?.toFixed(2) ?? '—' }}</span>
          </div>
        </div>
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
          <span class="card-title">份额历史（近 30 日）</span>
        </div>
        <div v-if="sharesLoading" class="chart-placeholder">加载中...</div>
        <div v-else-if="shares.length === 0" class="chart-placeholder">暂无份额数据</div>
        <div v-else ref="shareChartEl" class="chart-container" style="height: 200px;"></div>
      </div>
    </template>
    <div v-else class="loading">ETF 不存在</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import { fetchDailyBars, fetchEtfDateRange, fetchShareHistory } from '../api/etfs'
import { useEtfStore } from '../stores/etfs'
import type { DailyBar, DateRange, ShareSnapshot } from '../types/api'

const props = defineProps<{ etfCode: string }>()
const store = useEtfStore()

const bars = ref<DailyBar[]>([])
const shares = ref<ShareSnapshot[]>([])
const barsLoading = ref(false)
const sharesLoading = ref(false)

const chartEl = ref<HTMLElement | null>(null)
const shareChartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let chartInstance: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let shareChartInstance: any = null

const latestShare = computed(() => shares.value.length ? shares.value[shares.value.length - 1] : null)

/** 日期范围元数据 */
const dateRange = ref<DateRange>({ min_date: null, max_date: null })

/** 日期选择器绑定值 */
const barStartDate = ref('')
const barEndDate = ref('')
const activePreset = ref('近60日')

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

/** 应用快捷日期范围 */
async function applyPreset(preset: { label: string; days: number }) {
  activePreset.value = preset.label
  if (preset.days === 0) {
    barStartDate.value = dateRange.value.min_date ?? ''
    barEndDate.value = dateRange.value.max_date ?? ''
  } else {
    const end = new Date()
    const start = new Date()
    start.setDate(start.getDate() - preset.days)
    barStartDate.value = formatDate(start)
    barEndDate.value = formatDate(end)
  }
  await loadBars()
}

/** 应用自定义日期范围 */
async function applyCustomRange() {
  activePreset.value = ''
  await loadBars()
}

/** 加载 K 线数据 */
async function loadBars() {
  barsLoading.value = true
  try {
    if (barStartDate.value && barEndDate.value) {
      bars.value = await fetchDailyBars(props.etfCode, {
        startDate: barStartDate.value,
        endDate: barEndDate.value,
      })
    } else {
      bars.value = await fetchDailyBars(props.etfCode, { limit: 60 })
    }
  } catch {
    bars.value = []
  } finally {
    barsLoading.value = false
  }
}

/** 加载份额数据 */
async function loadShares() {
  sharesLoading.value = true
  try {
    shares.value = await fetchShareHistory(props.etfCode, { limit: 30 })
  } catch {
    shares.value = []
  } finally {
    sharesLoading.value = false
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

  if (shareChartEl.value && shares.value.length) {
    shareChartInstance?.dispose()
    shareChartInstance = echarts.init(shareChartEl.value, null, { renderer: 'canvas' })
    shareChartInstance.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
      grid: { left: 60, right: 20, top: 20, bottom: 30 },
      xAxis: { type: 'category', data: shares.value.map(s => s.trade_date), axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      yAxis: { scale: true, splitLine: { lineStyle: { color: '#334155', type: 'dashed' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      series: [{ type: 'line', data: shares.value.map(s => s.shares_total), smooth: true, lineStyle: { color: '#3b82f6', width: 2 }, areaStyle: { color: 'rgba(59,130,246,0.08)' }, symbol: 'none' }],
    })
  }
}

watch([bars, shares, chartEl, shareChartEl], () => {
  if (!barsLoading.value && !sharesLoading.value) initCharts()
}, { flush: 'post' })

onMounted(async () => {
  await store.loadOne(props.etfCode)
  try {
    dateRange.value = await fetchEtfDateRange(props.etfCode)
  } catch {
    // 日期范围获取失败不影响主流程
  }
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - 60)
  barStartDate.value = formatDate(start)
  barEndDate.value = formatDate(end)
  await Promise.all([loadBars(), loadShares()])
})

onUnmounted(() => {
  chartInstance?.dispose()
  shareChartInstance?.dispose()
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
.header-badges { display: flex; gap: 8px; align-items: center; }

.code-badge {
  font-family: monospace;
  font-size: 13px;
  font-weight: 700;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 3px 10px;
  border-radius: 20px;
}

.badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge-sse { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
.badge-szse { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.badge-category { background: var(--surface-2); color: var(--text-muted); }

.header-meta { display: flex; gap: 32px; flex-wrap: wrap; }
.meta-item { display: flex; flex-direction: column; gap: 2px; }
.meta-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.meta-val { font-size: 14px; font-weight: 500; }

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
