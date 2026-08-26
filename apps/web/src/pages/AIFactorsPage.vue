<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">AI 舆情分析</h1>
      <HelpTip text="基于大语言模型（LLM）的 A 股市场舆情分析模块。<br><br><b>数据来源</b>：11 个中文热榜平台（头条/百度/华尔街见闻等）+ 18 个中英文 RSS 财经源。<br><b>分析流程</b>：新闻采集 → LLM 情绪评分 → 标签分类（指数/行业/概念）→ 情绪聚合 → 市场研判。<br><b>定时调度</b>：每天 23:30 自动执行，覆盖当天新闻。" position="bottom" maxWidth="440px" /></div>

    <!-- ════════ 操作面板 ════════ -->
    <div class="card">
      <div class="card-header"><span class="card-title">操作面板</span><HelpTip text="<b>采集新闻</b>：从热榜（头条/百度/华尔街见闻等）和 18 个 RSS 源抓取当日财经新闻。<br><b>AI 分析</b>：异步任务，依次执行 LLM 情绪分析 → 标签分类 → 情绪聚合 → 市场研判，结果存入数据库。<br>系统每天 23:30 自动执行 AI 分析（无需手动触发）。" position="bottom" maxWidth="360px" /></div>
      <div class="card-body">
        <div class="action-row">
          <button class="btn-primary" :disabled="collecting" @click="handleCollect">
            {{ collecting ? '采集中...' : '采集新闻' }}
          </button>
          <button class="btn-primary" :disabled="analyzing" @click="handleAnalyze">
            {{ analyzing ? '分析中...' : 'AI 分析' }}
          </button>
        </div>
        <div v-if="actionMsg" class="action-banner" :class="actionOk ? 'banner-ok' : 'banner-err'">
          <span>{{ actionMsg }}</span>
          <router-link v-if="actionRunId" to="/runs" class="run-link">查看运行记录 →</router-link>
        </div>
      </div>
    </div>

    <!-- ════════ 每日情绪查询 ════════ -->
    <div class="card">
      <div class="card-header"><span class="card-title">每日情绪查询</span><HelpTip text="按交易日查询各资产标签的 AI 情绪聚合数据。<br><br><b>平均情绪</b>：所有相关新闻的 sentiment 简单均值。<br><b>加权情绪</b>：按关注度（热度/排名）加权，更能反映主流舆论。<br><b>正面/负面占比</b>：sentiment>&nbsp;0.15 为正面，<&nbsp;−0.15 为负面。<br><br>预警阈值可调：超出阈值的资产会在查询结果上方高亮提醒。" position="bottom" maxWidth="380px" /></div>
      <div class="card-body">
        <div class="action-row">
          <input v-model="queryDate" class="form-input" type="date" />
          <button class="btn-day" @click="goPrevTradingDay">上一交易日</button>
          <input v-model="queryTag" class="form-input tag-input" type="text" placeholder="资产标签（可选）" />
          <button class="btn-secondary" :disabled="queryLoading" @click="handleQuery">
            {{ queryLoading ? '查询中...' : '查询' }}
          </button>
        </div>

        <!-- 情绪异常预警 -->
        <div v-if="alertItems.length > 0" class="alert-row">
          <div v-for="a in alertItems" :key="a.tag" class="alert-item" :class="a.sentiment > 0 ? 'alert-positive' : 'alert-negative'">
            <span class="alert-icon">{{ a.sentiment > 0 ? '🔺' : '🔻' }}</span>
            <span class="alert-tag">{{ a.tag }}</span>
            <span>加权情绪 <strong>{{ a.sentiment.toFixed(2) }}</strong></span>
            <span>（{{ a.newsCount }} 条新闻）</span>
          </div>
        </div>
        <div class="alert-threshold-row">
          <label class="threshold-label">预警阈值：</label>
          <select v-model="alertThreshold" class="form-input threshold-select">
            <option :value="0.3">±0.3（敏感）</option>
            <option :value="0.5">±0.5（标准）</option>
            <option :value="0.7">±0.7（激进）</option>
          </select>
        </div>

        <div v-if="queryLoading" class="loading">加载中...</div>
        <div v-else-if="sentimentRows.length === 0 && queried" class="empty">暂无数据</div>
        <template v-else-if="sentimentRows.length > 0">
          <!-- 综合（_general / _other） -->
          <div v-if="specialRows.length > 0" class="section-group">
            <div class="section-title">综合</div>
            <div class="table-wrap">
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
                  <tr v-for="row in specialRows" :key="row.asset_tag">
                    <td><span class="code-mono">{{ row.asset_tag }}</span></td>
                    <td><span :class="sentimentClass(row.avg_sentiment)">{{ row.avg_sentiment.toFixed(3) }}</span></td>
                    <td><span :class="sentimentClass(row.weighted_sentiment)">{{ row.weighted_sentiment.toFixed(3) }}</span></td>
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

          <!-- 指数 -->
          <div v-if="indexRows.length > 0" class="section-group">
            <div class="section-title">指数</div>
            <div class="table-wrap">
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
                  <tr v-for="row in indexRows" :key="row.asset_tag">
                    <td><span class="code-mono">{{ row.asset_tag }}</span><span class="index-name">{{ indexNameMap[row.asset_tag] || '' }}</span></td>
                    <td><span :class="sentimentClass(row.avg_sentiment)">{{ row.avg_sentiment.toFixed(3) }}</span></td>
                    <td><span :class="sentimentClass(row.weighted_sentiment)">{{ row.weighted_sentiment.toFixed(3) }}</span></td>
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

          <!-- 行业主题 -->
          <div v-if="sectorRows.length > 0" class="section-group">
            <div class="section-title">行业主题</div>
            <div class="table-wrap">
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
                  <tr v-for="row in sectorRows" :key="row.asset_tag">
                    <td><span class="code-mono">{{ row.asset_tag }}</span></td>
                    <td><span :class="sentimentClass(row.avg_sentiment)">{{ row.avg_sentiment.toFixed(3) }}</span></td>
                    <td><span :class="sentimentClass(row.weighted_sentiment)">{{ row.weighted_sentiment.toFixed(3) }}</span></td>
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

          <!-- 主题分布柱状图 -->
          <div v-if="topicChartData.length > 0" ref="topicChartEl" class="chart-container chart-topic"></div>
        </template>
      </div>
    </div>

    <!-- ════════ 跨指数情绪对比 ════════ -->
    <div class="card">
      <div class="card-header"><span class="card-title">跨指数情绪对比</span><HelpTip text="选择 2–8 个指数，并列对比同一天的 AI 情绪指标。<br><br>通过分组柱状图直观比较：哪些指数情绪最积极/最消极，正面新闻占比高低。<br>情绪趋同可能预示系统性行情，情绪分化则反映结构性机会。<br><br>点击标签按钮选择指数，最少 2 个、最多 8 个。" position="bottom" maxWidth="380px" /></div>
      <div class="card-body">
        <div class="action-row">
          <select v-model="compareDate" class="form-input select-input">
            <option v-for="d in recentDates" :key="d" :value="d">{{ d }}</option>
          </select>
          <div class="multi-select-wrap">
            <button
              v-for="idx in INDEX_OPTIONS"
              :key="idx.code"
              class="btn-chip"
              :class="{ 'btn-chip-active': selectedIndexes.includes(idx.code) }"
              @click="toggleIndex(idx.code)"
            >{{ idx.code }} {{ idx.name }}</button>
          </div>
          <button class="btn-secondary" :disabled="compareLoading || selectedIndexes.length < 2" @click="handleCompare">
            {{ compareLoading ? '加载中...' : '对比' }}
          </button>
        </div>
        <div v-if="compareLoading" class="loading">加载中...</div>
        <div v-else-if="compareData.length === 0" class="chart-placeholder">选择 2 个以上指数后点击"对比"</div>
        <div v-else ref="compareChartEl" class="chart-container"></div>
      </div>
    </div>

    <!-- ════════ 市场综合研判 ════════ -->
    <div class="card">
      <div class="card-header"><span class="card-title">每日市场研判</span><HelpTip text="由 LLM（大语言模型）基于当日全市场 AI 情绪聚合数据自动生成的综合研判（200–300 字）。<br><br>包含三个方面：<br><b>正文</b>：当日市场情绪概况与核心主题<br><b>关键主题</b>：3–5 个最重要的话题方向<br><b>风险提示</b>：情绪极端值、分歧异常等需警惕的信号<br><br>每次 AI 分析完成后自动生成，LLM 不可用时跳过。" position="bottom" maxWidth="400px" /></div>
      <div class="card-body">
        <div class="action-row">
          <input v-model="synthesisDate" class="form-input" type="date" />
          <button class="btn-secondary" :disabled="synthesisLoading" @click="handleSynthesisQuery">
            {{ synthesisLoading ? '加载中...' : '查询' }}
          </button>
        </div>
        <div v-if="synthesisLoading" class="loading">加载中...</div>
        <div v-else-if="synthesisData === null && synthesisQueried" class="empty">
          <p>该日期暂无市场研判</p>
          <p class="empty-hint">请先触发 AI 分析生成研判</p>
        </div>
        <div v-else-if="synthesisData" class="synthesis-content">
          <div class="synthesis-body">{{ synthesisData.content }}</div>
          <div v-if="synthesisData.key_topics.length > 0" class="synthesis-topics">
            <span class="synthesis-label">关键主题：</span>
            <span v-for="t in synthesisData.key_topics" :key="t" class="chip chip-topic">{{ t }}</span>
          </div>
          <div v-if="synthesisData.risk_notes" class="synthesis-risk">
            <span class="synthesis-label">风险提示：</span>{{ synthesisData.risk_notes }}
          </div>
        </div>
      </div>
    </div>

    <!-- ════════ 指数情绪趋势 ════════ -->
    <div class="card">
      <div class="card-header"><span class="card-title">指数情绪趋势</span><HelpTip text="追踪单个指数在不同时间窗口内的 AI 情绪变化趋势。<br><br>折线图展示平均情绪和加权情绪的时序走势，柱状图叠加当日新闻数量。<br>可切换 7/14/30/60 天窗口，观察情绪动量变化和拐点信号。<br><br>情绪持续向上 → 市场信心增强；情绪高位回落 → 关注反转风险。" position="bottom" maxWidth="380px" /></div>
      <div class="card-body">
        <div class="action-row">
          <select v-model="trendIndex" class="form-input select-input">
            <option v-for="idx in INDEX_OPTIONS" :key="idx.code" :value="idx.code">{{ idx.code }} {{ idx.name }}</option>
          </select>
          <div class="days-btns">
            <button v-for="d in [7, 14, 30, 60]" :key="d" class="btn-day" :class="{ active: trendDays === d }" @click="trendDays = d">{{ d }}天</button>
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
 * 提供六大功能区域：
 * 1. 操作面板：手动触发新闻采集和 AI 分析（含异步任务链接）
 * 2. 每日情绪查询：按日期查询 + 情绪异常预警 + 主题分布柱状图 + 快捷日期
 * 3. 跨指数情绪对比：多选指数并列对比
 * 4. 市场综合研判：AI 生成的每日市场概况
 * 5. 指数情绪趋势：单指数时序情绪变化
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  fetchActiveIndexes,
  fetchDailySentiment,
  fetchIndexSummary,
  fetchLatestDataDate,
  fetchMarketSynthesis,
  triggerAnalyze,
  triggerCollect,
} from '../api/aiFactors'
import type { DailySentimentResponse, MarketSynthesisResponse } from '../types/api'
import HelpTip from '../components/HelpTip.vue'

/** 今天的日期字符串（YYYY-MM-DD） */
function todayStr(): string {
  return new Date().toISOString().slice(0, 10)
}

/** 日期偏移 helper */
function shiftDate(dateStr: string, days: number): string {
  const d = new Date(dateStr)
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

/** 最近 5 个交易日（用于对比日期选择器） */
const recentDates = ref<string[]>([])
function refreshRecentDates() {
  const today = new Date()
  const dates: string[] = []
  for (let i = 0; i < 5; i++) {
    const d = new Date(today)
    d.setDate(d.getDate() - i)
    dates.push(d.toISOString().slice(0, 10))
  }
  recentDates.value = dates
}

// ──── 指数选项 ────

const INDEX_OPTIONS = ref<{ code: string; name: string }[]>([
  { code: '000300', name: '沪深300' },
  { code: '000905', name: '中证500' },
  { code: '000016', name: '上证50' },
  { code: '399006', name: '创业板指' },
  { code: '000688', name: '科创50' },
  { code: '000852', name: '中证1000' },
  { code: '399673', name: '创业板50' },
])

// ──── 操作面板 ────

const collecting = ref(false)
const analyzing = ref(false)
const actionMsg = ref('')
const actionOk = ref(true)
const actionRunId = ref<string | null>(null)

function showActionMsg(ok: boolean, msg: string, runId?: string | null) {
  actionOk.value = ok
  actionMsg.value = msg
  actionRunId.value = runId ?? null
  setTimeout(() => { actionMsg.value = ''; actionRunId.value = null }, 8000)
}

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

async function handleAnalyze() {
  analyzing.value = true
  try {
    const res = await triggerAnalyze(todayStr())
    if (res.status === 'accepted' && res.run_id) {
      showActionMsg(
        true,
        `任务已提交（run_id: ${res.run_id}），正在后台执行 — 可跳转到运行记录页面查看进度`,
        res.run_id,
      )
    } else if (res.status === 'rejected') {
      showActionMsg(false, `任务被拒绝：${res.error ?? '已有进行中的任务'}`)
    } else if (res.status === 'success') {
      const parts: string[] = []
      if (res.collected) parts.push(`采集 ${res.collected} 条`)
      if (res.saved) parts.push(`入库 ${res.saved} 条`)
      if (res.analyzed) parts.push(`分析 ${res.analyzed} 条`)
      if (res.aggregated) parts.push(`聚合 ${res.aggregated} 组`)
      if (res.synthesis) parts.push('研判已生成')
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

// ──── 每日情绪查询 ────

const queryDate = ref(todayStr())
const queryTag = ref('')
const queryLoading = ref(false)
const queried = ref(false)
const sentimentRows = ref<DailySentimentResponse[]>([])

/** 情绪异常预警 */
const alertThreshold = ref(0.5)
const alertItems = computed(() =>
  sentimentRows.value.filter(
    (r) => Math.abs(r.weighted_sentiment) > alertThreshold.value,
  ).map((r) => ({
    tag: r.asset_tag,
    sentiment: r.weighted_sentiment,
    newsCount: r.news_count,
  })),
)

/** 判断资产标签类型。
 *
 * 根据标签值自动推断类型：
 * - _general / _other → 'special'（系统聚合桶）
 * - 6 位纯数字（如 000300）→ 'index'（指数代码）
 * - 其他（中文行业/概念标签）→ 'sector'
 */
function getTagType(assetTag: string): 'special' | 'index' | 'sector' {
  if (assetTag === '_general' || assetTag === '_other') return 'special'
  if (/^\d{6}$/.test(assetTag)) return 'index'
  return 'sector'
}

/** 指数代码 → 中文名称映射。 */
const indexNameMap = computed(() => {
  const map: Record<string, string> = {}
  for (const idx of INDEX_OPTIONS.value) {
    map[idx.code] = idx.name
  }
  return map
})

/** 综合标签行（_general / _other），固定在顶部。 */
const specialRows = computed(() =>
  sentimentRows.value.filter((r) => getTagType(r.asset_tag) === 'special'),
)

/** 指数类型行，按加权情绪降序排列。 */
const indexRows = computed(() =>
  sentimentRows.value
    .filter((r) => getTagType(r.asset_tag) === 'index')
    .sort((a, b) => b.weighted_sentiment - a.weighted_sentiment),
)

/** 行业主题类型行，按加权情绪降序排列。 */
const sectorRows = computed(() =>
  sentimentRows.value
    .filter((r) => getTagType(r.asset_tag) === 'sector')
    .sort((a, b) => b.weighted_sentiment - a.weighted_sentiment),
)

async function handleQuery() {
  queryLoading.value = true
  try {
    sentimentRows.value = await fetchDailySentiment(queryDate.value, queryTag.value || undefined)
    queried.value = true
  } catch {
    sentimentRows.value = []
    queried.value = true
  } finally {
    queryLoading.value = false
  }
}

/** 快捷日期：上一交易日 */
async function goPrevTradingDay() {
  try {
    const latest = await fetchLatestDataDate()
    if (latest) {
      queryDate.value = latest
    } else {
      // 回退：取昨天
      queryDate.value = shiftDate(todayStr(), -1)
    }
  } catch {
    queryDate.value = shiftDate(todayStr(), -1)
  }
  handleQuery()
}

function sentimentClass(val: number): string {
  if (val > 0.15) return 'text-rise'
  if (val < -0.15) return 'text-fall'
  return 'text-muted'
}

// ──── 主题分布柱状图 ────

const topicChartEl = ref<HTMLElement | null>(null)
let topicChart: unknown = null

const topicChartData = computed(() => {
  const counter: Record<string, number> = {}
  for (const row of sentimentRows.value) {
    for (const topic of row.top_topics) {
      counter[topic] = (counter[topic] || 0) + 1
    }
  }
  return Object.entries(counter)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 10)
})

function buildTopicOption(data: [string, number][]) {
  return {
    backgroundColor: 'transparent',
    title: { text: '热门主题分布', textStyle: { color: '#94a3b8', fontSize: 13 }, left: 0 },
    tooltip: { trigger: 'axis' as const, backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
    grid: { left: 100, right: 40, top: 30, bottom: 20 },
    xAxis: { type: 'value' as const, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    yAxis: {
      type: 'category' as const,
      data: data.map((d) => d[0]).reverse(),
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#e2e8f0', fontSize: 11 },
    },
    series: [{
      type: 'bar',
      data: data.map((d) => d[1]).reverse(),
      itemStyle: { color: 'rgba(59, 130, 246, 0.6)', borderRadius: [0, 2, 2, 0] },
      barWidth: 16,
    }],
  }
}

async function renderTopicChart() {
  if (!topicChartEl.value || topicChartData.value.length === 0) return
  const echarts = await import('echarts')
  ;(topicChart as { dispose?: () => void })?.dispose?.()
  topicChart = echarts.init(topicChartEl.value, null, { renderer: 'canvas' })
  ;(topicChart as { setOption?: (o: unknown) => void })?.setOption?.(buildTopicOption(topicChartData.value))
}

watch([topicChartData, topicChartEl], () => { nextTick(() => renderTopicChart()) }, { flush: 'post' })

// ──── 跨指数情绪对比 ────

const compareDate = ref(todayStr())
const selectedIndexes = ref<string[]>(['000300', '000905'])
const compareLoading = ref(false)
const compareData = ref<DailySentimentResponse[]>([])
const compareChartEl = ref<HTMLElement | null>(null)
let compareChart: unknown = null

function toggleIndex(code: string) {
  const idx = selectedIndexes.value.indexOf(code)
  if (idx >= 0) {
    if (selectedIndexes.value.length > 2) selectedIndexes.value.splice(idx, 1)
  } else {
    if (selectedIndexes.value.length < 8) selectedIndexes.value.push(code)
  }
}

async function handleCompare() {
  compareLoading.value = true
  try {
    const results: DailySentimentResponse[] = []
    for (const code of selectedIndexes.value) {
      const rows = await fetchDailySentiment(compareDate.value, code)
      results.push(...rows)
    }
    compareData.value = results
  } catch {
    compareData.value = []
  } finally {
    compareLoading.value = false
  }
}

function buildCompareOption(data: DailySentimentResponse[]) {
  const codes = [...new Set(data.map((d) => d.asset_tag))]
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' as const, backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
    legend: { data: ['平均情绪', '加权情绪', '正面占比'], textStyle: { color: '#94a3b8', fontSize: 12 }, top: 4 },
    grid: { left: 60, right: 30, top: 40, bottom: 50 },
    xAxis: {
      type: 'category' as const,
      data: codes,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#94a3b8', fontSize: 11, rotate: codes.length > 4 ? 30 : 0 },
    },
    yAxis: {
      type: 'value' as const,
      name: '情绪值 / 占比',
      nameTextStyle: { color: '#94a3b8', fontSize: 11 },
      splitLine: { lineStyle: { color: '#334155', type: 'dashed' as const } },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
    },
    series: [
      {
        name: '平均情绪', type: 'bar',
        data: codes.map((c) => {
          const r = data.find((d) => d.asset_tag === c)
          return { value: r?.avg_sentiment ?? 0, itemStyle: { color: (r?.avg_sentiment ?? 0) > 0 ? 'rgba(34,197,94,0.6)' : (r?.avg_sentiment ?? 0) < 0 ? 'rgba(239,68,68,0.6)' : 'rgba(148,163,184,0.4)' } }
        }),
      },
      {
        name: '加权情绪', type: 'bar',
        data: codes.map((c) => {
          const r = data.find((d) => d.asset_tag === c)
          return { value: r?.weighted_sentiment ?? 0, itemStyle: { color: (r?.weighted_sentiment ?? 0) > 0 ? 'rgba(34,197,94,0.8)' : (r?.weighted_sentiment ?? 0) < 0 ? 'rgba(239,68,68,0.8)' : 'rgba(148,163,184,0.5)' } }
        }),
      },
      {
        name: '正面占比', type: 'bar',
        data: codes.map((c) => {
          const r = data.find((d) => d.asset_tag === c)
          return { value: (r?.positive_ratio ?? 0) * 100, itemStyle: { color: 'rgba(59,130,246,0.5)' } }
        }),
      },
    ],
  }
}

async function renderCompareChart() {
  if (!compareChartEl.value || compareData.value.length === 0) return
  const echarts = await import('echarts')
  ;(compareChart as { dispose?: () => void })?.dispose?.()
  compareChart = echarts.init(compareChartEl.value, null, { renderer: 'canvas' })
  ;(compareChart as { setOption?: (o: unknown) => void })?.setOption?.(buildCompareOption(compareData.value))
}

watch([compareData, compareChartEl], () => { nextTick(() => renderCompareChart()) }, { flush: 'post' })

// ──── 市场综合研判 ────

const synthesisDate = ref(todayStr())
const synthesisLoading = ref(false)
const synthesisQueried = ref(false)
const synthesisData = ref<MarketSynthesisResponse | null>(null)

async function handleSynthesisQuery() {
  synthesisLoading.value = true
  try {
    synthesisData.value = await fetchMarketSynthesis(synthesisDate.value)
    synthesisQueried.value = true
  } catch {
    synthesisData.value = null
    synthesisQueried.value = true
  } finally {
    synthesisLoading.value = false
  }
}

// ──── 指数情绪趋势 ────

const trendIndex = ref('000300')
const trendDays = ref(7)
const trendLoading = ref(false)
const trendData = ref<DailySentimentResponse[]>([])
const trendChartEl = ref<HTMLElement | null>(null)
let trendChart: unknown = null

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

function buildTrendOption(data: DailySentimentResponse[]) {
  const dates = data.map((d) => d.trade_date)
  const sentiments = data.map((d) => d.avg_sentiment)
  const weighted = data.map((d) => d.weighted_sentiment)
  const counts = data.map((d) => d.news_count)
  return {
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' as const, backgroundColor: '#1e293b', borderColor: '#334155', textStyle: { color: '#f1f5f9', fontSize: 12 } },
    legend: { data: ['平均情绪', '加权情绪', '新闻数量'], textStyle: { color: '#94a3b8', fontSize: 12 }, top: 4 },
    grid: { left: 60, right: 60, top: 40, bottom: 40 },
    xAxis: { type: 'category' as const, data: dates, axisLine: { lineStyle: { color: '#334155' } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    yAxis: [
      { type: 'value' as const, name: '情绪分', nameTextStyle: { color: '#94a3b8', fontSize: 11 }, min: -1, max: 1, splitLine: { lineStyle: { color: '#334155', type: 'dashed' as const } }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
      { type: 'value' as const, name: '新闻数', nameTextStyle: { color: '#94a3b8', fontSize: 11 }, splitLine: { show: false }, axisLabel: { color: '#94a3b8', fontSize: 11 } },
    ],
    series: [
      { name: '平均情绪', type: 'line', data: sentiments, smooth: true, lineStyle: { color: '#3b82f6', width: 2 }, symbol: 'circle', symbolSize: 4, markLine: { silent: true, symbol: 'none', lineStyle: { color: '#64748b', type: 'dashed' as const, width: 1 }, label: { color: '#64748b', fontSize: 10 }, data: [{ yAxis: 0 }] } },
      { name: '加权情绪', type: 'line', data: weighted, smooth: true, lineStyle: { color: '#f59e0b', width: 2, type: 'dashed' as const }, symbol: 'diamond', symbolSize: 4 },
      { name: '新闻数量', type: 'bar', yAxisIndex: 1, data: counts, itemStyle: { color: 'rgba(59, 130, 246, 0.25)' } },
    ],
  }
}

async function renderTrendChart() {
  if (!trendChartEl.value || trendData.value.length === 0) return
  const echarts = await import('echarts')
  ;(trendChart as { dispose?: () => void })?.dispose?.()
  trendChart = echarts.init(trendChartEl.value, null, { renderer: 'canvas' })
  ;(trendChart as { setOption?: (o: unknown) => void })?.setOption?.(buildTrendOption(trendData.value))
}

watch([trendData, trendChartEl], () => { nextTick(() => renderTrendChart()) }, { flush: 'post' })

// ──── 生命周期 ────

onMounted(async () => {
  refreshRecentDates()
  handleQuery()
  try {
    const indexes = await fetchActiveIndexes()
    if (indexes.length > 0) {
      INDEX_OPTIONS.value = indexes.map((idx) => ({ code: idx.index_code, name: idx.name_cn }))
    }
  } catch { /* 保持预置列表 */ }
})

onUnmounted(() => {
  (trendChart as { dispose?: () => void })?.dispose?.()
  ;(topicChart as { dispose?: () => void })?.dispose?.()
  ;(compareChart as { dispose?: () => void })?.dispose?.()
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 24px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }

/* ── 卡片 ── */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.card-header { display: flex; align-items: center; gap: 10px; padding: 14px 20px; border-bottom: 1px solid var(--border); }
.card-title { font-size: 14px; font-weight: 600; }
.card-body { padding: 16px 20px; }

/* ── 操作行 ── */
.action-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tag-input { max-width: 200px; }
.select-input { min-width: 180px; }
.threshold-label { font-size: 12px; color: var(--text-muted); }
.threshold-select { min-width: 120px; }

/* ── 情绪预警 ── */
.alert-threshold-row { margin-top: 10px; display: flex; align-items: center; gap: 6px; }
.alert-row { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.alert-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: var(--radius-sm); font-size: 13px; font-weight: 500; }
.alert-positive { background: rgba(34, 197, 94, 0.1); border: 1px solid rgba(34, 197, 94, 0.3); color: var(--success); }
.alert-negative { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); color: var(--danger); }
.alert-icon { font-size: 16px; }
.alert-tag { font-family: 'SF Mono', monospace; font-weight: 700; min-width: 80px; }

/* ── 跨指数选择 ── */
.multi-select-wrap { display: flex; gap: 4px; flex-wrap: wrap; }
.btn-chip { background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); border-radius: 14px; padding: 4px 10px; font-size: 11px; cursor: pointer; transition: all .15s; }
.btn-chip:hover { border-color: var(--accent); color: var(--text); }
.btn-chip-active { background: rgba(59, 130, 246, 0.15); color: var(--accent); border-color: var(--accent); font-weight: 600; }

/* ── 天数按钮 ── */
.days-btns { display: flex; gap: 4px; }
.btn-day { background: var(--surface-2); color: var(--text-muted); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px 10px; font-size: 12px; font-weight: 500; cursor: pointer; transition: all .15s; }
.btn-day:hover { color: var(--text); border-color: var(--accent); }
.btn-day.active { background: rgba(59, 130, 246, 0.15); color: var(--accent); border-color: var(--accent); }

/* ── 按钮 ── */
.btn-primary { background: var(--accent); color: #fff; border: none; border-radius: var(--radius-sm); padding: 8px 16px; font-size: 13px; font-weight: 500; cursor: pointer; transition: opacity .15s; }
.btn-primary:hover { opacity: .85; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-secondary { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 14px; font-size: 13px; font-weight: 500; cursor: pointer; transition: all .15s; }
.btn-secondary:hover { background: var(--surface-2); border-color: var(--accent); }
.btn-secondary:disabled { opacity: .5; cursor: not-allowed; }

/* ── 表单 ── */
.form-input { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 7px 10px; font-size: 13px; outline: none; transition: border-color .15s; color-scheme: dark; }
.form-input:focus { border-color: var(--accent); }

/* ── 状态消息 ── */
.action-banner { margin-top: 12px; padding: 10px 16px; border-radius: var(--radius-sm); font-size: 13px; display: flex; align-items: center; gap: 16px; }
.banner-ok { background: rgba(34, 197, 94, 0.1); border: 1px solid rgba(34, 197, 94, 0.3); color: var(--success); }
.banner-err { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); color: var(--danger); }
.run-link { color: var(--accent); text-decoration: underline; font-weight: 500; white-space: nowrap; }

/* ── 表格 ── */
.section-group { margin-top: 12px; }
.section-group + .section-group { margin-top: 20px; }
.section-title { font-size: 13px; font-weight: 600; color: var(--accent); padding: 6px 0; border-bottom: 2px solid var(--border); margin-bottom: 8px; }

.table-wrap { margin-top: 0; overflow-x: auto; }
.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th { text-align: left; padding: 8px 10px; color: var(--text-muted); font-weight: 500; border-bottom: 1px solid var(--border); white-space: nowrap; }
.data-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.data-table tbody tr:hover { background: var(--surface-2); }

/* ── 标签 ── */
.chip { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px; margin: 2px 4px 2px 0; }
.chip-topic { background: rgba(59, 130, 246, 0.12); color: var(--accent); }

/* ── 市场研判 ── */
.synthesis-content { display: flex; flex-direction: column; gap: 14px; margin-top: 12px; }
.synthesis-body { font-size: 14px; line-height: 1.8; color: var(--text); white-space: pre-line; padding: 16px; background: var(--surface-2); border-radius: var(--radius-sm); border: 1px solid var(--border); }
.synthesis-topics, .synthesis-risk { font-size: 13px; }
.synthesis-label { font-weight: 600; color: var(--text-muted); margin-right: 8px; }
.synthesis-risk { padding: 10px 14px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: var(--radius-sm); color: #fbbf24; }
.empty-hint { font-size: 12px; color: var(--text-muted); margin-top: 4px; }

/* ── 通用 ── */
.code-mono { font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace; font-size: 12px; }
.index-name { color: var(--text-muted); font-size: 12px; margin-left: 6px; }
.text-rise { color: var(--success); font-weight: 600; }
.text-fall { color: var(--danger); font-weight: 600; }
.text-muted { color: var(--text-muted); }
.loading { padding: 40px; text-align: center; color: var(--text-muted); }
.empty { padding: 40px; text-align: center; color: var(--text-muted); margin-top: 12px; }
.chart-placeholder { padding: 120px 60px; text-align: center; color: var(--text-muted); }
.chart-container { width: 100%; height: 380px; margin-top: 12px; }
.chart-topic { height: 250px; }
</style>
