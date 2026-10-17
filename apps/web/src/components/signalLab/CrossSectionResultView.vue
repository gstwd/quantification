<template>
  <div class="result">
    <IssueList :issues="result.issues" />

    <div class="metric-grid">
      <div class="metric">
        <span class="m-label">IC 均值（h={{ result.horizon }}）</span>
        <span class="m-value" :class="signClass(s.mean)">{{ fmtNum(s.mean) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">IC 标准差</span>
        <span class="m-value">{{ fmtNum(s.std) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">ICIR</span>
        <span class="m-value" :class="signClass(s.icir)">{{ fmtNum(s.icir, 2) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">IC &gt; 0 占比</span>
        <span class="m-value">{{ fmtPct(s.positive_ratio) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">有效期数 / 平均资产数</span>
        <span class="m-value">{{ s.n_periods }} / {{ fmtNum(s.avg_assets, 1) }}</span>
      </div>
      <div class="metric">
        <span class="m-label">分年同号（与全期均值）</span>
        <span class="m-value">{{ sameSignText }}</span>
      </div>
    </div>

    <div ref="icChartEl" class="chart ic-chart"></div>

    <div class="sub-grid">
      <div>
        <div class="sub-title">分年 IC</div>
        <div ref="yearChartEl" class="chart small-chart"></div>
      </div>
      <div>
        <div class="sub-title">三分组平均 {{ result.horizon }} 日收益（G1 低分 → G3 高分）</div>
        <div ref="tercileChartEl" class="chart small-chart"></div>
      </div>
      <div>
        <div class="sub-title">与现有评分因子的平均截面相关（≥ 0.6 视为同源）</div>
        <table class="table">
          <tbody>
            <tr v-for="row in result.correlations" :key="row.label">
              <td>{{ row.label }}</td>
              <td :class="{ warn: row.corr !== null && Math.abs(row.corr) >= 0.6 }">
                {{ fmtNum(row.corr, 2) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="sub-grid">
      <div>
        <div class="sub-title">
          最近调仓日截面 {{ result.latest_date ?? '' }}（按因子值降序；未来收益为空表示尚未实现）
        </div>
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>排名</th>
                <th>指数</th>
                <th>因子值</th>
                <th>未来 {{ result.horizon }} 日</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in result.latest" :key="row.index_code">
                <td>{{ row.rank ?? '—' }}</td>
                <td class="name">{{ row.index_name }} <span class="code">{{ row.index_code }}</span></td>
                <td>{{ fmtNum(row.value, 4) }}</td>
                <td :class="signClass(row.forward_return)">{{ fmtPct(row.forward_return, 2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div>
        <div class="sub-title">指数池覆盖（字段覆盖 &lt; 95% 的指数被剔除）</div>
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>指数</th>
                <th>首日</th>
                <th>OHL</th>
                <th>量</th>
                <th>额</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in sortedCoverage" :key="row.index_code" :class="{ excluded: !row.used }">
                <td class="name">{{ row.index_name }} <span class="code">{{ row.index_code }}</span></td>
                <td>{{ row.first_date ?? '—' }}</td>
                <td>{{ fmtPct(row.ohl, 0) }}</td>
                <td>{{ fmtPct(row.volume, 0) }}</td>
                <td>{{ fmtPct(row.amount, 0) }}</td>
                <td :title="row.reason ?? ''">{{ row.used ? '参与' : '剔除' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <IssueList :issues="[]" :notes="result.notes" />
  </div>
</template>

<script setup lang="ts">
/**
 * 截面因子 IC 结果视图：IC 柱 + 累计 IC、分年 IC、三分组收益、
 * 与现有因子相关性、最近截面排名与指数池覆盖。
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { ECharts } from 'echarts'

import type { SignalLabCrossSectionResponse } from '../../types/api'
import IssueList from './IssueList.vue'
import { CHART_COLORS, fmtNum, fmtPct, signClass } from './format'

const props = defineProps<{ result: SignalLabCrossSectionResponse }>()

const icChartEl = ref<HTMLElement | null>(null)
const yearChartEl = ref<HTMLElement | null>(null)
const tercileChartEl = ref<HTMLElement | null>(null)
let charts: ECharts[] = []
let observer: ResizeObserver | null = null

const s = computed(() => props.result.summary)
const sortedCoverage = computed(() =>
  [...props.result.coverage].sort((a, b) => Number(a.used) - Number(b.used)),
)
const sameSignText = computed(() => {
  const mean = s.value.mean
  const years = props.result.yearly.filter((y) => y.ic_mean !== null)
  if (mean === null || !years.length) return '—'
  const same = years.filter((y) => Math.sign(y.ic_mean as number) === Math.sign(mean)).length
  return `${same}/${years.length}`
})

const axisStyle = {
  axisLabel: { color: CHART_COLORS.axis, fontSize: 11 },
  axisLine: { lineStyle: { color: CHART_COLORS.grid } },
  splitLine: { lineStyle: { color: CHART_COLORS.grid, type: 'dashed' as const } },
}

/** 渲染全部图表 */
async function render(): Promise<void> {
  const echarts = await import('echarts')
  charts.forEach((c) => c.dispose())
  charts = []
  const r = props.result
  if (icChartEl.value) {
    const chart = echarts.init(icChartEl.value, undefined, { renderer: 'canvas' })
    chart.setOption({
      backgroundColor: 'transparent',
      animation: false,
      legend: { top: 0, textStyle: { color: CHART_COLORS.axis, fontSize: 11 } },
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(3) : '—'),
      },
      grid: { left: 60, right: 60, top: 32, bottom: 56 },
      xAxis: { type: 'category', data: r.dates, ...axisStyle, axisLabel: { ...axisStyle.axisLabel, hideOverlap: true } },
      yAxis: [
        { type: 'value', name: '单期 IC', nameTextStyle: { color: CHART_COLORS.axis }, ...axisStyle },
        {
          type: 'value',
          name: '累计 IC',
          nameTextStyle: { color: CHART_COLORS.axis },
          ...axisStyle,
          splitLine: { show: false },
        },
      ],
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 6 }],
      series: [
        {
          name: '单期 IC',
          type: 'bar',
          data: r.ic.map((v) => ({
            value: v,
            itemStyle: { color: (v ?? 0) >= 0 ? 'rgba(34,197,94,0.55)' : 'rgba(239,68,68,0.55)' },
          })),
        },
        {
          name: '累计 IC',
          type: 'line',
          yAxisIndex: 1,
          showSymbol: false,
          lineStyle: { width: 1.6, color: CHART_COLORS.signal },
          itemStyle: { color: CHART_COLORS.signal },
          data: r.cum_ic,
        },
      ],
    })
    charts.push(chart)
  }
  if (yearChartEl.value) {
    const chart = echarts.init(yearChartEl.value, undefined, { renderer: 'canvas' })
    chart.setOption({
      backgroundColor: 'transparent',
      animation: false,
      tooltip: { trigger: 'axis', valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(3) : '—') },
      grid: { left: 50, right: 16, top: 16, bottom: 30 },
      xAxis: { type: 'category', data: r.yearly.map((y) => String(y.year)), ...axisStyle },
      yAxis: { type: 'value', ...axisStyle },
      series: [
        {
          name: 'IC 均值',
          type: 'bar',
          data: r.yearly.map((y) => ({
            value: y.ic_mean,
            itemStyle: { color: (y.ic_mean ?? 0) >= 0 ? '#22c55e' : '#ef4444' },
          })),
        },
      ],
    })
    charts.push(chart)
  }
  if (tercileChartEl.value) {
    const chart = echarts.init(tercileChartEl.value, undefined, { renderer: 'canvas' })
    chart.setOption({
      backgroundColor: 'transparent',
      animation: false,
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: unknown) => (typeof v === 'number' ? `${(v * 100).toFixed(2)}%` : '—'),
      },
      grid: { left: 60, right: 16, top: 16, bottom: 30 },
      xAxis: { type: 'category', data: r.terciles.map((t) => t.group), ...axisStyle },
      yAxis: {
        type: 'value',
        ...axisStyle,
        axisLabel: { ...axisStyle.axisLabel, formatter: (v: number) => `${(v * 100).toFixed(2)}%` },
      },
      series: [
        {
          name: `平均 ${r.horizon} 日收益`,
          type: 'bar',
          data: r.terciles.map((t) => t.mean_return),
          itemStyle: { color: CHART_COLORS.signal },
          label: {
            show: true,
            position: 'top',
            color: CHART_COLORS.axis,
            fontSize: 11,
            formatter: (p: { dataIndex: number }) => `年化 ${fmtPct(r.terciles[p.dataIndex]?.annualized)}`,
          },
        },
      ],
    })
    charts.push(chart)
  }
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
  observer = new ResizeObserver(() => charts.forEach((c) => c.resize()))
  for (const el of [icChartEl.value, yearChartEl.value, tercileChartEl.value]) {
    if (el) observer.observe(el)
  }
})

onUnmounted(() => {
  observer?.disconnect()
  observer = null
  charts.forEach((c) => c.dispose())
  charts = []
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
.ic-chart {
  height: 300px;
}
.small-chart {
  height: 220px;
}
.sub-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 16px;
}
.sub-title {
  color: var(--text-muted);
  font-size: 12px;
  margin-bottom: 6px;
}
.table-wrap {
  max-height: 360px;
  overflow: auto;
}
.table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.table th,
.table td {
  text-align: right;
  padding: 5px 8px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.table th:first-child,
.table td:first-child,
.table td.name {
  text-align: left;
}
.table th {
  color: #64748b;
  font-weight: 500;
  position: sticky;
  top: 0;
  background: var(--surface);
}
.code {
  color: #64748b;
  font-size: 11px;
}
.excluded td {
  color: #64748b;
}
.warn {
  color: var(--warning);
  font-weight: 600;
}
.pos {
  color: var(--success);
}
.neg {
  color: var(--danger);
}
</style>
