<template>
  <div class="page">
    <div class="page-header">
      <div class="header-main">
        <h1 class="page-title">RRG / 扩散调试台（临时）</h1>
        <span class="code-badge">TEMP</span>
      </div>
      <p class="factor-desc">
        申万一级行业 RRG 与数量占比扩散因子调试页面：修改参数后查看因子与轮动结果，
        仅供研究验证，正式页面后续另行设计。
      </p>
    </div>

    <!-- 参数面板 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">参数</span>
        <div class="controls">
          <button class="query-btn" :disabled="loading" @click="runAll">
            {{ loading ? '计算中...' : '查询' }}
          </button>
        </div>
      </div>
      <div class="lab-form">
        <div class="form-row">
          <label>日期范围</label>
          <input type="date" class="date-input" v-model="startDate" />
          <span class="sep">~</span>
          <input type="date" class="date-input" v-model="endDate" />
        </div>
        <div class="form-row">
          <label>RRG 参数</label>
          <input type="number" class="num-input" v-model.number="lookbackRatio" min="1" />
          <span class="field-hint">ratio 回看</span>
          <input type="number" class="num-input" v-model.number="lookbackMom" min="1" />
          <span class="field-hint">momentum 回看</span>
          <input type="number" class="num-input" v-model.number="smoothWindow" min="1" />
          <span class="field-hint">平滑</span>
        </div>
        <div class="form-row">
          <label>扩散参数</label>
          <input type="number" class="num-input" v-model.number="diffusionLookback" min="1" />
          <span class="field-hint">上涨回看</span>
        </div>
        <div class="form-row">
          <label>信号</label>
          <select v-model="signal" class="select-input">
            <option value="quadrant">A 纯 RRG 象限</option>
            <option value="diffusion">B 纯扩散</option>
            <option value="diffusion_rrg">C 扩散 + RRG（复刻）</option>
          </select>
          <input type="number" class="num-input" v-model.number="topN" min="1" />
          <span class="field-hint">top_n</span>
          <span v-for="q in [1, 2, 3, 4]" :key="q" class="quad-check">
            <input type="checkbox" :value="q" v-model="keepQuadrants" />
            象限{{ q }}
          </span>
        </div>
        <div class="form-row wrap">
          <label>行业范围</label>
          <span v-for="item in industries" :key="item.industry_code" class="ind-check">
            <input type="checkbox" :value="item.industry_code" v-model="selectedCodes" />
            {{ item.name_cn }}
          </span>
        </div>
      </div>
    </div>

    <!-- RRG 散点 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">RRG 最新日散点（中心 100/100）</span>
        <span v-if="rrgDate" class="card-subtitle">{{ rrgDate }}</span>
      </div>
      <div v-if="rrgLoading" class="empty">加载中...</div>
      <div v-else-if="!rrgPoints.length" class="empty">暂无 RRG 数据（检查日期与 warm-up）</div>
      <div v-else ref="rrgChartEl" class="chart-container" style="height: 420px"></div>
    </div>

    <!-- 扩散排名 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">扩散指标最新排名</span>
        <span v-if="diffusionDate" class="card-subtitle">{{ diffusionDate }}</span>
      </div>
      <div v-if="diffusionLoading" class="empty">加载中...</div>
      <div v-else-if="!diffusionRanking.length" class="empty">暂无扩散数据</div>
      <table v-else class="data-table">
        <thead>
          <tr><th>排名</th><th>行业</th><th>扩散值</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, idx) in diffusionRanking" :key="row.code">
            <td>{{ idx + 1 }}</td>
            <td>{{ row.name }}（{{ row.code }}）</td>
            <td class="mono">{{ row.value.toFixed(4) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 轮动选择 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">轮动选择（月末决策）</span>
      </div>
      <div v-if="rotationLoading" class="empty">加载中...</div>
      <div v-else-if="!selections.length" class="empty">暂无轮动结果</div>
      <table v-else class="data-table">
        <thead><tr><th>决策日</th><th>选中行业</th><th>权重</th></tr></thead>
        <tbody>
          <tr v-for="sel in selections" :key="sel.trade_date">
            <td class="mono">{{ sel.trade_date }}</td>
            <td>{{ displayNames(sel.selected_codes).join('、') || '空仓' }}</td>
            <td class="mono">{{ Object.values(sel.weights).map(w => w.toFixed(2)).join(' / ') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * RRG / 扩散因子临时调试页面。
 *
 * 允许修改回看/平滑/top_n/象限等参数，并直观查看申万一级行业的 RRG 散点、
 * 扩散排名与轮动选择结果。页面与接口均为临时验证用途。
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import {
  fetchDiffusion,
  fetchIndustryIndexes,
  fetchRotationSelections,
  fetchRRG,
  type IndustryDiffusionPoint,
  type IndustryIndexSummary,
  type IndustryRRGPoint,
  type IndustryRotationSelection,
} from '../api/industry'

const industries = ref<IndustryIndexSummary[]>([])
const selectedCodes = ref<string[]>([])
const startDate = ref('')
const endDate = ref('')
const lookbackRatio = ref(220)
const lookbackMom = ref(60)
const smoothWindow = ref(20)
const diffusionLookback = ref(220)
const signal = ref<'quadrant' | 'diffusion' | 'diffusion_rrg'>('diffusion_rrg')
const topN = ref(6)
const keepQuadrants = ref<number[]>([1, 2])

const rrgPoints = ref<IndustryRRGPoint[]>([])
const rrgDate = ref('')
const rrgLoading = ref(false)
const rrgChartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let rrgChartInstance: any = null

const diffusionPoints = ref<IndustryDiffusionPoint[]>([])
const diffusionDate = ref('')
const diffusionLoading = ref(false)
const selections = ref<IndustryRotationSelection[]>([])
const rotationLoading = ref(false)
const loading = computed(() => rrgLoading.value || diffusionLoading.value || rotationLoading.value)

const codeToName = computed(() => {
  const map: Record<string, string> = {}
  for (const item of industries.value) map[item.industry_code] = item.name_cn
  return map
})

/** 最新一日 RRG 点（仅取非空数据） */
const latestRRG = computed(() => {
  if (!rrgPoints.value.length) return []
  const byDate: Record<string, IndustryRRGPoint[]> = {}
  for (const p of rrgPoints.value) {
    if (p.rs_ratio !== null && p.rs_momentum !== null) {
      ;(byDate[p.trade_date] ??= []).push(p)
    }
  }
  const dates = Object.keys(byDate).sort()
  return dates.length ? byDate[dates[dates.length - 1]] : []
})

/** 最新一日扩散排名（降序） */
const diffusionRanking = computed(() => {
  if (!diffusionPoints.value.length) return []
  const byDate: Record<string, IndustryDiffusionPoint[]> = {}
  for (const p of diffusionPoints.value) {
    if (p.value !== null) {
      ;(byDate[p.trade_date] ??= []).push(p)
    }
  }
  const dates = Object.keys(byDate).sort()
  if (!dates.length) return []
  diffusionDate.value = dates[dates.length - 1]
  return byDate[dates[dates.length - 1]]
    .slice()
    .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
    .map((p) => ({ code: p.industry_code, name: p.name_cn || p.industry_code, value: p.value ?? 0 }))
})

/** 展示行业名称列表 */
function displayNames(codes: string[]): string[] {
  return codes.map((code) => codeToName.value[code] ?? code)
}

/** 默认时间范围：近 3 年，保证 RRG warm-up 有足够历史 */
function initDates(): void {
  const end = new Date()
  const start = new Date()
  start.setFullYear(start.getFullYear() - 3)
  endDate.value = end.toISOString().slice(0, 10)
  startDate.value = start.toISOString().slice(0, 10)
}

/** 渲染 RRG 散点图 */
async function renderRRG(): Promise<void> {
  if (!rrgChartEl.value || !latestRRG.value.length) return
  const echarts = await import('echarts')
  rrgChartInstance?.dispose()
  rrgChartInstance = echarts.init(rrgChartEl.value, undefined, { renderer: 'canvas' })
  const points = latestRRG.value
  const quadColors = ['', '#f97316', '#10b981', '#3b82f6', '#ef4444']
  const series = points
    .filter((p) => p.rs_ratio !== null && p.rs_momentum !== null)
    .map((p) => ({
      name: p.name_cn || p.industry_code,
      value: [p.rs_ratio, p.rs_momentum],
      symbolSize: 14,
      itemStyle: { color: quadColors[p.quadrant ?? 0] || '#94a3b8' },
    }))
  rrgChartInstance.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      formatter: (params: { data: { name: string; value: number[] } }) =>
        `${params.data.name}<br/>RS-Ratio: ${params.data.value[0].toFixed(2)}<br/>RS-Momentum: ${params.data.value[1].toFixed(2)}`,
    },
    xAxis: {
      name: 'RS-Ratio',
      type: 'value',
      min: (v: { min: number }) => Math.min(v.min, 100),
      max: (v: { max: number }) => Math.max(v.max, 100),
    },
    yAxis: {
      name: 'RS-Momentum',
      type: 'value',
      min: (v: { min: number }) => Math.min(v.min, 100),
      max: (v: { max: number }) => Math.max(v.max, 100),
    },
    series: [
      {
        type: 'scatter',
        data: series,
        label: {
          show: true,
          position: 'top',
          formatter: (params: { data: { name: string } }) => params.data.name,
          fontSize: 10,
        },
      },
    ],
  })
}

/** 拉取行业目录并初始化默认范围 */
async function loadIndustries(): Promise<void> {
  try {
    industries.value = await fetchIndustryIndexes()
    selectedCodes.value = industries.value.map((item) => item.industry_code)
  } catch {
    industries.value = []
  }
}

/** 查询 RRG */
async function loadRRG(): Promise<void> {
  rrgLoading.value = true
  try {
    rrgPoints.value = await fetchRRG(startDate.value, endDate.value, selectedCodes.value, {
      lookbackRatio: lookbackRatio.value,
      lookbackMom: lookbackMom.value,
      smoothWindow: smoothWindow.value,
    })
    const last = latestRRG.value
    rrgDate.value = last.length ? last[0].trade_date : ''
    await nextTick()
    await renderRRG()
  } catch {
    rrgPoints.value = []
  } finally {
    rrgLoading.value = false
  }
}

/** 查询扩散 */
async function loadDiffusion(): Promise<void> {
  diffusionLoading.value = true
  try {
    diffusionPoints.value = await fetchDiffusion(
      startDate.value,
      endDate.value,
      selectedCodes.value,
      {
        diffusionLookback: diffusionLookback.value,
        smoothWindow: smoothWindow.value,
      },
    )
  } catch {
    diffusionPoints.value = []
  } finally {
    diffusionLoading.value = false
  }
}

/** 查询轮动选择 */
async function loadRotation(): Promise<void> {
  rotationLoading.value = true
  try {
    selections.value = await fetchRotationSelections(
      startDate.value,
      endDate.value,
      selectedCodes.value,
      {
        signal: signal.value,
        topN: topN.value,
        keepQuadrants: keepQuadrants.value,
        lookbackRatio: lookbackRatio.value,
        lookbackMom: lookbackMom.value,
        smoothWindow: smoothWindow.value,
        diffusionLookback: diffusionLookback.value,
      },
    )
  } catch {
    selections.value = []
  } finally {
    rotationLoading.value = false
  }
}

/** 运行全部查询 */
async function runAll(): Promise<void> {
  await Promise.all([loadRRG(), loadDiffusion(), loadRotation()])
}

function resizeCharts(): void {
  rrgChartInstance?.resize()
}

watch(
  rrgPoints,
  () => {
    nextTick(() => renderRRG())
  },
)

onMounted(() => {
  initDates()
  loadIndustries()
  window.addEventListener('resize', resizeCharts)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  rrgChartInstance?.dispose()
})
</script>

<style scoped>
.lab-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}
.form-row.wrap {
  flex-wrap: wrap;
}
.form-row label {
  min-width: 64px;
  color: #94a3b8;
  font-size: 13px;
}
.num-input,
.select-input {
  width: 96px;
}
.field-hint {
  color: #64748b;
  font-size: 12px;
  margin-right: 6px;
}
.quad-check,
.ind-check {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: #cbd5e1;
}
.sep {
  color: #64748b;
}
</style>
