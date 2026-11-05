<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">宏观指标</h1>
      <RouterLink to="/data-management?dataset=macro_indicator" class="btn-secondary">维护数据</RouterLink>
    </div>

    <!-- 刷新提示 -->
    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="loadError" class="load-error">
      {{ loadError }}
      <button class="btn-secondary" @click="reload">重试</button>
    </div>
    <template v-else>
      <!-- CPI -->
      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">CPI 居民消费价格指数（同比）<HelpTip :text="macroHelp('cpi')" /></span>
          <span v-if="cpi.length" class="card-badge">{{ cpi[cpi.length - 1].period }}: {{ cpi[cpi.length - 1].value.toFixed(2) }}%</span>
        </div>
        <div v-if="cpi.length === 0" class="chart-placeholder">
          暂无 CPI 数据，请到<router-link to="/data-management">数据管理页</router-link>执行同步
        </div>
        <div v-else ref="cpiChartEl" class="chart-container"></div>
      </div>

      <!-- PMI -->
      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">PMI 制造业采购经理指数 <HelpTip :text="macroHelp('pmi')" /></span>
          <span v-if="pmi.length" class="card-badge">{{ pmi[pmi.length - 1].period }}: {{ pmi[pmi.length - 1].value.toFixed(1) }}</span>
        </div>
        <div v-if="pmi.length === 0" class="chart-placeholder">
          暂无 PMI 数据，请到<router-link to="/data-management">数据管理页</router-link>执行同步
        </div>
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
        <div v-if="lpr1y.length === 0 && lpr5y.length === 0" class="chart-placeholder">
          暂无 LPR 数据，请到<router-link to="/data-management">数据管理页</router-link>执行同步
        </div>
        <div v-else ref="lprChartEl" class="chart-container"></div>
      </div>

      <!-- 货币供应 -->
      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">货币供应 M1 / M2 同比 <HelpTip :text="macroHelp('m1_yoy')" /></span>
          <div class="card-header-right">
            <span v-if="m1.length" class="card-badge">M1: {{ m1[m1.length - 1].value.toFixed(1) }}%</span>
            <span v-if="m2.length" class="card-badge">M2: {{ m2[m2.length - 1].value.toFixed(1) }}%</span>
          </div>
        </div>
        <div v-if="m1.length === 0 && m2.length === 0" class="chart-placeholder">
          暂无货币供应数据，请到<router-link to="/data-management?dataset=macro_indicator">数据管理页</router-link>执行同步
        </div>
        <div v-else ref="moneyChartEl" class="chart-container"></div>
      </div>

      <!-- 日频市场指标 -->
      <div class="chart-card">
        <div class="card-header">
          <span class="card-title">10 年期国债收益率 / 沪深融资余额 <HelpTip :text="macroHelp('cgb10y')" /></span>
          <div class="card-header-right">
            <span v-if="cgb10y.length" class="card-badge">10Y: {{ cgb10y[cgb10y.length - 1].value.toFixed(3) }}%</span>
            <span v-if="margin.length" class="card-badge">融资余额: {{ margin[margin.length - 1].value.toFixed(0) }} 亿</span>
          </div>
        </div>
        <div v-if="cgb10y.length === 0 && margin.length === 0" class="chart-placeholder">
          暂无日频市场指标，请到<router-link to="/data-management?dataset=market_indicator">数据管理页</router-link>执行同步
        </div>
        <div v-else ref="marketChartEl" class="chart-container"></div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import type { EChartsCoreOption } from 'echarts'

import { fetchMacroIndicator, fetchMarketIndicator } from '../api/market_data'
import type { MacroIndicator, MarketIndicatorPoint } from '../types/api'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'
import { getErrorMessage } from '../api/client'
import { useChart } from '../composables/useChart'

/** 获取宏观指标描述的快捷方法 */
function macroHelp(key: string): string {
  return getIndicator('macro', key)?.description ?? ''
}

const cpi = ref<MacroIndicator[]>([])
const pmi = ref<MacroIndicator[]>([])
const lpr1y = ref<MacroIndicator[]>([])
const lpr5y = ref<MacroIndicator[]>([])
const m1 = ref<MacroIndicator[]>([])
const m2 = ref<MacroIndicator[]>([])
const cgb10y = ref<MarketIndicatorPoint[]>([])
const margin = ref<MarketIndicatorPoint[]>([])
const moneyChartEl = ref<HTMLElement | null>(null)
const marketChartEl = ref<HTMLElement | null>(null)
const loading = ref(true)
/** 加载失败文案（与「暂无数据」区分） */
const loadError = ref('')

const cpiChartEl = ref<HTMLElement | null>(null)
const pmiChartEl = ref<HTMLElement | null>(null)
const lprChartEl = ref<HTMLElement | null>(null)
/** 图表实例由 useChart 管理：尺寸变化自动 resize，卸载时释放 */
const cpiChart = useChart(cpiChartEl)
const pmiChart = useChart(pmiChartEl)
const lprChart = useChart(lprChartEl)
const moneyChart = useChart(moneyChartEl)
const marketChart = useChart(marketChartEl)

async function loadData() {
  try {
    const [c, p, l1, l5, mm1, mm2, bond, rz] = await Promise.all([
      fetchMacroIndicator('cpi', 120),
      fetchMacroIndicator('pmi', 120),
      fetchMacroIndicator('lpr1y', 60),
      fetchMacroIndicator('lpr5y', 60),
      fetchMacroIndicator('m1_yoy', 120),
      fetchMacroIndicator('m2_yoy', 120),
      fetchMarketIndicator('cgb10y', 750),
      fetchMarketIndicator('margin_rzye', 750),
    ])
    cpi.value = c
    pmi.value = p
    lpr1y.value = l1
    lpr5y.value = l5
    m1.value = mm1
    m2.value = mm2
    cgb10y.value = bond
    margin.value = rz
    loadError.value = ''
  } catch (e) {
    loadError.value = `宏观指标加载失败：${getErrorMessage(e, '网络错误')}`
  } finally {
    loading.value = false
  }
}

/** 重新加载 */
function reload(): void {
  loading.value = true
  void loadData()
}

function makeLineOption(dates: string[], values: (number | null)[], color: string) {
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
    grid: { left: 60, right: 20, top: 20, bottom: 64 },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    yAxis: { scale: true, splitLine: { lineStyle: { color: '#334155', type: 'dashed' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    dataZoom: [{ type: 'inside', start: 0, end: 100 }, { type: 'slider', bottom: 6, height: 24, borderColor: '#334155', fillerColor: 'rgba(59,130,246,0.1)', handleStyle: { color: '#3b82f6' }, textStyle: { color: '#94a3b8', fontSize: 10 } }],
    series: [{ type: 'line', data: values, smooth: false, lineStyle: { color, width: 2 }, itemStyle: { color }, symbol: 'none' }] as Array<Record<string, unknown>>,
  }
}

async function initCharts() {
  if (cpiChartEl.value && cpi.value.length) {
    await cpiChart.render(makeLineOption(
      cpi.value.map(v => v.period),
      cpi.value.map(v => v.value),
      '#3b82f6',
    ))
  }

  if (pmiChartEl.value && pmi.value.length) {
    const opt = makeLineOption(
      pmi.value.map(v => v.period),
      pmi.value.map(v => v.value),
      '#f59e0b',
    )
    opt.series.push({
      type: 'line', name: '荣枯线', data: [], markLine: { silent: true, symbol: 'none', lineStyle: { color: '#ef4444', type: 'dashed', width: 1 }, label: { color: '#ef4444', fontSize: 11 }, data: [{ yAxis: 50, label: { formatter: '荣枯线 50' } }] },
    })
    await pmiChart.render(opt as EChartsCoreOption)
  }

  if (lprChartEl.value && (lpr1y.value.length || lpr5y.value.length)) {
    const allPeriods = [...new Set([...lpr1y.value.map(v => v.period), ...lpr5y.value.map(v => v.period)])].sort()
    const lpr1yMap = new Map(lpr1y.value.map(v => [v.period, v.value]))
    const lpr5yMap = new Map(lpr5y.value.map(v => [v.period, v.value]))
    await lprChart.render({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
      legend: { data: ['1年期 LPR', '5年期 LPR'], textStyle: { color: '#94a3b8' }, top: 4 },
      grid: { left: 60, right: 20, top: 40, bottom: 64 },
      xAxis: { type: 'category', data: allPeriods, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      yAxis: { scale: true, splitLine: { lineStyle: { color: '#334155', type: 'dashed' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      dataZoom: [{ type: 'inside', start: 0, end: 100 }, { type: 'slider', bottom: 6, height: 24, borderColor: '#334155', fillerColor: 'rgba(59,130,246,0.1)', handleStyle: { color: '#3b82f6' }, textStyle: { color: '#94a3b8', fontSize: 10 } }],
      series: [
        { name: '1年期 LPR', type: 'line', data: allPeriods.map(p => lpr1yMap.get(p) ?? null), smooth: false, lineStyle: { color: '#3b82f6', width: 2 }, itemStyle: { color: '#3b82f6' }, symbol: 'none' },
        { name: '5年期 LPR', type: 'line', data: allPeriods.map(p => lpr5yMap.get(p) ?? null), smooth: false, lineStyle: { color: '#f59e0b', width: 2 }, itemStyle: { color: '#f59e0b' }, symbol: 'none' },
      ],
    })
  }
}

/** 双序列折线（可选右轴），用于 M1/M2 与国债 / 两融 */
function makeDualOption(
  dates: string[],
  series: { name: string; values: (number | null)[]; color: string; rightAxis?: boolean }[],
) {
  const hasRight = series.some(s => s.rightAxis)
  const axis = { scale: true, splitLine: { lineStyle: { color: '#334155', type: 'dashed' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } }
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
    legend: { data: series.map(s => s.name), textStyle: { color: '#94a3b8' }, top: 4 },
    grid: { left: 60, right: hasRight ? 70 : 20, top: 40, bottom: 64 },
    xAxis: { type: 'category', data: dates, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    yAxis: hasRight ? [axis, { ...axis, splitLine: { show: false } }] : axis,
    dataZoom: [{ type: 'inside', start: 0, end: 100 }, { type: 'slider', bottom: 6, height: 24, borderColor: '#334155', fillerColor: 'rgba(59,130,246,0.1)', handleStyle: { color: '#3b82f6' }, textStyle: { color: '#94a3b8', fontSize: 10 } }],
    series: series.map(s => ({
      name: s.name, type: 'line', data: s.values, smooth: false, symbol: 'none',
      lineStyle: { color: s.color, width: 2 }, itemStyle: { color: s.color }, yAxisIndex: s.rightAxis ? 1 : 0,
    })),
  }
}

async function initExtraCharts() {
  if (moneyChartEl.value && (m1.value.length || m2.value.length)) {
    const periods = [...new Set([...m1.value.map(v => v.period), ...m2.value.map(v => v.period)])].sort()
    const m1Map = new Map(m1.value.map(v => [v.period, v.value]))
    const m2Map = new Map(m2.value.map(v => [v.period, v.value]))
    await moneyChart.render(makeDualOption(periods, [
      { name: 'M1 同比', values: periods.map(p => m1Map.get(p) ?? null), color: '#3b82f6' },
      { name: 'M2 同比', values: periods.map(p => m2Map.get(p) ?? null), color: '#f59e0b' },
    ]))
  }
  if (marketChartEl.value && (cgb10y.value.length || margin.value.length)) {
    const days = [...new Set([...cgb10y.value.map(v => v.trade_date), ...margin.value.map(v => v.trade_date)])].sort()
    const bondMap = new Map(cgb10y.value.map(v => [v.trade_date, v.value]))
    const marginMap = new Map(margin.value.map(v => [v.trade_date, v.value]))
    await marketChart.render(makeDualOption(days, [
      { name: '10Y 国债收益率（%）', values: days.map(d => bondMap.get(d) ?? null), color: '#22c55e' },
      { name: '融资余额（亿元）', values: days.map(d => marginMap.get(d) ?? null), color: '#a855f7', rightAxis: true },
    ]))
  }
}

watch([m1, m2, cgb10y, margin, moneyChartEl, marketChartEl], () => {
  if (!loading.value) initExtraCharts()
}, { flush: 'post' })

watch([cpi, pmi, lpr1y, lpr5y, cpiChartEl, pmiChartEl, lprChartEl], () => {
  if (!loading.value) initCharts()
}, { flush: 'post' })

onMounted(loadData)

</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 24px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }
.load-error {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px; border-radius: var(--radius-sm);
  background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); color: var(--danger); font-size: 13px;
}
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
