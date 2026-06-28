<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">AI 舆情分析</h1>
    </div>

    <!-- ──────── 操作面板 ──────── -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">操作面板</span>
      </div>
      <div class="card-body">
        <div class="action-row">
          <button
            class="btn-primary"
            :disabled="collecting"
            @click="handleCollect"
          >{{ collecting ? '采集中...' : '采集新闻' }}</button>
          <button
            class="btn-primary"
            :disabled="analyzing"
            @click="handleAnalyze"
          >{{ analyzing ? '分析中...' : 'AI 分析' }}</button>
          <input
            v-model="marketContext"
            class="form-input context-input"
            type="text"
            placeholder="市场背景（可选，如：央行降准后次日）"
          />
        </div>
        <div v-if="actionMsg" class="action-banner" :class="actionOk ? 'banner-ok' : 'banner-err'">
          {{ actionMsg }}
        </div>
      </div>
    </div>

    <!-- ──────── 每日情绪查询 ──────── -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">每日情绪查询</span>
      </div>
      <div class="card-body">
        <div class="action-row">
          <input v-model="queryDate" class="form-input" type="date" />
          <input
            v-model="queryTag"
            class="form-input tag-input"
            type="text"
            placeholder="资产标签（可选，如 000300）"
          />
          <button class="btn-secondary" :disabled="queryLoading" @click="handleQuery">
            {{ queryLoading ? '查询中...' : '查询' }}
          </button>
        </div>

        <div v-if="queryLoading" class="loading">加载中...</div>
        <div v-else-if="sentimentRows.length === 0 && queried" class="empty">暂无数据</div>
        <div v-else-if="sentimentRows.length > 0" class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>资产标签</th>
                <th>平均情绪</th>
                <th>加权情绪</th>
                <th>关注度</th>
                <th>新闻数</th>
                <th>正面占比</th>
                <th>负面占比</th>
                <th>热门主题</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in sentimentRows" :key="row.asset_tag">
                <td><span class="code-mono">{{ row.asset_tag }}</span></td>
                <td>
                  <span :class="sentimentClass(row.avg_sentiment)">
                    {{ row.avg_sentiment.toFixed(3) }}
                  </span>
                </td>
                <td>
                  <span :class="sentimentClass(row.weighted_sentiment)">
                    {{ row.weighted_sentiment.toFixed(3) }}
                  </span>
                </td>
                <td class="text-muted">{{ row.total_attention.toFixed(1) }}</td>
                <td class="text-muted">{{ row.news_count }}</td>
                <td class="text-rise">{{ (row.positive_ratio * 100).toFixed(1) }}%</td>
                <td class="text-fall">{{ (row.negative_ratio * 100).toFixed(1) }}%</td>
                <td>
                  <span v-for="topic in row.top_topics" :key="topic" class="chip chip-topic">{{ topic }}</span>
                  <span v-if="row.top_topics.length === 0" class="text-muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ──────── 指数情绪趋势 ──────── -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">指数情绪趋势</span>
      </div>
      <div class="card-body">
        <div class="action-row">
          <select v-model="trendIndex" class="form-input select-input">
            <option v-for="idx in INDEX_OPTIONS" :key="idx.code" :value="idx.code">
              {{ idx.code }} {{ idx.name }}
            </option>
          </select>
          <div class="days-btns">
            <button
              v-for="d in [7, 14, 30, 60]"
              :key="d"
              class="btn-day"
              :class="{ active: trendDays === d }"
              @click="trendDays = d"
            >{{ d }}天</button>
          </div>
          <button class="btn-secondary" :disabled="trendLoading" @click="handleTrendQuery">
            {{ trendLoading ? '加载中...' : '查询' }}
          </button>
        </div>

        <div v-if="trendLoading" class="loading">加载中...</div>
        <div v-else-if="trendData.length === 0" class="chart-placeholder">暂无数据</div>
        <div v-else ref="trendChartEl" class="chart-container"></div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** AI 舆情分析主页面。
 *
 * 提供三大功能区域：
 * 1. 操作面板：手动触发新闻采集和 AI 分析
 * 2. 每日情绪查询：按日期查询情绪聚合数据
 * 3. 指数情绪趋势：查看指定指数近期情绪变化图表
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'

import { fetchActiveIndexes, fetchDailySentiment, fetchIndexSummary, triggerAnalyze, triggerCollect } from '../api/aiFactors'
import type { DailySentimentResponse } from '../types/api'

/** 指数选项（从后端动态加载，回退到预置列表） */
const INDEX_OPTIONS = ref<{ code: string; name: string }[]>([
  { code: '000300', name: '沪深300' },
  { code: '000905', name: '中证500' },
  { code: '000016', name: '上证50' },
  { code: '399006', name: '创业板指' },
  { code: '000688', name: '科创50' },
  { code: '000852', name: '中证1000' },
  { code: '399673', name: '创业板50' },
])

/** 获取今天的日期字符串（YYYY-MM-DD） */
function todayStr(): string {
  return new Date().toISOString().slice(0, 10)
}

// ---- 操作面板 ----

const collecting = ref(false)
const analyzing = ref(false)
const marketContext = ref('')
const actionMsg = ref('')
const actionOk = ref(true)

/** 显示操作结果消息，5 秒后自动清除 */
function showActionMsg(ok: boolean, msg: string) {
  actionOk.value = ok
  actionMsg.value = msg
  setTimeout(() => { actionMsg.value = '' }, 5000)
}

/** 触发新闻采集 */
async function handleCollect() {
  collecting.value = true
  try {
    const res = await triggerCollect()
    if (res.status === 'success') {
      showActionMsg(true, `采集完毕：采集 ${res.collected} 条，入库 ${res.saved} 条`)
    } else {
      showActionMsg(false, `采集失败：${res.error ?? '未知错误'}`)
    }
  } catch (e) {
    showActionMsg(false, `请求失败：${e instanceof Error ? e.message : '网络错误'}`)
  } finally {
    collecting.value = false
  }
}

/** 触发完整 AI 分析链路（异步模式） */
async function handleAnalyze() {
  analyzing.value = true
  try {
    const res = await triggerAnalyze(todayStr(), marketContext.value || undefined)
    if (res.status === 'accepted' && res.run_id) {
      showActionMsg(true, `任务已提交（run_id: ${res.run_id}），正在后台执行，可在「运行记录」页面查看进度`)
    } else if (res.status === 'rejected') {
      showActionMsg(false, `任务被拒绝：${res.error ?? '已有进行中的任务'}`)
    } else if (res.status === 'success') {
      const parts: string[] = []
      if (res.collected) parts.push(`采集 ${res.collected} 条`)
      if (res.saved) parts.push(`入库 ${res.saved} 条`)
      if (res.analyzed) parts.push(`分析 ${res.analyzed} 条`)
      if (res.aggregated) parts.push(`聚合 ${res.aggregated} 组`)
      showActionMsg(true, `分析完毕：${parts.join('，')}`)
    } else {
      showActionMsg(false, `分析失败：${res.error ?? '未知错误'}`)
    }
  } catch (e) {
    showActionMsg(false, `请求失败：${e instanceof Error ? e.message : '网络错误'}`)
  } finally {
    analyzing.value = false
  }
}

// ---- 每日情绪查询 ----

const queryDate = ref(todayStr())
const queryTag = ref('')
const queryLoading = ref(false)
const queried = ref(false)
const sentimentRows = ref<DailySentimentResponse[]>([])

/** 查询每日情绪数据 */
async function handleQuery() {
  queryLoading.value = true
  try {
    sentimentRows.value = await fetchDailySentiment(
      queryDate.value,
      queryTag.value || undefined,
    )
    queried.value = true
  } catch {
    sentimentRows.value = []
    queried.value = true
  } finally {
    queryLoading.value = false
  }
}

/** 根据情绪值返回 CSS class */
function sentimentClass(val: number): string {
  if (val > 0.15) return 'text-rise'
  if (val < -0.15) return 'text-fall'
  return 'text-muted'
}

// ---- 指数情绪趋势 ----

const trendIndex = ref('000300')
const trendDays = ref(7)
const trendLoading = ref(false)
const trendData = ref<DailySentimentResponse[]>([])
const trendChartEl = ref<HTMLElement | null>(null)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let trendChart: any = null

/** 查询指数情绪趋势数据 */
async function handleTrendQuery() {
  trendLoading.value = true
  try {
    trendData.value = await fetchIndexSummary(trendIndex.value, trendDays.value)
  } catch {
    trendData.value = []
  } finally {
    trendLoading.value = false
  }
}

/** 构建 ECharts 双轴图表配置 */
function buildTrendOption(data: DailySentimentResponse[]) {
  const dates = data.map((d) => d.trade_date)
  const sentiments = data.map((d) => d.avg_sentiment)
  const weighted = data.map((d) => d.weighted_sentiment)
  const counts = data.map((d) => d.news_count)

  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis' as const,
      backgroundColor: '#1e293b',
      borderColor: '#334155',
      textStyle: { color: '#f1f5f9', fontSize: 12 },
    },
    legend: {
      data: ['平均情绪', '加权情绪', '新闻数量'],
      textStyle: { color: '#94a3b8', fontSize: 12 },
      top: 4,
    },
    grid: { left: 60, right: 60, top: 40, bottom: 40 },
    xAxis: {
      type: 'category' as const,
      data: dates,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
    },
    yAxis: [
      {
        type: 'value' as const,
        name: '情绪分',
        nameTextStyle: { color: '#94a3b8', fontSize: 11 },
        min: -1,
        max: 1,
        splitLine: { lineStyle: { color: '#334155', type: 'dashed' as const } },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
      },
      {
        type: 'value' as const,
        name: '新闻数',
        nameTextStyle: { color: '#94a3b8', fontSize: 11 },
        splitLine: { show: false },
        axisLabel: { color: '#94a3b8', fontSize: 11 },
      },
    ],
    series: [
      {
        name: '平均情绪',
        type: 'line',
        data: sentiments,
        smooth: true,
        lineStyle: { color: '#3b82f6', width: 2 },
        symbol: 'circle',
        symbolSize: 4,
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#64748b', type: 'dashed' as const, width: 1 },
          label: { color: '#64748b', fontSize: 10 },
          data: [{ yAxis: 0 }],
        },
      },
      {
        name: '加权情绪',
        type: 'line',
        data: weighted,
        smooth: true,
        lineStyle: { color: '#f59e0b', width: 2, type: 'dashed' as const },
        symbol: 'diamond',
        symbolSize: 4,
      },
      {
        name: '新闻数量',
        type: 'bar',
        yAxisIndex: 1,
        data: counts,
        itemStyle: { color: 'rgba(59, 130, 246, 0.25)' },
      },
    ],
  }
}

/** 初始化/更新趋势图表 */
async function renderTrendChart() {
  if (!trendChartEl.value || trendData.value.length === 0) return

  const echarts = await import('echarts')
  trendChart?.dispose()
  trendChart = echarts.init(trendChartEl.value, null, { renderer: 'canvas' })
  trendChart.setOption(buildTrendOption(trendData.value))
}

/** 监听数据变化重新渲染图表 */
watch([trendData, trendChartEl], () => {
  nextTick(() => renderTrendChart())
}, { flush: 'post' })

// ---- 生命周期 ----

onMounted(async () => {
  // 页面加载时自动查询今天数据
  handleQuery()
  // 动态加载活跃指数列表
  try {
    const indexes = await fetchActiveIndexes()
    if (indexes.length > 0) {
      INDEX_OPTIONS.value = indexes.map((idx) => ({
        code: idx.index_code,
        name: idx.name_cn,
      }))
    }
  } catch {
    // 加载失败时保持预置列表
  }
})

onUnmounted(() => {
  trendChart?.dispose()
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
}

/* ---- 卡片 ---- */

.card {
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

.card-title {
  font-size: 14px;
  font-weight: 600;
}

.card-body {
  padding: 16px 20px;
}

/* ---- 操作行 ---- */

.action-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.context-input {
  flex: 1;
  min-width: 220px;
}

.tag-input {
  max-width: 200px;
}

.select-input {
  min-width: 180px;
}

/* ---- 天数快捷按钮 ---- */

.days-btns {
  display: flex;
  gap: 4px;
}

.btn-day {
  background: var(--surface-2);
  color: var(--text-muted);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.btn-day:hover {
  color: var(--text);
  border-color: var(--accent);
}

.btn-day.active {
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  border-color: var(--accent);
}

/* ---- 按钮 ---- */

.btn-primary {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.15s;
}

.btn-primary:hover {
  opacity: 0.85;
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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

.btn-secondary:hover {
  background: var(--surface-2);
  border-color: var(--accent);
}

.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ---- 表单 ---- */

.form-input {
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 7px 10px;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s;
  color-scheme: dark;
}

.form-input:focus {
  border-color: var(--accent);
}

/* ---- 状态消息 ---- */

.action-banner {
  margin-top: 12px;
  padding: 10px 16px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}

.banner-ok {
  background: rgba(34, 197, 94, 0.1);
  border: 1px solid rgba(34, 197, 94, 0.3);
  color: var(--success);
}

.banner-err {
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: var(--danger);
}

/* ---- 表格 ---- */

.table-wrap {
  margin-top: 12px;
  overflow-x: auto;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.data-table th {
  text-align: left;
  padding: 8px 10px;
  color: var(--text-muted);
  font-weight: 500;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.data-table td {
  padding: 8px 10px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}

.data-table tbody tr:hover {
  background: var(--surface-2);
}

/* ---- 主题标签 ---- */

.chip {
  display: inline-block;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 10px;
  margin: 2px 4px 2px 0;
}

.chip-topic {
  background: rgba(59, 130, 246, 0.12);
  color: var(--accent);
}

/* ---- 通用 ---- */

.code-mono {
  font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  font-size: 12px;
}

.text-rise {
  color: var(--success);
  font-weight: 600;
}

.text-fall {
  color: var(--danger);
  font-weight: 600;
}

.text-muted {
  color: var(--text-muted);
}

.loading {
  padding: 40px;
  text-align: center;
  color: var(--text-muted);
}

.empty {
  padding: 40px;
  text-align: center;
  color: var(--text-muted);
  margin-top: 12px;
}

.chart-placeholder {
  padding: 120px 60px;
  text-align: center;
  color: var(--text-muted);
}

.chart-container {
  width: 100%;
  height: 380px;
  margin-top: 12px;
}
</style>
