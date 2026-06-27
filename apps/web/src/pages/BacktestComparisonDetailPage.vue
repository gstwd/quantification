<template>
  <div class="page">
    <div class="page-header">
      <RouterLink to="/backtests" class="back-link">← 回测中心</RouterLink>
      <div class="header-row">
        <div>
          <h1 class="page-title">策略对比详情</h1>
          <p class="subtitle">
            <span class="strategy-tag a">{{ strategyAName }}</span>
            <span class="vs">vs</span>
            <span class="strategy-tag b">{{ strategyBName }}</span>
            &nbsp;|&nbsp; {{ store.currentComparison?.start_date }} ~ {{ store.currentComparison?.end_date }}
          </p>
        </div>
        <span v-if="store.currentComparison" class="status-badge" :class="'status-' + store.currentComparison.status">
          {{ compStatusLabel(store.currentComparison.status) }}
        </span>
      </div>
    </div>

    <div v-if="store.loading" class="loading">加载中...</div>

    <template v-else-if="store.currentComparison">
      <!-- 轮询提示 -->
      <div v-if="polling" class="polling-banner">
        <span>对比回测执行中，自动刷新结果...</span>
        <span v-if="(store.currentComparison?.progress ?? 0) > 0" class="polling-progress">
          <span class="polling-bar-bg">
            <span class="polling-bar-fill" :style="{ width: (store.currentComparison?.progress ?? 0) + '%' }"></span>
          </span>
          <span class="polling-pct">{{ store.currentComparison?.progress ?? 0 }}%</span>
        </span>
      </div>

      <!-- 综合评价面板 -->
      <div v-if="store.currentComparison.comparison_metrics" class="verdict-card">
        <div class="verdict-title">综合评价</div>
        <div class="verdict-text">{{ verdictText }}</div>
        <div class="verdict-count">
          优胜指标：<span class="a-win">{{ aWinCount }}</span> / {{ totalCompared }} vs
          <span class="b-win">{{ bWinCount }}</span> / {{ totalCompared }}
        </div>
      </div>

      <!-- 核心绩效对比表 -->
      <div v-if="store.currentComparison.comparison_metrics" class="section">
        <div class="section-label">核心绩效对比</div>
        <div class="comparison-table">
          <table>
            <thead>
              <tr>
                <th>指标</th>
                <th>策略 A</th>
                <th>差值 (A-B)</th>
                <th>策略 B</th>
                <th>优胜</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in coreMetrics" :key="row.label">
                <td class="metric-label-col">{{ row.label }}</td>
                <td :class="row.aClass(row.a)">{{ row.format(row.a) }}</td>
                <td :class="row.diffClass(row.diff)">{{ row.formatDiff(row.diff) }}</td>
                <td :class="row.bClass(row.b)">{{ row.format(row.b) }}</td>
                <td>
                  <span v-if="row.winner === 'a'" class="winner-tag a">A</span>
                  <span v-else-if="row.winner === 'b'" class="winner-tag b">B</span>
                  <span v-else class="winner-tag draw">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 权益曲线叠加图 -->
      <div class="chart-card">
        <div class="chart-title">权益曲线叠加（累计收益率 %）</div>
        <div ref="equityChartEl" class="chart-container"></div>
      </div>

      <!-- 回撤曲线叠加图 -->
      <div class="chart-card">
        <div class="chart-title">回撤曲线叠加（%）</div>
        <div ref="ddChartEl" class="chart-container"></div>
      </div>

      <!-- 超额收益图 -->
      <div class="chart-card">
        <div class="chart-title">策略 A 相对策略 B 的累计超额收益（%）</div>
        <div ref="excessChartEl" class="chart-container"></div>
      </div>

      <!-- 风险与胜率对比表 -->
      <div v-if="store.currentComparison.comparison_metrics" class="section">
        <div class="section-label">风险与胜率对比</div>
        <div class="comparison-table">
          <table>
            <thead>
              <tr>
                <th>指标</th>
                <th>策略 A</th>
                <th>差值 (A-B)</th>
                <th>策略 B</th>
                <th>优胜</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in riskMetrics" :key="row.label">
                <td class="metric-label-col">{{ row.label }}</td>
                <td :class="row.aClass(row.a)">{{ row.format(row.a) }}</td>
                <td :class="row.diffClass(row.diff)">{{ row.formatDiff(row.diff) }}</td>
                <td :class="row.bClass(row.b)">{{ row.format(row.b) }}</td>
                <td>
                  <span v-if="row.winner === 'a'" class="winner-tag a">A</span>
                  <span v-else-if="row.winner === 'b'" class="winner-tag b">B</span>
                  <span v-else class="winner-tag draw">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 基准对比表 -->
      <div v-if="hasBenchmark && store.currentComparison.comparison_metrics" class="section">
        <div class="section-label">基准对比</div>
        <div class="comparison-table">
          <table>
            <thead>
              <tr>
                <th>指标</th>
                <th>策略 A</th>
                <th>策略 B</th>
                <th>最佳</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in benchmarkMetrics" :key="row.label">
                <td class="metric-label-col">{{ row.label }}</td>
                <td :class="row.aClass(row.a)">{{ row.format(row.a) }}</td>
                <td :class="row.bClass(row.b)">{{ row.format(row.b) }}</td>
                <td>
                  <span v-if="row.winner === 'a'" class="winner-tag a">A</span>
                  <span v-else-if="row.winner === 'b'" class="winner-tag b">B</span>
                  <span v-else class="winner-tag draw">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { useBacktestStore } from '../stores/backtests'
import { useStrategyStore } from '../stores/strategies'

const props = defineProps<{ comparisonId: string }>()
const store = useBacktestStore()
const strategyStore = useStrategyStore()

const equityChartEl = ref<HTMLElement | null>(null)
const ddChartEl = ref<HTMLElement | null>(null)
const excessChartEl = ref<HTMLElement | null>(null)
const polling = ref(false)

let equityChartInst: any = null
let ddChartInst: any = null
let excessChartInst: any = null

// ── 策略名称 ──

const strategyAName = computed(() => {
  const s = strategyStore.items.find((s) => s.strategy_id === store.currentComparison?.strategy_a_id)
  return s?.display_name ?? store.currentComparison?.strategy_a_id ?? '策略 A'
})

const strategyBName = computed(() => {
  const s = strategyStore.items.find((s) => s.strategy_id === store.currentComparison?.strategy_b_id)
  return s?.display_name ?? store.currentComparison?.strategy_b_id ?? '策略 B'
})

// ── 状态标签 ──

function compStatusLabel(status: string): string {
  const map: Record<string, string> = {
    pending: '待执行', running: '执行中', success: '成功', failed: '失败', partial: '部分成功',
  }
  return map[status] ?? status
}

// ── 综合评价 ──

type Winner = 'a' | 'b' | 'draw' | 'neutral'

/** 可对比指标总数（排除中性指标如交易天数等） */
const totalCompared = computed(() => {
  let count = 0
  for (const row of allCoreRows.value) {
    if (row.winner !== 'neutral') count++
  }
  for (const row of riskRows.value) {
    if (row.winner !== 'neutral') count++
  }
  return count
})

const verdictText = computed(() => {
  const m = store.currentComparison?.comparison_metrics
  if (!m) return ''
  const aWins: string[] = []
  const bWins: string[] = []

  if (m.cumulative_return_diff_pct > 0.01) aWins.push('累计收益')
  else if (m.cumulative_return_diff_pct < -0.01) bWins.push('累计收益')

  // 回撤为负值（-10 > -20），正差值 = A 回撤更优
  if (m.max_drawdown_diff_pct > 0.5) aWins.push('最大回撤控制')
  else if (m.max_drawdown_diff_pct < -0.5) bWins.push('最大回撤控制')

  if (m.sharpe_diff > 0.01) aWins.push('夏普比率')
  else if (m.sharpe_diff < -0.01) bWins.push('夏普比率')

  if (m.calmar_diff > 0.01) aWins.push('卡玛比率')
  else if (m.calmar_diff < -0.01) bWins.push('卡玛比率')

  if (m.win_rate_diff_pct > 0.1) aWins.push('胜率')
  else if (m.win_rate_diff_pct < -0.1) bWins.push('胜率')

  const aPart = aWins.length > 0 ? `策略 A 在 ${aWins.join('、')} 方面优于策略 B` : ''
  const bPart = bWins.length > 0 ? `策略 B 在 ${bWins.join('、')} 方面更优` : ''
  if (aPart && bPart) return `${aPart}；${bPart}。`
  if (aPart) return `${aPart}。`
  if (bPart) return `${bPart}。`
  return '两个策略表现接近，无明显差异。'
})

const aWinCount = computed(() => {
  let count = 0
  for (const row of allCoreRows.value) {
    if (row.winner === 'a') count++
  }
  for (const row of riskRows.value) {
    if (row.winner === 'a') count++
  }
  return count
})

const bWinCount = computed(() => {
  let count = 0
  for (const row of allCoreRows.value) {
    if (row.winner === 'b') count++
  }
  for (const row of riskRows.value) {
    if (row.winner === 'b') count++
  }
  return count
})

// ── 对比表数据 ──

interface MetricRow {
  label: string
  a: number
  b: number
  diff: number
  format: (v: number) => string
  formatDiff: (v: number) => string
  aClass: (v: number) => string
  bClass: (v: number) => string
  diffClass: (v: number) => string
  winner: Winner
}

function pctFmt(v: number): string {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

function ratioFmt(v: number): string {
  return v.toFixed(2)
}

function daysFmt(v: number): string {
  return String(Math.round(v))
}

function pctColor(v: number): string {
  return v >= 0 ? 'success' : 'danger'
}

// 对于一般指标，diff>0 意味着 A 更好；回撤为负值（-10 > -20），diff>0 同样意味着 A 更好
function buildRow(
  label: string,
  a: number,
  b: number,
  fmt: (v: number) => string,
  diffFmt: (v: number) => string = pctFmt,
  invertDiff: boolean = false,
): MetricRow {
  const diff = a - b
  let winner: Winner = 'draw'
  const threshold = fmt === ratioFmt ? 0.001 : 0.01
  if (invertDiff) {
    // 更小=更好的指标（如费用、波动率等正数指标）
    winner = diff > threshold ? 'b' : diff < -threshold ? 'a' : 'draw'
  } else {
    winner = diff > threshold ? 'a' : diff < -threshold ? 'b' : 'draw'
  }
  return {
    label,
    a,
    b,
    diff,
    format: fmt,
    formatDiff: diffFmt,
    aClass: pctColor,
    bClass: pctColor,
    diffClass: (v: number) => (v >= 0 ? 'success' : 'danger'),
    winner,
  }
}

/** 构建无优劣判定的中性指标行（如交易天数、持仓天数等非绩效指标） */
function buildNeutralRow(
  label: string,
  a: number,
  b: number,
  fmt: (v: number) => string,
  diffFmt: (v: number) => string = daysFmt,
): MetricRow {
  const diff = a - b
  return {
    label,
    a,
    b,
    diff,
    format: fmt,
    formatDiff: diffFmt,
    aClass: () => '',
    bClass: () => '',
    diffClass: () => '',
    winner: 'neutral',
  }
}

const allCoreRows = computed<MetricRow[]>(() => {
  const m = store.currentComparison?.comparison_metrics
  if (!m) return []
  return [
    buildRow('累计收益 (%)', m.a_cumulative_return_pct, m.b_cumulative_return_pct, pctFmt),
    buildRow('最大回撤 (%)', m.a_max_drawdown_pct, m.b_max_drawdown_pct, pctFmt, pctFmt),
    buildRow('夏普比率', m.a_sharpe_ratio, m.b_sharpe_ratio, ratioFmt, ratioFmt),
    buildRow('卡玛比率', m.a_calmar_ratio, m.b_calmar_ratio, ratioFmt, ratioFmt),
  ]
})

const coreMetrics = computed(() => allCoreRows.value)

const riskRows = computed<MetricRow[]>(() => {
  const m = store.currentComparison?.comparison_metrics
  if (!m) return []
  return [
    buildRow('索提诺比率', m.a_sortino_ratio, m.b_sortino_ratio, ratioFmt, ratioFmt),
    buildRow('胜率 (%)', m.a_win_rate_pct, m.b_win_rate_pct, pctFmt),
    buildRow('信号准确率 (%)', m.a_signal_accuracy_pct, m.b_signal_accuracy_pct, pctFmt),
    // 以下为中性指标，不做优劣比较
    buildNeutralRow('交易天数', m.a_total_trading_days, m.b_total_trading_days, daysFmt, daysFmt),
    buildNeutralRow('持仓天数', m.a_active_days, m.b_active_days, daysFmt, daysFmt),
  ]
})

const riskMetrics = computed(() => riskRows.value)

const hasBenchmark = computed(
  () => store.currentComparison?.comparison_metrics?.a_benchmark_return_pct != null,
)

interface BenchRow {
  label: string
  a: number
  b: number
  format: (v: number) => string
  aClass: (v: number) => string
  bClass: (v: number) => string
  winner: Winner
}

function buildBenchRow(
  label: string,
  a: number,
  b: number,
  fmt: (v: number) => string,
  higherBetter: boolean = true,
): BenchRow {
  let winner: Winner = 'draw'
  const threshold = fmt === ratioFmt ? 0.001 : 0.01
  if (higherBetter) {
    winner = a - b > threshold ? 'a' : b - a > threshold ? 'b' : 'draw'
  } else {
    // Beta 更接近 1 不一定更好，这里简单起见不做判断
    winner = 'draw'
  }
  return { label, a, b, format: fmt, aClass: pctColor, bClass: pctColor, winner }
}

const benchmarkMetrics = computed<BenchRow[]>(() => {
  const m = store.currentComparison?.comparison_metrics
  if (!m) return []
  const rows: BenchRow[] = []
  if (m.a_benchmark_return_pct != null) {
    rows.push(buildBenchRow('基准收益 (%)', m.a_benchmark_return_pct, m.b_benchmark_return_pct ?? 0, pctFmt))
  }
  if (m.a_excess_return_pct != null) {
    rows.push(buildBenchRow('超额收益 (%)', m.a_excess_return_pct, m.b_excess_return_pct ?? 0, pctFmt))
  }
  if (m.a_alpha != null) {
    rows.push(buildBenchRow('Alpha (%)', m.a_alpha, m.b_alpha ?? 0, pctFmt))
  }
  if (m.a_beta != null) {
    rows.push(buildBenchRow('Beta', m.a_beta, m.b_beta ?? 0, ratioFmt, false))
  }
  if (m.a_information_ratio != null) {
    rows.push(buildBenchRow('信息比率', m.a_information_ratio, m.b_information_ratio ?? 0, ratioFmt))
  }
  return rows
})

// ── 图表数据 ──

const equityData = computed(() => {
  if (!store.comparisonDaily) return { dates: [], aCum: [], bCum: [] }
  const aMap = new Map(store.comparisonDaily.a_daily.map((r) => [r.trade_date, r.cumulative_return]))
  const bMap = new Map(store.comparisonDaily.b_daily.map((r) => [r.trade_date, r.cumulative_return]))

  // 取日期并集作为 X 轴，缺失日期补 null（ECharts 自动断开处理）
  const allDates = Array.from(new Set([...aMap.keys(), ...bMap.keys()])).sort()
  const aCum = allDates.map((d) => aMap.get(d) ?? null)
  const bCum = allDates.map((d) => bMap.get(d) ?? null)
  return { dates: allDates, aCum, bCum }
})

const ddData = computed(() => {
  if (!store.comparisonDaily) return { dates: [], aDD: [], bDD: [] }
  const aMap = new Map(store.comparisonDaily.a_daily.map((r) => [r.trade_date, r.drawdown]))
  const bMap = new Map(store.comparisonDaily.b_daily.map((r) => [r.trade_date, r.drawdown]))

  // 取日期并集，缺失补 null
  const allDates = Array.from(new Set([...aMap.keys(), ...bMap.keys()])).sort()
  const aDD = allDates.map((d) => aMap.get(d) ?? null)
  const bDD = allDates.map((d) => bMap.get(d) ?? null)
  return { dates: allDates, aDD, bDD }
})

const excessData = computed(() => {
  if (!store.comparisonDaily) return { dates: [], excessCum: [] }
  const aDaily = store.comparisonDaily.a_daily
  const bDaily = store.comparisonDaily.b_daily
  // 构建日期 → 日收益率的映射
  const dateMap = new Map<string, number>()
  for (const r of aDaily) dateMap.set(r.trade_date, r.portfolio_return)
  const dates: string[] = []
  const excessCum: number[] = []
  // 分别复利累计，超额 = A 累计 / B 累计 - 1（%）
  let cumA = 1.0
  let cumB = 1.0
  for (const r of bDaily) {
    const aRet = dateMap.get(r.trade_date)
    if (aRet !== undefined) {
      cumA *= 1 + aRet / 100
      cumB *= 1 + r.portfolio_return / 100
      const excess = (cumA / cumB - 1) * 100
      dates.push(r.trade_date)
      excessCum.push(Number(excess.toFixed(4)))
    }
  }
  return { dates, excessCum }
})

// ── ECharts 初始化 ──

async function initCharts() {
  const echarts = await import('echarts')

  // 权益曲线叠加
  if (equityChartEl.value) {
    equityChartInst = echarts.init(equityChartEl.value)
    equityChartInst.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['策略 A', '策略 B'], bottom: 0 },
      grid: { left: 50, right: 20, top: 20, bottom: 40 },
      xAxis: { type: 'category', data: equityData.value.dates, axisLabel: { rotate: 45, fontSize: 10 } },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => v.toFixed(0) + '%' } },
      series: [
        {
          name: '策略 A',
          type: 'line',
          data: equityData.value.aCum,
          smooth: true,
          lineStyle: { color: '#3B82F6', width: 2 },
          itemStyle: { color: '#3B82F6' },
          symbol: 'none',
        },
        {
          name: '策略 B',
          type: 'line',
          data: equityData.value.bCum,
          smooth: true,
          lineStyle: { color: '#F59E0B', width: 2 },
          itemStyle: { color: '#F59E0B' },
          symbol: 'none',
        },
      ],
    })
  }

  // 回撤曲线叠加
  if (ddChartEl.value) {
    ddChartInst = echarts.init(ddChartEl.value)
    ddChartInst.setOption({
      tooltip: { trigger: 'axis' },
      legend: { data: ['策略 A 回撤', '策略 B 回撤'], bottom: 0 },
      grid: { left: 50, right: 20, top: 20, bottom: 40 },
      xAxis: { type: 'category', data: ddData.value.dates, axisLabel: { rotate: 45, fontSize: 10 } },
      yAxis: { type: 'value', axisLabel: { formatter: (v: number) => v.toFixed(0) + '%' } },
      series: [
        {
          name: '策略 A 回撤',
          type: 'line',
          data: ddData.value.aDD,
          smooth: true,
          lineStyle: { color: '#EF4444', width: 2 },
          itemStyle: { color: '#EF4444' },
          symbol: 'none',
          areaStyle: { color: 'rgba(239,68,68,0.1)' },
        },
        {
          name: '策略 B 回撤',
          type: 'line',
          data: ddData.value.bDD,
          smooth: true,
          lineStyle: { color: '#F97316', width: 2 },
          itemStyle: { color: '#F97316' },
          symbol: 'none',
          areaStyle: { color: 'rgba(249,115,22,0.1)' },
        },
      ],
    })
  }

  // 超额收益图
  if (excessChartEl.value) {
    excessChartInst = echarts.init(excessChartEl.value)
    excessChartInst.setOption({
      tooltip: { trigger: 'axis' },
      grid: { left: 50, right: 20, top: 20, bottom: 40 },
      xAxis: { type: 'category', data: excessData.value.dates, axisLabel: { rotate: 45, fontSize: 10 } },
      yAxis: {
        type: 'value',
        axisLabel: { formatter: (v: number) => v.toFixed(0) + '%' },
        splitLine: { lineStyle: { color: 'rgba(148,163,184,0.15)' } },
      },
      series: [
        {
          name: '超额收益',
          type: 'line',
          data: excessData.value.excessCum,
          smooth: true,
          lineStyle: { color: '#8B5CF6', width: 2 },
          itemStyle: { color: '#8B5CF6' },
          symbol: 'none',
          areaStyle: {
            color: {
              type: 'linear',
              x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: excessData.value.excessCum.length > 0 && excessData.value.excessCum[excessData.value.excessCum.length - 1] >= 0 ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)' },
                { offset: 1, color: 'rgba(148,163,184,0.02)' },
              ],
            },
          },
          markLine: {
            silent: true,
            data: [{ yAxis: 0, lineStyle: { color: '#94A3B8', type: 'dashed' } }],
          },
        },
      ],
    })
  }

  // 联动缩放：权益曲线、回撤曲线、超额收益图共享 X 轴交互
  const connected = [equityChartInst, ddChartInst, excessChartInst].filter(Boolean)
  if (connected.length > 1) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    echarts.connect(connected as any)
  }
}

function disposeCharts() {
  // dispose 会自动解除图表联动
  equityChartInst?.dispose()
  ddChartInst?.dispose()
  excessChartInst?.dispose()
}

// ── 数据重载图表 ──

watch(
  () => store.comparisonDaily,
  () => {
    disposeCharts()
    initCharts()
  },
  { flush: 'post' },
)

// ── 生命周期 ──

onMounted(async () => {
  // 加载策略列表（用于显示名称）
  if (strategyStore.items.length === 0) strategyStore.loadAll()

  await store.loadOneComparison(props.comparisonId)

  const status = store.currentComparison?.status
  if (status === 'pending' || status === 'running') {
    polling.value = true
    store.pollComparisonUntilDone(props.comparisonId).then(async () => {
      polling.value = false
      if (store.currentComparison?.status === 'success') {
        await store.loadComparisonDaily(props.comparisonId)
        initCharts()
      }
    })
  } else if (status === 'success' || status === 'partial') {
    await store.loadComparisonDaily(props.comparisonId)
    initCharts()
  }
})

onUnmounted(() => {
  disposeCharts()
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-header { display: flex; flex-direction: column; gap: 6px; }
.back-link { color: var(--text-muted); text-decoration: none; font-size: 13px; }
.back-link:hover { color: var(--accent); }
.header-row { display: flex; align-items: flex-start; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }
.subtitle { color: var(--text-muted); font-size: 13px; margin-top: 4px; }
.strategy-tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 600;
}
.strategy-tag.a { background: rgba(59,130,246,0.12); color: #3B82F6; }
.strategy-tag.b { background: rgba(245,158,11,0.12); color: #F59E0B; }
.vs { margin: 0 6px; color: var(--text-muted); }

.loading { padding: 40px; text-align: center; color: var(--text-muted); }

/* 状态标签 */
.status-badge {
  display: inline-block;
  padding: 3px 12px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
}
.status-pending { background: rgba(148,163,184,0.15); color: var(--text-muted); }
.status-running { background: rgba(59,130,246,0.15); color: #60a5fa; }
.status-success { background: rgba(34,197,94,0.15); color: var(--success); }
.status-failed { background: rgba(239,68,68,0.15); color: var(--danger); }
.status-partial { background: rgba(245,158,11,0.15); color: #f59e0b; }

/* 轮询 */
.polling-banner {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px;
  background: rgba(59,130,246,0.08);
  border: 1px solid rgba(59,130,246,0.2);
  border-radius: var(--radius-sm);
  font-size: 13px; color: #60a5fa;
}
.polling-progress { display: flex; align-items: center; gap: 6px; }
.polling-bar-bg { display: inline-block; width: 120px; height: 6px; background: rgba(59,130,246,0.15); border-radius: 3px; overflow: hidden; }
.polling-bar-fill { display: block; height: 100%; background: #60a5fa; border-radius: 3px; transition: width 0.3s; }
.polling-pct { font-size: 12px; color: #60a5fa; }

/* 综合评价 */
.verdict-card {
  background: linear-gradient(135deg, rgba(59,130,246,0.05), rgba(139,92,246,0.05));
  border: 1px solid rgba(59,130,246,0.2);
  border-radius: var(--radius);
  padding: 20px 24px;
}
.verdict-title { font-size: 15px; font-weight: 700; margin-bottom: 10px; }
.verdict-text { font-size: 14px; color: var(--text); line-height: 1.7; margin-bottom: 10px; }
.verdict-count { font-size: 13px; color: var(--text-muted); }
.a-win { color: #3B82F6; font-weight: 700; }
.b-win { color: #F59E0B; font-weight: 700; }

/* 对比表 */
.section { display: flex; flex-direction: column; gap: 12px; }
.section-label { font-size: 15px; font-weight: 700; }
.comparison-table {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.comparison-table table { width: 100%; border-collapse: collapse; }
.comparison-table th {
  text-align: left; padding: 10px 16px;
  font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: rgba(0,0,0,0.1);
}
.comparison-table td { padding: 10px 16px; border-bottom: 1px solid rgba(51,65,85,0.5); font-size: 13px; }
.comparison-table tr:last-child td { border-bottom: none; }
.metric-label-col { font-weight: 600; color: var(--text); }
.success { color: var(--success); font-weight: 600; }
.danger { color: var(--danger); font-weight: 600; }

.winner-tag {
  display: inline-block;
  width: 24px; height: 24px;
  line-height: 24px;
  text-align: center;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
}
.winner-tag.a { background: rgba(59,130,246,0.15); color: #3B82F6; }
.winner-tag.b { background: rgba(245,158,11,0.15); color: #F59E0B; }
.winner-tag.draw { background: rgba(148,163,184,0.1); color: var(--text-muted); }

/* 图表 */
.chart-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}
.chart-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; }
.chart-container { width: 100%; height: 350px; }
</style>
