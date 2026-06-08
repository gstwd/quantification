<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">宏观指标</h1>
      <button class="btn-secondary" :disabled="refreshing" @click="handleRefreshData">{{ refreshing ? '刷新中...' : '刷新数据' }}</button>
    </div>

    <!-- 刷新提示 -->
    <div v-if="refreshMsg" class="refresh-banner" :class="refreshOk ? 'banner-ok' : 'banner-err'">{{ refreshMsg }}</div>

    <div v-if="loading" class="loading">加载中...</div>
    <template v-else>
      <!-- CPI -->
      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">CPI 居民消费价格指数（同比）<HelpTip :text="macroHelp('cpi')" /></span>
          <span v-if="cpi.length" class="card-badge">{{ cpi[cpi.length - 1].period }}: {{ cpi[cpi.length - 1].value.toFixed(2) }}%</span>
        </div>
        <div v-if="cpi.length === 0" class="chart-placeholder">暂无 CPI 数据</div>
        <div v-else ref="cpiChartEl" class="chart-container"></div>
      </div>

      <!-- PMI -->
      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">PMI 制造业采购经理指数 <HelpTip :text="macroHelp('pmi')" /></span>
          <span v-if="pmi.length" class="card-badge">{{ pmi[pmi.length - 1].period }}: {{ pmi[pmi.length - 1].value.toFixed(1) }}%</span>
        </div>
        <div v-if="pmi.length === 0" class="chart-placeholder">暂无 PMI 数据</div>
        <div v-else ref="pmiChartEl" class="chart-container"></div>
      </div>

      <!-- LPR -->
      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">LPR 贷款市场报价利率 <HelpTip :text="macroHelp('lpr1y')" /></span>
          <div class="card-header-right">
            <span v-if="lpr1y.length" class="card-badge">1Y: {{ lpr1y[lpr1y.length - 1].value.toFixed(2) }}%</span>
            <span v-if="lpr5y.length" class="card-badge">5Y: {{ lpr5y[lpr5y.length - 1].value.toFixed(2) }}%</span>
          </div>
        </div>
        <div v-if="lpr1y.length === 0 && lpr5y.length === 0" class="chart-placeholder">暂无 LPR 数据</div>
        <div v-else ref="lprChartEl" class="chart-container"></div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'

import { triggerMacroRefresh } from '../api/runs'
import { fetchMacroIndicator } from '../api/market_data'
import type { MacroIndicator } from '../types/api'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'

/** 获取宏观指标描述的快捷方法 */
function macroHelp(key: string): string {
  return getIndicator('macro', key)?.description ?? ''
}

const cpi = ref<MacroIndicator[]>([])
const pmi = ref<MacroIndicator[]>([])
const lpr1y = ref<MacroIndicator[]>([])
const lpr5y = ref<MacroIndicator[]>([])
const loading = ref(true)
const refreshing = ref(false)
const refreshMsg = ref('')
const refreshOk = ref(true)

const cpiChartEl = ref<HTMLElement | null>(null)
const pmiChartEl = ref<HTMLElement | null>(null)
const lprChartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let cpiChart: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let pmiChart: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let lprChart: any = null

/** 触发宏观数据刷新 */
async function handleRefreshData() {
  refreshing.value = true
  refreshMsg.value = ''
  try {
    const res = await triggerMacroRefresh()
    refreshMsg.value = `宏观数据刷新已触发，可在运行记录中查看进度 (${res.run_id.slice(0, 8)}…)`
    refreshOk.value = true
  } catch {
    refreshMsg.value = '触发失败，请重试'
    refreshOk.value = false
  } finally {
    refreshing.value = false
    setTimeout(() => { refreshMsg.value = '' }, 5000)
  }
}

async function loadData() {
  try {
    const [c, p, l1, l5] = await Promise.all([
      fetchMacroIndicator('cpi', 120),
      fetchMacroIndicator('pmi', 120),
      fetchMacroIndicator('lpr1y', 60),
      fetchMacroIndicator('lpr5y', 60),
    ])
    cpi.value = c
    pmi.value = p
    lpr1y.value = l1
    lpr5y.value = l5
  } catch {
    // 拉取失败时保持空状态
  } finally {
    loading.value = false
  }
}

function makeLineOption(dates: string[], values: (number | null)[], color: string) {
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
    grid: { left: 60, right: 20, top: 20, bottom: 40 },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: '#334155', type: 'dashed' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    dataZoom: [{ type: 'inside', start: 0, end: 100 }, { type: 'slider', bottom: 6, height: 24, borderColor: '#334155', fillerColor: 'rgba(59,130,246,0.1)', handleStyle: { color: '#3b82f6' }, textStyle: { color: '#94a3b8', fontSize: 10 } }],
    series: [{ type: 'line', data: values, smooth: false, lineStyle: { color, width: 2 }, symbol: 'none' }],
  }
}

async function initCharts() {
  const echarts = await import('echarts')

  if (cpiChartEl.value && cpi.value.length) {
    cpiChart?.dispose()
    cpiChart = echarts.init(cpiChartEl.value, null, { renderer: 'canvas' })
    cpiChart.setOption(makeLineOption(
      cpi.value.map(v => v.period),
      cpi.value.map(v => v.value),
      '#3b82f6',
    ))
  }

  if (pmiChartEl.value && pmi.value.length) {
    pmiChart?.dispose()
    pmiChart = echarts.init(pmiChartEl.value, null, { renderer: 'canvas' })
    const opt = makeLineOption(
      pmi.value.map(v => v.period),
      pmi.value.map(v => v.value),
      '#f59e0b',
    )
    opt.series.push({
      type: 'line', name: '荣枯线', data: [], markLine: { silent: true, symbol: 'none', lineStyle: { color: '#ef4444', type: 'dashed', width: 1 }, label: { color: '#ef4444', fontSize: 11 }, data: [{ yAxis: 50, label: { formatter: '荣枯线 50' } }] },
    } as any)
    pmiChart.setOption(opt)
  }

  if (lprChartEl.value && (lpr1y.value.length || lpr5y.value.length)) {
    lprChart?.dispose()
    lprChart = echarts.init(lprChartEl.value, null, { renderer: 'canvas' })
    const allPeriods = [...new Set([...lpr1y.value.map(v => v.period), ...lpr5y.value.map(v => v.period)])].sort()
    const lpr1yMap = new Map(lpr1y.value.map(v => [v.period, v.value]))
    const lpr5yMap = new Map(lpr5y.value.map(v => [v.period, v.value]))
    lprChart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
      legend: { data: ['1年期 LPR', '5年期 LPR'], textStyle: { color: '#94a3b8' }, top: 4 },
      grid: { left: 60, right: 20, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: allPeriods, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      yAxis: { scale: true, splitLine: { lineStyle: { color: '#334155', type: 'dashed' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      dataZoom: [{ type: 'inside', start: 0, end: 100 }, { type: 'slider', bottom: 6, height: 24, borderColor: '#334155', fillerColor: 'rgba(59,130,246,0.1)', handleStyle: { color: '#3b82f6' }, textStyle: { color: '#94a3b8', fontSize: 10 } }],
      series: [
        { name: '1年期 LPR', type: 'line', data: allPeriods.map(p => lpr1yMap.get(p) ?? null), smooth: false, lineStyle: { color: '#3b82f6', width: 2 }, symbol: 'none' },
        { name: '5年期 LPR', type: 'line', data: allPeriods.map(p => lpr5yMap.get(p) ?? null), smooth: false, lineStyle: { color: '#f59e0b', width: 2 }, symbol: 'none' },
      ],
    })
  }
}

watch([cpi, pmi, lpr1y, lpr5y, cpiChartEl, pmiChartEl, lprChartEl], () => {
  if (!loading.value) initCharts()
}, { flush: 'post' })

onMounted(loadData)

onUnmounted(() => {
  cpiChart?.dispose()
  pmiChart?.dispose()
  lprChart?.dispose()
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 24px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }
.btn-secondary {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.btn-secondary:hover { background: var(--surface-2); border-color: var(--accent); }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
.refresh-banner {
  padding: 10px 16px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.banner-ok { background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3); color: var(--success); }
.banner-err { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); color: var(--danger); }
.loading { padding: 60px; text-align: center; color: var(--text-muted); }

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
.card-header-right { display: flex; gap: 8px; margin-left: auto; }
.card-badge {
  font-size: 12px;
  font-weight: 600;
  color: var(--accent);
  background: rgba(59, 130, 246, 0.1);
  padding: 2px 8px;
  border-radius: 20px;
}

.chart-placeholder { padding: 60px; text-align: center; color: var(--text-muted); }
.chart-container { width: 100%; height: 340px; padding: 8px; }
</style>
