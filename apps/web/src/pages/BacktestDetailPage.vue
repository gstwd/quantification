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
      <!-- 轮询提示 -->
      <div v-if="polling" class="polling-banner">回测执行中，自动刷新结果...</div>

      <!-- 汇总指标卡片 -->
      <div v-if="store.current.metrics" class="metrics-section">
        <div class="section-label">核心绩效</div>
        <div class="metrics-grid">
          <div class="metric-card">
            <div class="metric-label">累计收益</div>
            <div class="metric-value" :class="store.current.metrics.cumulative_return_pct >= 0 ? 'success' : 'danger'">
              {{ formatPct(store.current.metrics.cumulative_return_pct) }}
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-label">年化收益</div>
            <div class="metric-value" :class="store.current.metrics.annualized_return_pct >= 0 ? 'success' : 'danger'">
              {{ formatPct(store.current.metrics.annualized_return_pct) }}
            </div>
          </div>
          <div class="metric-card">
            <div class="metric-label">最大回撤</div>
            <div class="metric-value danger">{{ formatPct(store.current.metrics.max_drawdown_pct) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">回撤持续</div>
            <div class="metric-value">{{ store.current.metrics.max_drawdown_days }} 天</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">夏普比率</div>
            <div class="metric-value">{{ store.current.metrics.sharpe_ratio.toFixed(2) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">卡玛比率</div>
            <div class="metric-value">{{ store.current.metrics.calmar_ratio.toFixed(2) }}</div>
          </div>
        </div>

        <div class="section-label" style="margin-top: 16px;">风险与胜率</div>
        <div class="metrics-grid">
          <div class="metric-card">
            <div class="metric-label">索提诺比率</div>
            <div class="metric-value">{{ store.current.metrics.sortino_ratio.toFixed(2) }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">胜率</div>
            <div class="metric-value">{{ store.current.metrics.win_rate_pct.toFixed(1) }}%</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">信号准确率</div>
            <div class="metric-value">{{ store.current.metrics.signal_accuracy_pct.toFixed(1) }}%</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">盈亏比</div>
            <div class="metric-value">{{ store.current.metrics.profit_loss_ratio !== null ? store.current.metrics.profit_loss_ratio.toFixed(2) : '-' }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">交易日</div>
            <div class="metric-value">{{ store.current.metrics.total_trading_days }}</div>
          </div>
          <div class="metric-card">
            <div class="metric-label">持仓日</div>
            <div class="metric-value">{{ store.current.metrics.active_days }}</div>
          </div>
        </div>

        <!-- 基准对比（有基准数据时显示） -->
        <template v-if="store.current.metrics.benchmark_return_pct !== null && store.current.metrics.benchmark_return_pct !== undefined">
          <div class="section-label" style="margin-top: 16px;">基准对比</div>
          <div class="metrics-grid">
            <div class="metric-card">
              <div class="metric-label">基准收益</div>
              <div class="metric-value" :class="store.current.metrics.benchmark_return_pct >= 0 ? 'success' : 'danger'">
                {{ formatPct(store.current.metrics.benchmark_return_pct) }}
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-label">超额收益</div>
              <div class="metric-value" :class="(store.current.metrics.excess_return_pct ?? 0) >= 0 ? 'success' : 'danger'">
                {{ formatPct(store.current.metrics.excess_return_pct ?? 0) }}
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-label">Alpha</div>
              <div class="metric-value" :class="(store.current.metrics.alpha ?? 0) >= 0 ? 'success' : 'danger'">
                {{ store.current.metrics.alpha !== null ? formatPct(store.current.metrics.alpha) : '-' }}
              </div>
            </div>
            <div class="metric-card">
              <div class="metric-label">Beta</div>
              <div class="metric-value">{{ store.current.metrics.beta !== null ? store.current.metrics.beta.toFixed(2) : '-' }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-label">信息比率</div>
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

      <!-- 信号分布（信号模式） -->
      <div v-if="!isAllocationMode" class="chart-card">
        <div class="chart-title">每日信号分布</div>
        <div ref="signalChartEl" class="chart-container chart-short"></div>
      </div>

      <!-- 仓位变化（配置模式） -->
      <div v-if="isAllocationMode" class="chart-card">
        <div class="chart-title">仓位变化</div>
        <div ref="positionsChartEl" class="chart-container"></div>
      </div>

      <!-- 择时状态时间线（配置模式） -->
      <div v-if="isAllocationMode && regimeTimeline.length > 0" class="table-card">
        <div class="table-title">择时状态变化</div>
        <div class="regime-timeline">
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

      <!-- per-指数 汇总表（信号模式） -->
      <div v-if="!isAllocationMode && indexSummary.length > 0" class="table-card">
        <div class="table-title">指数信号汇总</div>
        <table class="data-table">
          <thead>
            <tr>
              <th>指数代码</th>
              <th>HIGH 次数</th>
              <th>平均得分</th>
              <th>原始均分</th>
              <th>信号准确率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in indexSummary" :key="row.index_code">
              <td class="mono">{{ row.index_code }}</td>
              <td>{{ row.high_count }}</td>
              <td>{{ row.avg_score.toFixed(1) }}</td>
              <td>{{ row.avg_original_score !== null ? row.avg_original_score.toFixed(1) : '-' }}</td>
              <td :class="row.accuracy >= 50 ? 'success' : 'danger'">{{ row.accuracy.toFixed(1) }}%</td>
            </tr>
          </tbody>
        </table>
      </div>

    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { useBacktestStore } from '../stores/backtests'

const props = defineProps<{ backtestId: string }>()
const store = useBacktestStore()

const equityChartEl = ref<HTMLElement | null>(null)
const drawdownChartEl = ref<HTMLElement | null>(null)
const signalChartEl = ref<HTMLElement | null>(null)
const positionsChartEl = ref<HTMLElement | null>(null)
const polling = ref(false)

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let equityChart: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let drawdownChart: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let signalChart: any = null
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let positionsChart: any = null

/** 是否为配置模式 */
const isAllocationMode = computed(() => store.current?.backtest_mode === 'allocation')

function statusLabel(status: string): string {
  const map: Record<string, string> = { pending: '待执行', running: '执行中', success: '成功', failed: '失败' }
  return map[status] ?? status
}

function formatPct(v: number): string {
  return (v >= 0 ? '+' : '') + v.toFixed(2) + '%'
}

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

/** 按指数汇总信号统计 */
const indexSummary = computed(() => {
  const map = new Map<string, { high_count: number; scores: number[]; original_scores: number[]; correct: number; total: number }>()
  for (const r of store.indexResults) {
    if (!map.has(r.index_code)) map.set(r.index_code, { high_count: 0, scores: [], original_scores: [], correct: 0, total: 0 })
    const entry = map.get(r.index_code)!
    entry.scores.push(r.signal_score)
    if (r.original_score !== null && r.original_score !== undefined) {
      entry.original_scores.push(r.original_score)
    }
    if (r.signal_level === 'HIGH') entry.high_count++
    if (r.in_portfolio && r.index_return !== null) {
      entry.total++
      if (r.index_return > 0) entry.correct++
    }
  }
  return Array.from(map.entries())
    .map(([index_code, v]) => ({
      index_code,
      high_count: v.high_count,
      avg_score: v.scores.reduce((a, b) => a + b, 0) / (v.scores.length || 1),
      avg_original_score: v.original_scores.length > 0
        ? v.original_scores.reduce((a, b) => a + b, 0) / v.original_scores.length
        : null as number | null,
      accuracy: v.total > 0 ? (v.correct / v.total) * 100 : 0,
    }))
    .sort((a, b) => b.high_count - a.high_count)
})

async function initCharts() {
  if (store.dailyResults.length === 0) return
  const echarts = await import('echarts')

  const dates = store.dailyResults.map((r) => r.trade_date)
  const cumReturns = store.dailyResults.map((r) => r.cumulative_return)
  const drawdowns = store.dailyResults.map((r) => r.drawdown)
  const highCounts = store.dailyResults.map((r) => r.high_signal_count)
  const midCounts = store.dailyResults.map((r) => r.mid_signal_count)
  const lowCounts = store.dailyResults.map((r) => r.low_signal_count)

  // 计算基准累计收益（从日收益率累加）
  const hasBenchmark = store.dailyResults.some((r) => r.benchmark_return !== null && r.benchmark_return !== undefined)
  const benchmarkCum: number[] = []
  if (hasBenchmark) {
    let benchCum = 0
    for (const r of store.dailyResults) {
      if (r.benchmark_return !== null && r.benchmark_return !== undefined) {
        benchCum = (1 + benchCum / 100) * (1 + r.benchmark_return / 100) - 1
        benchCum = benchCum * 100
      }
      benchmarkCum.push(Number(benchCum.toFixed(4)))
    }
  }

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
    if (hasBenchmark) {
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
      legend: hasBenchmark ? { data: ['策略', '基准'], textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0 } : undefined,
      grid: { left: 60, right: 20, top: hasBenchmark ? 30 : 20, bottom: 40 },
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

  // 信号分布堆叠柱状图（信号模式）
  if (!isAllocationMode.value && signalChartEl.value) {
    signalChart?.dispose()
    signalChart = echarts.init(signalChartEl.value)
    signalChart.setOption({
      backgroundColor: 'transparent',
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { data: ['HIGH', 'MID', 'LOW'], textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0 },
      grid: { left: 40, right: 20, top: 30, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#94a3b8', fontSize: 10, interval: Math.floor(dates.length / 8) }, axisLine: { lineStyle: { color: '#334155' } } },
      yAxis: { type: 'value', axisLabel: { color: '#94a3b8', fontSize: 11 }, splitLine: { lineStyle: { color: '#1e293b' } } },
      series: [
        { name: 'HIGH', type: 'bar', stack: 'signal', data: highCounts, itemStyle: { color: '#22c55e' } },
        { name: 'MID', type: 'bar', stack: 'signal', data: midCounts, itemStyle: { color: '#f59e0b' } },
        { name: 'LOW', type: 'bar', stack: 'signal', data: lowCounts, itemStyle: { color: '#475569' } },
      ],
    })
  }

  // 仓位变化堆叠面积图（配置模式）
  if (isAllocationMode.value && positionsChartEl.value) {
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

    // 构建每个指数的权重时间序列
    const colorPalette = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#a855f7', '#06b6d4', '#f97316', '#ec4899']
    const series = codeList.map((code, idx) => ({
      name: code,
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
      legend: { data: [...codeList, '现金'], textStyle: { color: '#94a3b8', fontSize: 11 }, top: 0, type: 'scroll' },
      grid: { left: 60, right: 20, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: dates, axisLabel: { color: '#94a3b8', fontSize: 10, interval: Math.floor(dates.length / 8) }, axisLine: { lineStyle: { color: '#334155' } } },
      yAxis: { type: 'value', max: 100, axisLabel: { color: '#94a3b8', fontSize: 11, formatter: (v: number) => v + '%' }, splitLine: { lineStyle: { color: '#1e293b' } } },
      series,
    })
  }
}

watch(() => store.dailyResults, () => { if (store.dailyResults.length > 0) initCharts() })

onMounted(async () => {
  await store.loadOne(props.backtestId)
  if (store.current?.status === 'pending' || store.current?.status === 'running') {
    polling.value = true
    await store.pollUntilDone(props.backtestId)
    polling.value = false
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
  signalChart?.dispose()
  positionsChart?.dispose()
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
  padding: 10px 16px;
  background: rgba(59,130,246,0.1);
  border: 1px solid rgba(59,130,246,0.3);
  border-radius: var(--radius-sm);
  color: #60a5fa;
  font-size: 13px;
}

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
