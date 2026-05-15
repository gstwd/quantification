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
          <span class="card-title">K 线图（近 60 日）</span>
          <span class="card-sub text-muted">来源: {{ bars[0]?.source ?? '—' }}</span>
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

import { fetchDailyBars, fetchShareHistory } from '../api/etfs'
import { useEtfStore } from '../stores/etfs'
import type { DailyBar, ShareSnapshot } from '../types/api'

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

async function loadData() {
  barsLoading.value = true
  sharesLoading.value = true
  try {
    bars.value = await fetchDailyBars(props.etfCode, 60)
  } finally {
    barsLoading.value = false
  }
  try {
    shares.value = await fetchShareHistory(props.etfCode, 30)
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
        { scale: true, splitLine: { show: false }, axisLabel: { color: '#94a3b8', fontSize: 10 }, gridIndex: 1 },
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
  await loadData()
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
}
.card-title { font-size: 14px; font-weight: 600; }
.card-sub { font-size: 12px; }
.text-muted { color: var(--text-muted); }

.chart-placeholder { padding: 60px; text-align: center; color: var(--text-muted); }
.chart-container { width: 100%; height: 380px; padding: 8px; }
</style>
