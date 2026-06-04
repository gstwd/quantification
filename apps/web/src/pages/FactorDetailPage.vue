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
            <span class="chip" :class="`chip-${spec.category ?? 'default'}`">
              {{ CATEGORY_LABELS[spec.category ?? ''] ?? spec.category ?? '未分类' }}
            </span>
            <span class="chip chip-ver">v{{ spec.version }}</span>
            <span class="status-tag" :class="spec.is_active ? 'active' : 'disabled'">
              {{ spec.is_active ? '启用' : '禁用' }}
            </span>
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
        <button class="tab-btn" :class="{ active: tab === 'ic' }" @click="tab = 'ic'; loadIC()">IC 分析</button>
        <button class="tab-btn" :class="{ active: tab === 'correlation' }" @click="tab = 'correlation'; loadCorrelation()">相关性</button>
      </div>

      <!-- 横截面 Tab -->
      <div v-show="tab === 'cross'" class="card">
        <div class="card-header">
          <span class="card-title">横截面快照</span>
          <span v-if="crossDate" class="card-subtitle">数据日期：{{ crossDate }}</span>
          <div class="controls">
            <input type="date" class="date-input" v-model="crossDateInput" />
            <button class="query-btn" @click="loadCrossByDate">指定日期查询</button>
            <button class="recompute-btn" :disabled="crossLoading" @click="loadCross(true)">
              {{ crossLoading ? '计算中...' : '强制重新计算' }}
            </button>
          </div>
        </div>
        <details class="info-block">
          <summary>查看说明</summary>
          <p><strong>横截面快照</strong>是某一天所有 ETF 的因子值截面，按因子值降序排列。因子值越高，表示该因子维度上信号越强。</p>
          <p><strong>相对分布</strong>柱显示各 ETF 因子值在当日全量 ETF 中的归一化位置，便于直观对比。</p>
          <p>点击行尾的「时间序列」按钮，可跳转查看该 ETF 在此因子上的历史走势。</p>
        </details>
        <div v-if="crossLoading" class="empty">加载中...</div>
        <div v-else-if="crossRows.length === 0" class="empty">暂无数据</div>
        <table v-else class="data-table">
          <thead>
            <tr>
              <th>排名</th>
              <th>ETF 代码</th>
              <th>ETF 名称</th>
              <th>因子值</th>
              <th class="bar-col">相对分布</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, idx) in crossRows" :key="row.etf_code">
              <td class="rank">{{ idx + 1 }}</td>
              <td>
                <RouterLink :to="`/etfs/${row.etf_code}`" class="etf-link">{{ row.etf_code }}</RouterLink>
              </td>
              <td class="name-cell">{{ row.name_cn }}</td>
              <td class="value mono">{{ row.factor_value_numeric != null ? row.factor_value_numeric.toFixed(4) : '—' }}</td>
              <td class="bar-cell">
                <div class="bar-track">
                  <div
                    class="bar-fill"
                    :style="{ width: barWidth(row.factor_value_numeric) + '%' }"
                  ></div>
                </div>
              </td>
              <td>
                <button class="series-btn" @click="goToSeries(row.etf_code)">时间序列</button>
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
              @keyup.enter="() => loadSeries()"
            />
            <input type="date" class="date-input" v-model="seriesStart" />
            <span class="sep">~</span>
            <input type="date" class="date-input" v-model="seriesEnd" />
            <button class="query-btn" @click="() => loadSeries()">查询</button>
            <button class="recompute-btn" :disabled="seriesLoading" @click="() => loadSeries(true)">
              {{ seriesLoading ? '计算中...' : '强制重新计算' }}
            </button>
          </div>
        </div>
        <details class="info-block">
          <summary>查看说明</summary>
          <p><strong>时间序列</strong>展示单个 ETF 在指定日期范围内因子值的变化趋势。输入 ETF 代码和日期范围后点击查询。</p>
          <p>后端会自动补算缺失日期的因子值，勾选「强制重新计算」可覆盖已有数据。</p>
        </details>
        <div v-if="seriesLoading" class="empty">加载中...</div>
        <div v-else-if="seriesRows.length === 0" class="empty">输入 ETF 代码并查询，查看因子值走势</div>
        <div v-else ref="chartEl" class="chart-container"></div>
      </div>

      <!-- IC 分析 Tab -->
      <div v-show="tab === 'ic'" class="card">
        <div class="card-header">
          <span class="card-title">IC 分析（Rank IC）</span>
          <div class="controls">
            <input type="date" class="date-input" v-model="icStart" />
            <span class="sep">~</span>
            <input type="date" class="date-input" v-model="icEnd" />
            <button class="query-btn" @click="loadIC">查询</button>
          </div>
        </div>
        <details class="info-block">
          <summary>查看说明</summary>
          <p><strong>Rank IC（Information Coefficient）</strong>是因子值与下期收益率的 Spearman 秩相关系数，衡量因子的预测能力。取值范围 [-1, 1]。</p>
          <p><strong>计算流程：</strong>取当日所有 ETF 的因子值 → 计算 N 天后的收益率 → 对两个序列做 Spearman 相关。</p>
          <div class="info-table">
            <div class="info-row"><span class="info-label">IC 均值</span><span class="info-desc">所有交易日 IC 的平均值。<strong>> 0</strong> 表示因子有正向预测力，绝对值 > 0.03 算有效。</span></div>
            <div class="info-row"><span class="info-label">IC 标准差</span><span class="info-desc">IC 的波动程度，<strong>越小越稳定</strong>。</span></div>
            <div class="info-row"><span class="info-label">IC_IR</span><span class="info-desc">IC 均值 / IC 标准差，类似夏普比率。<strong>> 0.5 较稳定，> 1.0 优秀</strong>，是衡量因子质量的核心指标。</span></div>
            <div class="info-row"><span class="info-label">IC>0 占比</span><span class="info-desc">IC 为正的交易日占比。<strong>> 50%</strong> 说明多数时候方向正确，> 60% 较好。</span></div>
            <div class="info-row"><span class="info-label">数据点</span><span class="info-desc">有效 IC 计算天数，<strong>< 30 天</strong>时统计意义不足。</span></div>
          </div>
          <p><strong>柱状图解读：</strong>绿色柱 = IC > 0（因子预测方向正确），红色柱 = IC < 0（方向相反）。柱子越高预测力越强。大面积绿色且柱高说明因子有效且稳定。</p>
        </details>
        <div v-if="icLoading" class="empty">加载中...</div>
        <template v-else>
          <div v-if="icSummary && icSummary.count > 0" class="ic-summary-row">
            <div class="stat-card">
              <span class="stat-label">IC 均值</span>
              <span class="stat-value" :class="(icSummary.ic_mean ?? 0) >= 0 ? 'positive' : 'negative'">
                {{ icSummary.ic_mean?.toFixed(4) ?? '—' }}
              </span>
            </div>
            <div class="stat-card">
              <span class="stat-label">IC 标准差</span>
              <span class="stat-value">{{ icSummary.ic_std?.toFixed(4) ?? '—' }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-label">IC_IR</span>
              <span class="stat-value" :class="(icSummary.ic_ir ?? 0) >= 0.5 ? 'positive' : ''">
                {{ icSummary.ic_ir?.toFixed(4) ?? '—' }}
              </span>
            </div>
            <div class="stat-card">
              <span class="stat-label">IC>0 占比</span>
              <span class="stat-value">{{ icSummary.ic_positive_ratio != null ? (icSummary.ic_positive_ratio * 100).toFixed(1) + '%' : '—' }}</span>
            </div>
            <div class="stat-card">
              <span class="stat-label">数据点</span>
              <span class="stat-value">{{ icSummary.count }}</span>
            </div>
          </div>
          <div v-if="icSeries.length === 0" class="empty">暂无 IC 数据，请调整日期范围</div>
          <div v-else ref="icChartEl" class="chart-container"></div>
        </template>
      </div>

      <!-- 相关性 Tab -->
      <div v-show="tab === 'correlation'" class="card">
        <div class="card-header">
          <span class="card-title">因子相关性矩阵</span>
          <span v-if="corrData" class="card-subtitle">ETF 数量：{{ corrData.etf_count }}</span>
          <div class="controls">
            <input type="date" class="date-input" v-model="corrDate" />
            <button class="query-btn" @click="loadCorrelation">查询</button>
          </div>
        </div>
        <details class="info-block">
          <summary>查看说明</summary>
          <p><strong>因子相关性矩阵</strong>衡量同一天各因子之间的 Spearman 秩相关系数，用于判断因子冗余度。对角线恒为 1.0（自身完全相关）。</p>
          <p><strong>热力图颜色：</strong>绿色 = 正相关（两个因子同向变化），红色 = 负相关（反向变化），深色/黑色 = 接近 0（相互独立）。</p>
          <div class="info-table">
            <div class="info-row"><span class="info-label">|r| > 0.7</span><span class="info-desc">高度冗余，两个因子捕捉的信息高度重叠，建议只保留一个或做正交化处理。</span></div>
            <div class="info-row"><span class="info-label">|r| < 0.3</span><span class="info-desc">低相关，互补性好，组合使用可提升策略覆盖面。</span></div>
            <div class="info-row"><span class="info-label">负相关</span><span class="info-desc">两个因子可能捕捉市场的不同维度（如量能 vs 波动率），组合时注意对冲效应。</span></div>
          </div>
          <p>选择相关性低的因子组合，可以降低策略的单一因子依赖，提高鲁棒性。</p>
        </details>
        <div v-if="corrLoading" class="empty">加载中...</div>
        <template v-else>
          <div v-if="!corrData || corrData.matrix.length === 0" class="empty">暂无相关性数据，请确认当日有多个因子的计算结果</div>
          <div v-else ref="corrChartEl" class="chart-container corr-chart"></div>
        </template>
      </div>
    </template>
    <div v-else class="empty">因子不存在</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 因子详情页面。
 *
 * 展示单个因子的元数据、横截面快照和时间序列图表。
 * 横截面自动展示最新有数据的交易日，后端按需自动计算。
 * 时间序列查询时后端自动补算缺失日期。
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchFactorCrossSection, fetchFactorCorrelation, fetchFactorIC, fetchFactorSpecs, fetchFactorTimeSeries } from '../api/factors'
import type { CorrelationResponse, CrossSectionRow, FactorRow, FactorSpec, ICPoint, ICSummary } from '../types/api'

const props = defineProps<{ factorId: string }>()

const spec = ref<FactorSpec | null>(null)
const specLoading = ref(false)

const tab = ref<'cross' | 'series' | 'ic' | 'correlation'>('cross')

/** 横截面状态 */
const crossDate = ref('')
const crossDateInput = ref('')
const crossRows = ref<CrossSectionRow[]>([])
const crossLoading = ref(false)

/** 时间序列状态 */
const seriesEtf = ref('')
const seriesRows = ref<FactorRow[]>([])
const seriesLoading = ref(false)
const chartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let chartInstance: any = null

/** IC 分析状态 */
const icStart = ref((() => {
  const d = new Date()
  d.setDate(d.getDate() - 180)
  return d.toISOString().slice(0, 10)
})())
const icEnd = ref(new Date().toISOString().slice(0, 10))
const icSummary = ref<ICSummary | null>(null)
const icSeries = ref<ICPoint[]>([])
const icLoading = ref(false)
const icChartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let icChartInstance: any = null

/** 相关性状态 */
const corrDate = ref(new Date().toISOString().slice(0, 10))
const corrData = ref<CorrelationResponse | null>(null)
const corrLoading = ref(false)
const corrChartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let corrChartInstance: any = null

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

/** 加载横截面数据（默认最新日期），按因子值降序排列 */
async function loadCross(forceRecompute = false) {
  crossLoading.value = true
  try {
    const resp = await fetchFactorCrossSection(props.factorId, undefined, forceRecompute)
    crossDate.value = resp.trade_date
    crossDateInput.value = resp.trade_date
    crossRows.value = resp.rows
      .slice()
      .sort((a, b) => (b.factor_value_numeric ?? -Infinity) - (a.factor_value_numeric ?? -Infinity))
  } catch {
    crossRows.value = []
  } finally {
    crossLoading.value = false
  }
}

/** 按指定日期加载横截面 */
async function loadCrossByDate() {
  if (!crossDateInput.value) return
  crossLoading.value = true
  try {
    const resp = await fetchFactorCrossSection(props.factorId, crossDateInput.value)
    crossDate.value = resp.trade_date
    crossRows.value = resp.rows
      .slice()
      .sort((a, b) => (b.factor_value_numeric ?? -Infinity) - (a.factor_value_numeric ?? -Infinity))
  } catch {
    crossRows.value = []
  } finally {
    crossLoading.value = false
  }
}

/** 从横截面跳转到指定 ETF 的时间序列 */
function goToSeries(etfCode: string) {
  seriesEtf.value = etfCode
  tab.value = 'series'
  nextTick(() => loadSeries())
}

/** 加载时间序列数据并渲染图表 */
async function loadSeries(forceRecompute = false) {
  if (!seriesEtf.value.trim()) return
  seriesLoading.value = true
  try {
    seriesRows.value = await fetchFactorTimeSeries(
      props.factorId,
      seriesEtf.value.trim(),
      seriesStart.value,
      seriesEnd.value,
      forceRecompute,
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

/** 加载 IC 分析数据 */
async function loadIC() {
  if (!icStart.value || !icEnd.value) return
  icLoading.value = true
  try {
    const resp = await fetchFactorIC(props.factorId, icStart.value, icEnd.value)
    icSummary.value = resp.summary
    icSeries.value = resp.series
  } catch {
    icSummary.value = null
    icSeries.value = []
  } finally {
    icLoading.value = false
  }
}

/** 渲染 IC 时间序列图表 */
async function renderICChart() {
  if (!icChartEl.value || icSeries.value.length === 0) return
  const echarts = await import('echarts')
  icChartInstance?.dispose()
  icChartInstance = echarts.init(icChartEl.value, null, { renderer: 'canvas' })
  const dates = icSeries.value.map(r => r.trade_date)
  const values = icSeries.value.map(r => r.ic)
  icChartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#f1f5f9', fontSize: 12 },
      formatter: (params: { name: string; value: number }[]) => {
        const p = params[0]
        return `${p.name}<br/>Rank IC: <b>${p.value?.toFixed(4) ?? '—'}</b>`
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
        type: 'bar',
        data: values.map(v => ({
          value: v,
          itemStyle: { color: v >= 0 ? '#4ade80' : '#f87171' },
        })),
      },
    ],
  })
}

watch([icSeries, icChartEl], () => {
  if (!icLoading.value) renderICChart()
}, { flush: 'post' })

/** 加载因子相关性数据 */
async function loadCorrelation() {
  if (!corrDate.value) return
  corrLoading.value = true
  try {
    corrData.value = await fetchFactorCorrelation(corrDate.value)
  } catch {
    corrData.value = null
  } finally {
    corrLoading.value = false
  }
}

/** 渲录相关性热力图 */
async function renderCorrChart() {
  if (!corrChartEl.value || !corrData.value || corrData.value.matrix.length === 0) return
  const echarts = await import('echarts')
  corrChartInstance?.dispose()
  corrChartInstance = echarts.init(corrChartEl.value, null, { renderer: 'canvas' })
  const { factor_ids: fids, matrix } = corrData.value
  const data: [number, number, number][] = []
  for (let i = 0; i < fids.length; i++) {
    for (let j = 0; j < fids.length; j++) {
      data.push([j, i, matrix[i][j]])
    }
  }
  corrChartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#f1f5f9', fontSize: 12 },
      formatter: (p: { data: [number, number, number] }) => {
        const d = p.data
        return `${fids[d[1]]} × ${fids[d[0]]}<br/>相关系数: <b>${d[2].toFixed(4)}</b>`
      },
    },
    grid: { left: 100, right: 40, top: 20, bottom: 80 },
    xAxis: {
      type: 'category',
      data: fids,
      axisLabel: { color: '#94a3b8', fontSize: 10, rotate: 45 },
      splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0.02)', 'transparent'] } },
    },
    yAxis: {
      type: 'category',
      data: fids,
      axisLabel: { color: '#94a3b8', fontSize: 10 },
      splitArea: { show: true, areaStyle: { color: ['rgba(255,255,255,0.02)', 'transparent'] } },
    },
    visualMap: {
      min: -1,
      max: 1,
      calculable: true,
      orient: 'vertical',
      right: 0,
      top: 'center',
      inRange: {
        color: ['#f87171', '#1e293b', '#4ade80'],
      },
      textStyle: { color: '#94a3b8', fontSize: 11 },
    },
    series: [
      {
        type: 'heatmap',
        data,
        emphasis: {
          itemStyle: { borderColor: '#f1f5f9', borderWidth: 1 },
        },
      },
    ],
  })
}

watch([corrData, corrChartEl], () => {
  if (!corrLoading.value) renderCorrChart()
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
  icChartInstance?.dispose()
  corrChartInstance?.dispose()
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
.chip-default   { background: var(--surface-2, rgba(255,255,255,0.05)); color: var(--text-muted); }
.chip-ver       { background: var(--surface-2); color: var(--text-muted); }

.status-tag {
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 20px;
  font-weight: 600;
}
.status-tag.active  { background: rgba(34, 197, 94, 0.12); color: #4ade80; }
.status-tag.disabled { background: rgba(239, 68, 68, 0.12); color: #f87171; }

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
.card-subtitle { font-size: 12px; color: var(--text-muted); }

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

.recompute-btn {
  background: rgba(245, 158, 11, 0.15);
  border: 1px solid rgba(245, 158, 11, 0.3);
  color: #fbbf24;
  border-radius: var(--radius-sm);
  padding: 4px 14px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.recompute-btn:hover:not(:disabled) { background: rgba(245, 158, 11, 0.25); }
.recompute-btn:disabled { opacity: 0.5; cursor: not-allowed; }

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
.name-cell { font-size: 13px; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.value.mono { font-family: monospace; font-size: 13px; }
.bar-col { width: 180px; }
.etf-link { color: var(--accent); font-family: monospace; font-weight: 600; }
.etf-link:hover { text-decoration: underline; }

.bar-cell { padding: 9px 16px 9px 0; }
.bar-track { background: var(--surface-2, rgba(255,255,255,0.06)); border-radius: 4px; height: 6px; overflow: hidden; }
.bar-fill  { height: 100%; background: var(--accent); border-radius: 4px; transition: width 0.3s ease; }

.series-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 2px 10px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.series-btn:hover { color: var(--accent); border-color: var(--accent); }

/* 时间序列图表 */
.chart-container { width: 100%; height: 340px; padding: 8px; }
.corr-chart { height: 400px; }

/* IC 汇总统计卡片 */
.ic-summary-row {
  display: flex;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--border);
  flex-wrap: wrap;
}
.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  background: var(--surface-2, rgba(255,255,255,0.04));
  border-radius: var(--radius-sm);
  padding: 10px 16px;
  min-width: 100px;
}
.stat-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
.stat-value { font-size: 16px; font-weight: 600; font-family: monospace; }
.stat-value.positive { color: #4ade80; }
.stat-value.negative { color: #f87171; }

/* 说明折叠块 */
.info-block {
  border-bottom: 1px solid var(--border);
  padding: 0 20px;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.7;
}
.info-block summary {
  cursor: pointer;
  padding: 8px 0;
  font-size: 12px;
  color: var(--text-muted);
  user-select: none;
}
.info-block summary:hover { color: var(--text); }
.info-block p { margin: 6px 0; }
.info-block strong { color: var(--text); font-weight: 600; }
.info-block[open] { padding-bottom: 14px; }

.info-table {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 8px 0;
  padding: 10px 14px;
  background: var(--surface-2, rgba(255,255,255,0.03));
  border-radius: var(--radius-sm);
}
.info-row {
  display: flex;
  gap: 12px;
  align-items: baseline;
}
.info-label {
  flex-shrink: 0;
  width: 90px;
  font-family: monospace;
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
}
.info-desc {
  font-size: 12px;
  color: var(--text-muted);
}
</style>
