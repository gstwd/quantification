<template>
  <div class="result">
    <IssueList :issues="result.issues" />

    <div class="metric-grid">
      <div class="metric">
        <span class="m-label">翻转次数 / 年</span>
        <span class="m-value">{{ fmtNum(m.flips_per_year, 1) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">持仓时间占比</span>
        <span class="m-value">{{ fmtPct(m.on_ratio) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">IC：信号 vs 未来 20 日</span>
        <span class="m-value" :class="signClass(m.ic_fwd20)">{{ fmtNum(m.ic_fwd20) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">IC：信号 vs 过去 20 日（滞后度）</span>
        <span class="m-value">{{ fmtNum(m.ic_past20) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">与现有择时分相关</span>
        <span class="m-value">{{ fmtNum(m.corr_existing, 2) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">与现有择时同向占比</span>
        <span class="m-value">{{ fmtPct(m.agree_existing) }}</span>
      </div>
    </div>

    <table class="perf-table">
      <thead>
        <tr>
          <th></th>
          <th>区间收益</th>
          <th>年化</th>
          <th>最大回撤</th>
          <th>夏普</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td>择时持仓</td>
          <td :class="signClass(m.strategy.total)">{{ fmtPct(m.strategy.total) }}</td>
          <td :class="signClass(m.strategy.annualized)">{{ fmtPct(m.strategy.annualized) }}</td>
          <td>{{ fmtPct(m.strategy.max_drawdown) }}</td>
          <td>{{ fmtNum(m.strategy.sharpe, 2) }}</td>
        </tr>
        <tr>
          <td>买入持有 {{ result.index_name || result.index_code }}</td>
          <td :class="signClass(m.benchmark.total)">{{ fmtPct(m.benchmark.total) }}</td>
          <td :class="signClass(m.benchmark.annualized)">{{ fmtPct(m.benchmark.annualized) }}</td>
          <td>{{ fmtPct(m.benchmark.max_drawdown) }}</td>
          <td>{{ fmtNum(m.benchmark.sharpe, 2) }}</td>
        </tr>
      </tbody>
    </table>

    <div ref="chartEl" class="chart" :style="{ height: `${chartHeight}px` }"></div>

    <div class="sub-grid">
      <div>
        <div class="sub-title">分年表现（下跌年份重点看 2018、2022）</div>
        <table class="perf-table">
          <thead>
            <tr>
              <th>年份</th>
              <th>择时</th>
              <th>买入持有</th>
              <th>差值</th>
              <th>持仓占比</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in result.yearly" :key="row.year">
              <td>{{ row.year }}</td>
              <td :class="signClass(row.strategy_return)">{{ fmtPct(row.strategy_return) }}</td>
              <td :class="signClass(row.benchmark_return)">{{ fmtPct(row.benchmark_return) }}</td>
              <td :class="signClass(diff(row.strategy_return, row.benchmark_return))">
                {{ fmtPct(diff(row.strategy_return, row.benchmark_return)) }}
              </td>
              <td>{{ fmtPct(row.on_ratio, 0) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div>
        <div class="sub-title">数据覆盖（请求区间）</div>
        <table class="perf-table">
          <tbody>
            <tr>
              <td>指数</td>
              <td>{{ result.coverage.index_name }} {{ result.coverage.index_code }}</td>
            </tr>
            <tr>
              <td>区间</td>
              <td>{{ result.coverage.first_date ?? '—' }} ~ {{ result.coverage.last_date ?? '—' }}</td>
            </tr>
            <tr>
              <td>交易日</td>
              <td>{{ result.coverage.rows }}</td>
            </tr>
            <tr>
              <td>收盘 / OHL</td>
              <td>{{ fmtPct(result.coverage.close) }} / {{ fmtPct(result.coverage.ohl) }}</td>
            </tr>
            <tr>
              <td>成交量 / 成交额</td>
              <td>{{ fmtPct(result.coverage.volume) }} / {{ fmtPct(result.coverage.amount) }}</td>
            </tr>
            <tr>
              <td>参数</td>
              <td class="mono">{{ paramText }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <IssueList :issues="[]" :notes="result.notes" />
  </div>
</template>

<script setup lang="ts">
/**
 * 择时 / 市场级信号结果视图：评估指标、分年表现、覆盖信息，以及
 * “价格 + 持仓区间 / 信号值 / 中间分量 / 净值” 四联动图（共享缩放）。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { ECharts } from 'echarts'

import type { SignalLabTimingResponse } from '../../types/api'
import IssueList from './IssueList.vue'
import { CHART_COLORS, fmtNum, fmtPct, signClass } from './format'

const props = defineProps<{ result: SignalLabTimingResponse }>()

const chartEl = ref<HTMLElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

const m = computed(() => props.result.metrics)
const priceComponents = computed(() => props.result.components.filter((c) => c.axis === 'price'))
const signalComponents = computed(() => props.result.components.filter((c) => c.axis === 'signal'))
const auxComponents = computed(() => props.result.components.filter((c) => c.axis === 'aux'))
const panelCount = computed(() => (auxComponents.value.length ? 4 : 3))
const chartHeight = computed(() => 90 + panelCount.value * 190)
const paramText = computed(() =>
  Object.entries(props.result.params)
    .map(([k, v]) => `${k}=${v}`)
    .join('  '),
)

/** 两数之差（任一为空时返回 null） */
function diff(a: number | null, b: number | null): number | null {
  return a === null || b === null ? null : a - b
}

/** 渲染联动图 */
async function render(): Promise<void> {
  if (!chartEl.value) return
  const echarts = await import('echarts')
  chart?.dispose()
  chart = echarts.init(chartEl.value, undefined, { renderer: 'canvas' })
  const r = props.result
  const panels = panelCount.value
  const top0 = 36
  const panelH = 150
  const gap = 40
  const grids = Array.from({ length: panels }, (_, i) => ({
    left: 70,
    right: 60,
    top: top0 + i * (panelH + gap),
    height: panelH,
  }))
  const xAxes = grids.map((_, i) => ({
    type: 'category' as const,
    gridIndex: i,
    data: r.dates,
    boundaryGap: false,
    axisLabel: { show: i === panels - 1, color: CHART_COLORS.axis, fontSize: 11, hideOverlap: true },
    axisLine: { lineStyle: { color: CHART_COLORS.grid } },
    axisTick: { show: false },
  }))
  const valueAxis = (gridIndex: number, name: string, extra: Record<string, unknown> = {}) => ({
    type: 'value' as const,
    gridIndex,
    name,
    nameTextStyle: { color: CHART_COLORS.axis, fontSize: 11, align: 'left' as const },
    scale: true,
    axisLabel: { color: CHART_COLORS.axis, fontSize: 11 },
    splitLine: { lineStyle: { color: CHART_COLORS.grid, type: 'dashed' as const } },
    ...extra,
  })
  const yAxes: Array<Record<string, unknown>> = [
    valueAxis(0, '收盘'),
    {
      type: 'value',
      gridIndex: 0,
      min: 0,
      max: 1,
      show: false,
    },
    valueAxis(1, '信号值'),
  ]
  let navGrid = 2
  if (auxComponents.value.length) {
    yAxes.push(valueAxis(2, '中间分量'))
    navGrid = 3
  }
  yAxes.push(valueAxis(navGrid, '净值'))
  const navAxisIndex = yAxes.length - 1
  const auxAxisIndex = auxComponents.value.length ? 3 : -1

  const series: Array<Record<string, unknown>> = [
    {
      name: '持仓区间',
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 1,
      step: 'end',
      showSymbol: false,
      lineStyle: { width: 0 },
      areaStyle: { color: CHART_COLORS.holding },
      data: r.on.map((v) => (v === null ? null : v)),
      tooltip: { show: false },
    },
    {
      name: `收盘 ${r.index_name || r.index_code}`,
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      lineStyle: { width: 1.4, color: CHART_COLORS.price },
      itemStyle: { color: CHART_COLORS.price },
      data: r.close,
    },
    ...priceComponents.value.map((c, i) => ({
      name: c.label,
      type: 'line',
      xAxisIndex: 0,
      yAxisIndex: 0,
      showSymbol: false,
      lineStyle: { width: 1.2, color: CHART_COLORS.palette[i % 5] },
      itemStyle: { color: CHART_COLORS.palette[i % 5] },
      data: c.values,
    })),
    {
      name: r.signal.label,
      type: 'line',
      xAxisIndex: 1,
      yAxisIndex: 2,
      showSymbol: false,
      lineStyle: { width: 1.3, color: CHART_COLORS.signal },
      itemStyle: { color: CHART_COLORS.signal },
      data: r.value,
      markLine: {
        symbol: 'none',
        silent: true,
        label: { color: CHART_COLORS.reference, fontSize: 10, formatter: '{b}' },
        lineStyle: { color: CHART_COLORS.reference, type: 'dashed', width: 1 },
        data: r.signal.reference_lines.map((line) => ({ name: line.label, yAxis: line.value })),
      },
    },
    ...signalComponents.value.map((c, i) => ({
      name: c.label,
      type: 'line',
      xAxisIndex: 1,
      yAxisIndex: 2,
      showSymbol: false,
      step: c.key === 'threshold' ? 'end' : undefined,
      lineStyle: { width: 1, color: CHART_COLORS.palette[(i + 1) % 5] },
      itemStyle: { color: CHART_COLORS.palette[(i + 1) % 5] },
      data: c.values,
    })),
    ...auxComponents.value.map((c, i) => ({
      name: c.label,
      type: 'line',
      xAxisIndex: 2,
      yAxisIndex: auxAxisIndex,
      showSymbol: false,
      lineStyle: { width: 1.1, color: CHART_COLORS.palette[(i + 2) % 5] },
      itemStyle: { color: CHART_COLORS.palette[(i + 2) % 5] },
      data: c.values,
    })),
    {
      name: '择时净值',
      type: 'line',
      xAxisIndex: navGrid,
      yAxisIndex: navAxisIndex,
      showSymbol: false,
      lineStyle: { width: 1.5, color: CHART_COLORS.strategy },
      itemStyle: { color: CHART_COLORS.strategy },
      data: r.strategy_nav,
    },
    {
      name: '买入持有净值',
      type: 'line',
      xAxisIndex: navGrid,
      yAxisIndex: navAxisIndex,
      showSymbol: false,
      lineStyle: { width: 1.2, color: CHART_COLORS.benchmark, type: 'dashed' },
      itemStyle: { color: CHART_COLORS.benchmark },
      data: r.benchmark_nav,
    },
  ]
  const allX = grids.map((_, i) => i)
  chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    legend: { type: 'scroll', top: 0, textStyle: { color: CHART_COLORS.axis, fontSize: 11 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', link: [{ xAxisIndex: 'all' }] },
      valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(4) : '—'),
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    dataZoom: [
      { type: 'inside', xAxisIndex: allX },
      { type: 'slider', xAxisIndex: allX, height: 20, bottom: 6 },
    ],
    series,
  })
}

watch(
  () => props.result,
  async () => {
    await nextTick()
    await render()
  },
)

onMounted(async () => {
  await render()
  if (chartEl.value) {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(chartEl.value)
  }
})

onUnmounted(() => {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.result {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}
.metric {
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.m-label {
  color: var(--text-muted);
  font-size: 11px;
}
.m-value {
  font-size: 18px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.chart {
  width: 100%;
}
.sub-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 16px;
}
.sub-title {
  color: var(--text-muted);
  font-size: 12px;
  margin-bottom: 6px;
}
.perf-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.perf-table th,
.perf-table td {
  text-align: right;
  padding: 5px 8px;
  border-bottom: 1px solid var(--border);
}
.perf-table th:first-child,
.perf-table td:first-child {
  text-align: left;
  color: var(--text-muted);
}
.perf-table th {
  color: #64748b;
  font-weight: 500;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
}
.pos {
  color: var(--success);
}
.neg {
  color: var(--danger);
}
</style>
