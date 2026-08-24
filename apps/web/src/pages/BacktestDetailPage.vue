<template>
  <div class="page">
    <div class="page-header">
      <RouterLink to="/backtests" class="back-link">← 回测中心</RouterLink>
      <div class="header-row">
        <div>
          <h1 class="page-title">{{ store.current?.strategy_id ?? '回测详情' }}</h1>
          <p class="subtitle">{{ store.current?.start_date }} ~ {{ store.current?.end_date }}</p>
        </div>
        <span v-if="store.current" class="status-badge" :class="'status-' + store.current.status">
          {{ statusLabel(store.current.status) }}
        </span>
      </div>
    </div>

    <div v-if="store.loading" class="loading">加载中...</div>

    <template v-else-if="store.current">
      <!-- 轮询提示（含执行进度） -->
      <div v-if="polling" class="polling-banner">
        <span>回测执行中，自动刷新结果...</span>
        <span v-if="(store.current?.progress ?? 0) > 0" class="polling-progress">
          <span class="polling-bar-bg">
            <span class="polling-bar-fill" :style="{ width: (store.current?.progress ?? 0) + '%' }"></span>
          </span>
          <span class="polling-pct">{{ store.current?.progress ?? 0 }}%</span>
        </span>
      </div>

      <!-- 汇总指标卡片 -->
      <div v-if="store.current.metrics" class="metrics-section">
        <div class="section-label">核心绩效</div>
        <div class="metrics-grid">
          <div class="metric-card">
            <div class="metric-label">累计收益 <HelpTip :text="metricHelp('cumulative_return_pct')" /></div>
            <div class="metric-value" :class="store.current.metrics.cumulative_return_pct >= 0 ? 'success' : 'danger'">
              {{ formatPct(store.current.metrics.cumulative_return_pct) }}
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-label">年化收益 <HelpTip :text="metricHelp('annualized_return_pct')" /></div>
            <div class="metric-value" :class="store.current.metrics.annualized_return_pct >= 0 ? 'success' : 'danger'">
              {{ formatPct(store.current.metrics.annualized_return_pct) }}
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-label">最大回撤 <HelpTip :text="metricHelp('max_drawdown_pct')" /></div>
            <div class="metric-value danger">{{ formatPct(store.current.metrics.max_drawdown_pct) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">回撤持续 <HelpTip :text="metricHelp('max_drawdown_days')" /></div>
            <div class="metric-value">{{ store.current.metrics.max_drawdown_days }} 天</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">夏普比率 <HelpTip :text="metricHelp('sharpe_ratio')" /></div>
            <div class="metric-value">{{ store.current.metrics.sharpe_ratio.toFixed(2) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">卡玛比率 <HelpTip :text="metricHelp('calmar_ratio')" /></div>
            <div class="metric-value">{{ store.current.metrics.calmar_ratio.toFixed(2) }}</div>
          </div>
        </div>

        <div class="section-label" style="margin-top: 16px;">风险与胜率</div>
        <div class="metrics-grid">
          <div class="metric-card">
            <div class="metric-label">索提诺比率 <HelpTip :text="metricHelp('sortino_ratio')" /></div>
            <div class="metric-value">{{ store.current.metrics.sortino_ratio.toFixed(2) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">胜率 <HelpTip :text="metricHelp('win_rate_pct')" /></div>
            <div class="metric-value">{{ store.current.metrics.win_rate_pct.toFixed(1) }}%</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">信号准确率 <HelpTip :text="metricHelp('signal_accuracy_pct')" /></div>
            <div class="metric-value">{{ store.current.metrics.signal_accuracy_pct.toFixed(1) }}%</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">盈亏比 <HelpTip :text="metricHelp('profit_loss_ratio')" /></div>
            <div class="metric-value">{{ store.current.metrics.profit_loss_ratio !== null ? store.current.metrics.profit_loss_ratio.toFixed(2) : '-' }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">交易日 <HelpTip :text="metricHelp('total_trading_days')" /></div>
            <div class="metric-value">{{ store.current.metrics.total_trading_days }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">持仓日 <HelpTip :text="metricHelp('active_days')" /></div>
            <div class="metric-value">{{ store.current.metrics.active_days }}</div>
          </div>
        </div>

        <!-- 基准对比（有基准数据时显示） -->
        <template v-if="store.current.metrics.benchmark_return_pct !== null && store.current.metrics.benchmark_return_pct !== undefined">
          <div class="section-label" style="margin-top: 16px;">基准对比</div>
          <div class="metrics-grid">
            <div class="metric-card">
              <div class="metric-label">基准收益 <HelpTip :text="metricHelp('benchmark_return_pct')" /></div>
              <div class="metric-value" :class="store.current.metrics.benchmark_return_pct >= 0 ? 'success' : 'danger'">
                {{ formatPct(store.current.metrics.benchmark_return_pct) }}
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-label">超额收益 <HelpTip :text="metricHelp('excess_return_pct')" /></div>
              <div class="metric-value" :class="(store.current.metrics.excess_return_pct ?? 0) >= 0 ? 'success' : 'danger'">
                {{ formatPct(store.current.metrics.excess_return_pct ?? 0) }}
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-label">Alpha <HelpTip :text="metricHelp('alpha')" /></div>
              <div class="metric-value" :class="(store.current.metrics.alpha ?? 0) >= 0 ? 'success' : 'danger'">
                {{ store.current.metrics.alpha !== null ? formatPct(store.current.metrics.alpha) : '-' }}
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-label">Beta <HelpTip :text="metricHelp('beta')" /></div>
              <div class="metric-value">{{ store.current.metrics.beta !== null ? store.current.metrics.beta.toFixed(2) : '-' }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">信息比率 <HelpTip :text="metricHelp('information_ratio')" /></div>
              <div class="metric-value">{{ store.current.metrics.information_ratio !== null ? store.current.metrics.information_ratio.toFixed(2) : '-' }}</div>
            </div>
          </div>
        </template>
      </div>

      <!-- 权益曲线 -->
      <div class="chart-card">
        <div class="chart-title">权益曲线（累计收益率 %）</div>
        <div ref="equityChartEl" class="chart-container"></div>
      </div>

      <!-- 回撤曲线 -->
      <div class="chart-card">
        <div class="chart-title">回撤曲线（%）</div>
        <div ref="drawdownChartEl" class="chart-container"></div>
      </div>

      <!-- 仓位变化 -->
      <div class="chart-card">
        <div class="chart-title">仓位变化</div>
        <div ref="positionsChartEl" class="chart-container"></div>
      </div>

      <!-- 择时状态 + 代理指数趋势（合并图表） -->
      <div v-if="regimeTimeline.length > 0" class="chart-card">
        <div class="chart-title">择时状态 &amp; 代理指数趋势</div>
        <div v-if="hasBenchmark" ref="timingChartEl" class="chart-container"></div>
        <!-- 无基准数据时降级为原始 regime 时间线 -->
        <div v-else class="regime-timeline">
          <span
            v-for="(item, i) in regimeTimeline"
            :key="i"
            :class="['regime-block', `regime-${item.regime}`]"
            :title="`${item.date}: ${item.regime} (${(item.exposure * 100).toFixed(0)}%)`"
          >
            {{ item.date.slice(5) }}
          </span>
        </div>
      </div>

    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import type { BenchmarkIndex } from '../types/api'
import { fetchBenchmarkIndexes } from '../api/market_data'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'
import { useBacktestStore } from '../stores/backtests'
import { usePolling } from '../composables/usePolling'

/** 获取指标描述的快捷方法 */
function metricHelp(key: string): string {
  return getIndicator('metrics', key)?.description ?? ''
}

const props = defineProps<{ backtestId: string }>()
const store = useBacktestStore()

const equityChartEl = ref<HTMLElement | null>(null)
const drawdownChartEl = ref<HTMLElement | null>(null)
const positionsChartEl = ref<HTMLElement | null>(null)
const timingChartEl = ref<HTMLElement | null>(null)
/** 轮询回测执行状态，组件卸载时自动停止 */
const { polling, start: startPolling } = usePolling({
  fetcher: () => store.refreshOne(props.backtestId),
  isDone: (detail) => detail.status !== 'pending' && detail.status !== 'running',
})
/** 指数代码 → 指数名称 映射表 */
const indexNameMap = ref<Record<string, string>>({})

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let equityChart: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let drawdownChart: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let positionsChart: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let timingChart: any = null

function statusLabel(status: string): string {
  const map: Record<string, string> = { pending: '待执行', running: '执行中', success: '成功', failed: '失败' }
  return map[status] ?? status
}

function formatPct(v: number): string {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

/** 是否有基准收益数据 */
const hasBenchmark = computed(() => {
  return store.dailyResults.some((r) => r.benchmark_return !== null && r.benchmark_return !== undefined)
})

/** 基准累计收益率序列（从日收益率累加，用于权益曲线对比） */
const benchmarkCumReturns = computed(() => {
  const result: number[] = []
  let cum = 0
  for (const r of store.dailyResults) {
    if (r.benchmark_return !== null && r.benchmark_return !== undefined) {
      cum = (1 + cum / 100) * (1 + r.benchmark_return / 100) - 1
      cum = cum * 100
    }
    result.push(Number(cum.toFixed(4)))
  }
  return result
})

/** 代理指数归一化收盘价序列（从日收益率重建，基准=100） */
const benchmarkPrices = computed(() => {
  const result: number[] = []
  let price = 100
  for (const r of store.dailyResults) {
    if (r.benchmark_return !== null && r.benchmark_return !== undefined) {
      price = price * (1 + r.benchmark_return / 100)
    }
    result.push(Number(price.toFixed(2)))
  }
  return result
})

/** 择时状态时间线数据 */
const regimeTimeline = computed(() => {
  return store.dailyResults
    .filter((r) => r.timing_regime)
    .map((r) => ({
      date: String(r.trade_date),
      regime: r.timing_regime ?? 'neutral',
      exposure: r.total_exposure ?? 0,
    }))
})

/** 根据指数代码获取展示名称（名称优先，降级为代码） */
function getDisplayName(code: string): string {
  const name = indexNameMap.value[code]
  if (name) return name
  return code
}

async function initCharts() {
  if (store.dailyResults.length === 0) return
  const echarts = await import('echarts')

  const dates = store.dailyResults.map((r) => r.trade_date)
  const cumReturns = store.dailyResults.map((r) => r.cumulative_return)
  const drawdowns = store.dailyResults.map((r) => r.drawdown)
  const benchmarkCum = benchmarkCumReturns.value
  const benchmarkPricesData = benchmarkPrices.value
  const showBench = hasBenchmark.value

  // 权益曲线（含基准对比叠加）
  if (equityChartEl.value) {
    equityChart?.dispose()
    equityChart = echarts.init(equityChartEl.value)
    const series: Record<string, unknown>[] = [{
      name: '策略',
      type: 'line',
      data: cumReturns,
      smooth: true,
      symbol: 'none',
      lineStyle: { color: '#3b82f6', width: 2 },
      areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.2)' }, { offset: 1, color: 'rgba(59,130,246,0.02)' }] } },
    }]
    if (showBench) {
      series.push({
        name: '基准',
        type: 'line',
        data: benchmarkCum,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#f59e0b', width: 1.5, type: 'dashed' },
      })
    }
    equityChart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        formatter: (params: { seriesName: string; value: number }[]) => {
          let tip = ''
          for (const p of params) {
            tip += `${p.seriesName}: ${p.value.toFixed(2)}%<br/>`
          }
          return tip
        },
      },
      legend: showBench ? { data: ['策略', '基准'], textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0 } : undefined,
      grid: { left: 60, right: 20, top: showBench ? 30 : 20, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#94a3b8', fontSize: 11 }, axisLine: { lineStyle: { color: '#334155' } } },
      yAxis: { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 11, formatter: (v: number) => v.toFixed(1) + '%' }, splitLine: { lineStyle: { color: '#1e293b' } } },
      series,
    })
  }

  // 回撤曲线
  if (drawdownChartEl.value) {
    drawdownChart?.dispose()
    drawdownChart = echarts.init(drawdownChartEl.value)
    drawdownChart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', formatter: (p: { name: string; value: number }[]) => `${p[0].name}<br/>回撤: ${p[0].value.toFixed(2)}%` },
      grid: { left: 60, right: 20, top: 20, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#94a3b8', fontSize: 11 }, axisLine: { lineStyle: { color: '#334155' } } },
      yAxis: { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 11, formatter: (v: number) => v.toFixed(1) + '%' }, splitLine: { lineStyle: { color: '#1e293b' } } },
      series: [{
        type: 'line',
        data: drawdowns,
        smooth: false,
        symbol: 'none',
        lineStyle: { color: '#ef4444', width: 1.5 },
        areaStyle: { color: 'rgba(239,68,68,0.15)' },
      }],
    })
  }

  // 仓位变化堆叠面积图（使用指数名称展示）
  if (positionsChartEl.value) {
    positionsChart?.dispose()
    positionsChart = echarts.init(positionsChartEl.value)

    // 收集所有持仓的指数代码
    const allCodes = new Set<string>()
    for (const r of store.dailyResults) {
      if (r.positions) {
        for (const code of Object.keys(r.positions)) allCodes.add(code)
      }
    }
    const codeList = Array.from(allCodes).sort()

    // 构建 code → displayName 映射用于图例和 tooltip
    const codeDisplayNames = codeList.map((code) => getDisplayName(code))
    const legendData = [...codeDisplayNames, '现金']

    // 构建每个指数的权重时间序列
    const colorPalette = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#a855f7', '#06b6d4', '#f97316', '#ec4899']
    const series = codeList.map((code, idx) => ({
      name: codeDisplayNames[idx],
      type: 'line',
      stack: 'positions',
      areaStyle: {},
      symbol: 'none',
      lineStyle: { width: 0 },
      itemStyle: { color: colorPalette[idx % colorPalette.length] },
      data: store.dailyResults.map((r) => r.positions ? ((r.positions[code] ?? 0) * 100) : 0),
    }))

    // 现金比例
    series.push({
      name: '现金',
      type: 'line',
      stack: 'positions',
      areaStyle: {},
      symbol: 'none',
      lineStyle: { width: 0 },
      itemStyle: { color: '#475569' },
      data: store.dailyResults.map((r) => r.cash_ratio ? (r.cash_ratio * 100) : (100 - (r.total_exposure ?? 0) * 100)),
    })

    positionsChart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        formatter: (params: { name: string; seriesName: string; value: number }[]) => {
          let tip = params[0].name + '<br/>'
          for (const p of params) {
            if (p.value > 0.01) tip += `${p.seriesName}: ${p.value.toFixed(1)}%<br/>`
          }
          return tip
        },
      },
      legend: { data: legendData, textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0, type: 'scroll' },
      grid: { left: 60, right: 20, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#94a3b8', fontSize: 10, interval: Math.floor(dates.length / 8) }, axisLine: { lineStyle: { color: '#334155' } } },
      yAxis: { type: 'value', max: 100, axisLabel: { color: '#94a3b8', fontSize: 11, formatter: (v: number) => v + '%' }, splitLine: { lineStyle: { color: '#1e293b' } } },
      series,
    })
  }

  // 择时状态 + 代理指数趋势（合并图表）
  initTimingChart(echarts, dates, benchmarkPricesData, showBench)
}

/** 构建择时状态 + 代理指数趋势合并图表（每日归一化收盘价 + regime 背景着色） */
function initTimingChart(
  echarts: any, // eslint-disable-line @typescript-eslint/no-explicit-any
  dates: string[],
  prices: number[],
  showBench: boolean,
) {
  if (!timingChartEl.value || regimeTimeline.value.length === 0) return
  if (!showBench) return // 无基准数据时使用降级方案（regime 时间线块）

  timingChart?.dispose()
  timingChart = echarts.init(timingChartEl.value)

  // 构建 regime → 颜色映射
  const REGIME_COLORS: Record<string, string> = {
    offensive: 'rgba(34,197,94,0.12)',
    neutral: 'rgba(245,158,11,0.12)',
    defensive: 'rgba(239,68,68,0.12)',
  }

  // 将连续的同一 regime 日期合并为区间，用于 markArea 背景着色
  // ECharts markArea data 格式：[[{name, itemStyle, xAxis, yAxis}, {xAxis, yAxis}], ...]
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markAreas: any[] = []
  const timeline = regimeTimeline.value
  if (timeline.length > 0) {
    let blockStart = 0
    let currentRegime = timeline[0].regime
    for (let i = 1; i < timeline.length; i++) {
      if (timeline[i].regime !== currentRegime) {
        markAreas.push([
          { name: currentRegime, itemStyle: { color: REGIME_COLORS[currentRegime] ?? 'rgba(100,116,139,0.1)' }, xAxis: timeline[blockStart].date, yAxis: 'min' },
          { xAxis: timeline[i - 1].date, yAxis: 'max' },
        ])
        currentRegime = timeline[i].regime
        blockStart = i
      }
    }
    // 最后一个区间
    markAreas.push([
      { name: currentRegime, itemStyle: { color: REGIME_COLORS[currentRegime] ?? 'rgba(100,116,139,0.1)' }, xAxis: timeline[blockStart].date, yAxis: 'min' },
      { xAxis: timeline[timeline.length - 1].date, yAxis: 'max' },
    ])
  }

  timingChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: (params: { name: string; seriesName: string; value: number; axisValue: string }[]) => {
        const date = params[0]?.axisValue ?? ''
        let tip = `<b>${date}</b><br/>`
        // 代理指数收盘价
        const benchP = params.find((p) => p.seriesName === '代理指数')
        if (benchP) tip += `代理指数: ${benchP.value.toFixed(2)}<br/>`
        // 择时状态
        const regimeItem = regimeTimeline.value.find((r) => r.date === date)
        if (regimeItem) {
          const regimeLabels: Record<string, string> = { offensive: '进攻', neutral: '中性', defensive: '防守' }
          tip += `择时状态: ${regimeLabels[regimeItem.regime] ?? regimeItem.regime}<br/>`
          tip += `目标仓位: ${(regimeItem.exposure * 100).toFixed(0)}%`
        }
        return tip
      },
    },
    grid: { left: 80, right: 20, top: 20, bottom: 40 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      name: '归一化价格',
      nameLocation: 'end',
      nameRotate: 0,
      nameGap: 24,
      nameTextStyle: { color: '#94a3b8', fontSize: 10, align: 'left' },
      axisLabel: { color: '#94a3b8', fontSize: 11, formatter: (v: number) => v.toFixed(1) },
      splitLine: { lineStyle: { color: '#1e293b' } },
      scale: true,
    },
    series: [
      {
        name: '代理指数',
        type: 'line',
        data: prices,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#f59e0b', width: 2 },
        markArea: {
          silent: true,
          data: markAreas,
          label: { show: false },
        },
      },
    ],
  })
}

/** 加载基准指数列表，构建 index_code → index_name 映射 */
async function loadIndexNames() {
  try {
    const indexes: BenchmarkIndex[] = await fetchBenchmarkIndexes()
    const map: Record<string, string> = {}
    for (const idx of indexes) {
      map[idx.index_code] = idx.index_name
    }
    indexNameMap.value = map
  } catch {
    // 获取指数名称失败时降级显示代码
  }
}

watch(() => store.dailyResults, () => { if (store.dailyResults.length > 0) initCharts() })

onMounted(async () => {
  await Promise.all([
    store.loadOne(props.backtestId),
    loadIndexNames(),
  ])
  if (store.current?.status === 'pending' || store.current?.status === 'running') {
    await startPolling()
  }
  if (store.current?.status === 'success') {
    await Promise.all([
      store.loadDailyResults(props.backtestId),
      store.loadIndexResults(props.backtestId),
    ])
    await initCharts()
  }
})

onUnmounted(() => {
  equityChart?.dispose()
  drawdownChart?.dispose()
  positionsChart?.dispose()
  timingChart?.dispose()
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-header { display: flex; flex-direction: column; gap: 8px; }
.back-link { font-size: 13px; color: var(--text-muted); text-decoration: none; }
.back-link:hover { color: var(--accent); }
.header-row { display: flex; align-items: flex-start; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 2px; }

.loading { padding: 60px; text-align: center; color: var(--text-muted); }

.polling-banner {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: rgba(59,130,246,0.1);
  border: 1px solid rgba(59,130,246,0.3);
  border-radius: var(--radius-sm);
  color: #60a5fa;
  font-size: 13px;
}
.polling-progress { display: inline-flex; align-items: center; gap: 6px; }
.polling-bar-bg { display: inline-block; width: 100px; height: 5px; background: rgba(59,130,246,0.15); border-radius: 3px; overflow: hidden; }
.polling-bar-fill { display: block; height: 100%; background: #60a5fa; border-radius: 3px; transition: width 0.4s; }
.polling-pct { font-size: 12px; }

.section-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 8px;
}
.metrics-section {
  display: flex;
  flex-direction: column;
}
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
}
@media (max-width: 1200px) {
  .metrics-grid { grid-template-columns: repeat(3, 1fr); }
}
.metric-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.metric-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.metric-value { font-size: 20px; font-weight: 700; }
.success { color: var(--success); }
.danger { color: var(--danger); }

.chart-card, .table-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}
.chart-title, .table-title { font-size: 13px; font-weight: 600; color: var(--text-muted); margin-bottom: 12px; }
.chart-container { width: 100%; height: 260px; }
.chart-short { height: 180px; }

.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  text-align: left;
  padding: 8px 14px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
}
.data-table td { padding: 10px 14px; border-bottom: 1px solid rgba(51,65,85,0.5); font-size: 13px; }
.data-table tr:last-child td { border-bottom: none; }
.mono { font-family: monospace; font-size: 12px; }

.status-badge {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}
.status-pending { background: rgba(148,163,184,0.15); color: var(--text-muted); }
.status-running { background: rgba(59,130,246,0.15); color: #60a5fa; }
.status-success { background: rgba(34,197,94,0.15); color: var(--success); }
.status-failed { background: rgba(239,68,68,0.15); color: var(--danger); }

/* 择时状态时间线 */
.regime-timeline {
  display: flex;
  flex-wrap: wrap;
  gap: 2px;
  padding: 8px 0;
}
.regime-block {
  display: inline-block;
  padding: 2px 4px;
  border-radius: 3px;
  font-size: 9px;
  font-weight: 500;
  cursor: default;
  line-height: 1.4;
}
.regime-block.regime-offensive { background: rgba(34,197,94,0.25); color: #4ade80; }
.regime-block.regime-neutral { background: rgba(245,158,11,0.25); color: #f59e0b; }
.regime-block.regime-defensive { background: rgba(239,68,68,0.25); color: #f87171; }
</style>
