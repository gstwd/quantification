<template>
  <div class="page">
    <div v-if="loading" class="loading">加载中...</div>
    <template v-else>
      <div class="page-header">
        <div class="header-main">
          <h1 class="page-title">{{ indexName }}</h1>
          <span class="code-badge">{{ indexCode }}</span>
        </div>
        <div v-if="latestValuation" class="stat-row">
          <div class="stat-item">
            <span class="stat-label">PE(TTM)</span>
            <span class="stat-value">{{ latestValuation.pe?.toFixed(1) ?? '—' }}</span>
            <span class="stat-sub">分位 {{ latestValuation.pe_percentile?.toFixed(0) ?? '—' }}%</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">PB</span>
            <span class="stat-value">{{ latestValuation.pb?.toFixed(2) ?? '—' }}</span>
            <span class="stat-sub">分位 {{ latestValuation.pb_percentile?.toFixed(0) ?? '—' }}%</span>
          </div>
          <div class="stat-item">
            <span class="stat-label">股息率</span>
            <span class="stat-value">{{ latestValuation.dividend_yield?.toFixed(2) ?? '—' }}%</span>
          </div>
        </div>
        <div v-else class="no-valuation">暂无估值数据</div>
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
          <span class="card-title">PE / PB 估值</span>
          <span class="card-sub text-muted">来源: {{ valuation[0]?.source ?? '—' }}</span>
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

import { fetchBenchmarkIndexes, fetchIndexDailyBars, fetchIndexValuation } from '../api/market_data'
import type { DailyBar, IndexValuation } from '../types/api'

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

async function loadData() {
  barsLoading.value = true
  valuationLoading.value = true
  try {
    const [indexes, barsData, valuationData] = await Promise.all([
      fetchBenchmarkIndexes(),
      fetchIndexDailyBars(props.indexCode, 60),
      fetchIndexValuation(props.indexCode, 60),
    ])
    const found = indexes.find(i => i.index_code === props.indexCode)
    if (found) indexName.value = found.index_name
    bars.value = barsData
    valuation.value = valuationData
  } catch {
    // 数据拉取失败时保持空状态，由模板展示占位
  } finally {
    barsLoading.value = false
    valuationLoading.value = false
    loading.value = false
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
      tooltip: { trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
      legend: { data: ['PE', 'PB'], textStyle: { color: '#94a3b8' }, top: 4 },
      grid: { left: 60, right: 60, top: 40, bottom: 30 },
      xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      yAxis: [
        { name: 'PE', nameTextStyle: { color: '#3b82f6' }, splitLine: { lineStyle: { color: '#334155', type: 'dashed' } }, axisLabel: { color: '#3b82f6', fontSize: 11 } },
        { name: 'PB', nameTextStyle: { color: '#f59e0b' }, splitLine: { show: false }, axisLabel: { color: '#f59e0b', fontSize: 11 } },
      ],
      series: [
        { name: 'PE', type: 'line', yAxisIndex: 0, data: valuation.value.map(v => v.pe), smooth: true, lineStyle: { color: '#3b82f6', width: 2 }, symbol: 'none' },
        { name: 'PB', type: 'line', yAxisIndex: 1, data: valuation.value.map(v => v.pb), smooth: true, lineStyle: { color: '#f59e0b', width: 2 }, symbol: 'none' },
      ],
    })
  }
}

watch([bars, valuation, chartEl, valuationChartEl], () => {
  if (!barsLoading.value && !valuationLoading.value) initCharts()
}, { flush: 'post' })

onMounted(loadData)

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
}
.card-title { font-size: 14px; font-weight: 600; }
.card-sub { font-size: 12px; }
.text-muted { color: var(--text-muted); }

.chart-placeholder { padding: 60px; text-align: center; color: var(--text-muted); }
.chart-container { width: 100%; height: 380px; padding: 8px; }
</style>
