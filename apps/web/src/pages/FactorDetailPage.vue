<template>
  <div class="page">
    <div v-if="specLoading" class="empty">加载中...</div>
    <template v-else-if="spec">
      <!-- 页头 -->
      <div class="page-header">
        <div class="header-main">
          <h1 class="page-title">{{ spec.name }}</h1>
          <div class="badges">
            <span class="factor-id">{{ spec.factor_id }}</span>
            <span class="chip" :class="`chip-${spec.category}`">{{ CATEGORY_LABELS[spec.category] ?? spec.category }}</span>
            <span class="chip chip-ver">v{{ spec.version }}</span>
          </div>
        </div>
        <p class="factor-desc">{{ spec.description }}</p>
        <div class="meta-row">
          <span class="meta-label">所需数据</span>
          <span class="meta-val">{{ spec.required_data.join(', ') }}</span>
        </div>
      </div>

      <!-- Tab 切换 -->
      <div class="tabs">
        <button class="tab-btn" :class="{ active: tab === 'cross' }" @click="tab = 'cross'">横截面</button>
        <button class="tab-btn" :class="{ active: tab === 'series' }" @click="tab = 'series'">时间序列</button>
      </div>

      <!-- 横截面 Tab -->
      <div v-show="tab === 'cross'" class="card">
        <div class="card-header">
          <span class="card-title">横截面快照</span>
          <div class="controls">
            <input type="date" class="date-input" v-model="crossDate" />
            <button class="query-btn" @click="loadCross">查询</button>
          </div>
        </div>
        <div v-if="crossLoading" class="empty">加载中...</div>
        <div v-else-if="crossRows.length === 0" class="empty">暂无数据，请选择日期并查询</div>
        <table v-else class="data-table">
          <thead>
            <tr>
              <th>排名</th>
              <th>ETF 代码</th>
              <th>因子值</th>
              <th class="bar-col">相对分布</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, idx) in crossRows" :key="row.etf_code">
              <td class="rank">{{ idx + 1 }}</td>
              <td>
                <RouterLink :to="`/etfs/${row.etf_code}`" class="etf-link">{{ row.etf_code }}</RouterLink>
              </td>
              <td class="value mono">{{ row.factor_value_numeric != null ? row.factor_value_numeric.toFixed(4) : '—' }}</td>
              <td class="bar-cell">
                <div class="bar-track">
                  <div
                    class="bar-fill"
                    :style="{ width: barWidth(row.factor_value_numeric) + '%' }"
                  ></div>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 时间序列 Tab -->
      <div v-show="tab === 'series'" class="card">
        <div class="card-header">
          <span class="card-title">时间序列</span>
          <div class="controls">
            <input
              type="text"
              class="etf-input"
              v-model="seriesEtf"
              placeholder="ETF代码，如 510300"
              @keyup.enter="loadSeries"
            />
            <input type="date" class="date-input" v-model="seriesStart" />
            <span class="sep">~</span>
            <input type="date" class="date-input" v-model="seriesEnd" />
            <button class="query-btn" @click="loadSeries">查询</button>
          </div>
        </div>
        <div v-if="seriesLoading" class="empty">加载中...</div>
        <div v-else-if="seriesRows.length === 0" class="empty">输入 ETF 代码并查询，查看因子值走势</div>
        <div v-else ref="chartEl" class="chart-container"></div>
      </div>
    </template>
    <div v-else class="empty">因子不存在</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchFactorCrossSection, fetchFactorSpecs, fetchFactorTimeSeries } from '../api/factors'
import type { FactorRow, FactorSpec } from '../types/api'

const props = defineProps<{ factorId: string }>()

const spec = ref<FactorSpec | null>(null)
const specLoading = ref(false)

const tab = ref<'cross' | 'series'>('cross')

/** 横截面状态 */
const crossDate = ref(new Date().toISOString().slice(0, 10))
const crossRows = ref<FactorRow[]>([])
const crossLoading = ref(false)

/** 时间序列状态 */
const seriesEtf = ref('')
const seriesRows = ref<FactorRow[]>([])
const seriesLoading = ref(false)
const chartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let chartInstance: any = null

const CATEGORY_LABELS: Record<string, string> = {
  volume: '量能',
  momentum: '动量',
  volatility: '波动率',
  flow: '份额流',
  valuation: '估值',
}

/** 默认时间序列日期范围：最近 90 天 */
const seriesStart = ref((() => {
  const d = new Date()
  d.setDate(d.getDate() - 90)
  return d.toISOString().slice(0, 10)
})())
const seriesEnd = ref(new Date().toISOString().slice(0, 10))

/** 横截面 bar 宽度：归一化到 [0, 100] */
const crossMin = computed(() =>
  crossRows.value.reduce((m, r) => Math.min(m, r.factor_value_numeric ?? m), Infinity),
)
const crossMax = computed(() =>
  crossRows.value.reduce((m, r) => Math.max(m, r.factor_value_numeric ?? m), -Infinity),
)

function barWidth(val: number | null): number {
  if (val === null || crossMin.value === Infinity) return 0
  const range = crossMax.value - crossMin.value
  if (range === 0) return 50
  return ((val - crossMin.value) / range) * 100
}

/** 加载横截面数据，按因子值降序排列 */
async function loadCross() {
  crossLoading.value = true
  try {
    const rows = await fetchFactorCrossSection(props.factorId, crossDate.value)
    crossRows.value = rows
      .filter(r => r.factor_value_numeric !== null)
      .sort((a, b) => (b.factor_value_numeric ?? 0) - (a.factor_value_numeric ?? 0))
  } catch {
    crossRows.value = []
  } finally {
    crossLoading.value = false
  }
}

/** 加载时间序列数据并渲染图表 */
async function loadSeries() {
  if (!seriesEtf.value.trim()) return
  seriesLoading.value = true
  try {
    seriesRows.value = await fetchFactorTimeSeries(
      props.factorId,
      seriesEtf.value.trim(),
      seriesStart.value,
      seriesEnd.value,
    )
  } catch {
    seriesRows.value = []
  } finally {
    seriesLoading.value = false
  }
}

/** 渲染 ECharts 时间序列折线图 */
async function renderChart() {
  if (!chartEl.value || seriesRows.value.length === 0) return
  const echarts = await import('echarts')
  chartInstance?.dispose()
  chartInstance = echarts.init(chartEl.value, null, { renderer: 'canvas' })
  const dates = seriesRows.value.map(r => r.trade_date)
  const values = seriesRows.value.map(r => r.factor_value_numeric)
  chartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#f1f5f9', fontSize: 12 },
      formatter: (params: { name: string; value: number | null }[]) => {
        const p = params[0]
        return `${p.name}<br/>${spec.value?.name ?? props.factorId}: <b>${p.value?.toFixed(4) ?? '—'}</b>`
      },
    },
    grid: { left: 64, right: 20, top: 24, bottom: 36 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
    },
    yAxis: {
      scale: true,
      splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
    },
    series: [
      {
        type: 'line',
        data: values,
        smooth: false,
        lineStyle: { color: '#3b82f6', width: 2 },
        itemStyle: { color: '#3b82f6' },
        areaStyle: { color: 'rgba(59, 130, 246, 0.08)' },
        symbol: 'circle',
        symbolSize: 4,
      },
    ],
  })
}

watch([seriesRows, chartEl], () => {
  if (!seriesLoading.value) renderChart()
}, { flush: 'post' })

onMounted(async () => {
  specLoading.value = true
  try {
    const all = await fetchFactorSpecs()
    spec.value = all.find(s => s.factor_id === props.factorId) ?? null
  } catch {
    spec.value = null
  } finally {
    specLoading.value = false
  }
  await loadCross()
})

onUnmounted(() => {
  chartInstance?.dispose()
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.empty { padding: 60px; text-align: center; color: var(--text-muted); }

.page-header {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.header-main { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.page-title { font-size: 20px; font-weight: 700; }

.badges { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.factor-id {
  font-family: monospace;
  font-size: 12px;
  color: var(--accent);
  background: rgba(59, 130, 246, 0.1);
  padding: 2px 10px;
  border-radius: 20px;
  font-weight: 600;
}

.chip { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; }
.chip-volume    { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.chip-momentum  { background: rgba(34, 197, 94, 0.12);  color: #4ade80; }
.chip-volatility{ background: rgba(239, 68, 68, 0.12);  color: #f87171; }
.chip-flow      { background: rgba(139, 92, 246, 0.15); color: #a78bfa; }
.chip-valuation { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
.chip-ver       { background: var(--surface-2); color: var(--text-muted); }

.factor-desc { font-size: 13px; color: var(--text-muted); line-height: 1.6; }

.meta-row { display: flex; align-items: center; gap: 8px; }
.meta-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.meta-val   { font-size: 13px; font-family: monospace; }

/* Tabs */
.tabs { display: flex; gap: 4px; }
.tab-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 6px 18px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.tab-btn:hover { color: var(--text); border-color: var(--accent); }
.tab-btn.active { background: rgba(59, 130, 246, 0.15); color: var(--accent); border-color: var(--accent); }

/* Cards */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.card-title { font-size: 14px; font-weight: 600; }

.controls { display: flex; align-items: center; gap: 8px; margin-left: auto; flex-wrap: wrap; }

.date-input {
  background: var(--surface-2, rgba(255,255,255,0.05));
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 4px 8px;
  font-size: 12px;
  outline: none;
  width: 130px;
  color-scheme: dark;
}
.date-input:focus { border-color: var(--accent); }

.etf-input {
  background: var(--surface-2, rgba(255,255,255,0.05));
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 4px 10px;
  font-size: 12px;
  outline: none;
  width: 140px;
}
.etf-input:focus { border-color: var(--accent); }
.etf-input::placeholder { color: var(--text-muted); }

.query-btn {
  background: rgba(59, 130, 246, 0.15);
  border: 1px solid rgba(59, 130, 246, 0.3);
  color: var(--accent);
  border-radius: var(--radius-sm);
  padding: 4px 14px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.query-btn:hover { background: rgba(59, 130, 246, 0.25); }

.sep { color: var(--text-muted); font-size: 12px; }

/* 横截面表格 */
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th {
  text-align: left;
  padding: 8px 16px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border);
}
.data-table td { padding: 9px 16px; border-bottom: 1px solid var(--border); }
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: rgba(255,255,255,0.02); }

.rank { color: var(--text-muted); font-size: 12px; width: 48px; }
.value.mono { font-family: monospace; font-size: 13px; }
.bar-col { width: 180px; }
.etf-link { color: var(--accent); font-family: monospace; font-weight: 600; }
.etf-link:hover { text-decoration: underline; }

.bar-cell { padding: 9px 16px 9px 0; }
.bar-track { background: var(--surface-2, rgba(255,255,255,0.06)); border-radius: 4px; height: 6px; overflow: hidden; }
.bar-fill  { height: 100%; background: var(--accent); border-radius: 4px; transition: width 0.3s ease; }

/* 时间序列图表 */
.chart-container { width: 100%; height: 340px; padding: 8px; }
</style>
