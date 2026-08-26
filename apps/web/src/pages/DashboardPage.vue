<template>
  <div class="dashboard">
    <!-- 背景纹理层 -->
    <div class="dashboard-bg"></div>

    <!-- 页头：标题 + 最新交易日 + 数据库连接指示灯 + 操作按钮 -->
    <header class="page-header animate-in">
      <div class="header-left">
        <div class="header-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>
          </svg>
        </div>
        <div class="header-text">
          <h1 class="page-title">研究总览</h1>
          <span class="page-subtitle">
            {{ systemStatus?.latest_trade_date ? '最新交易日 ' + systemStatus?.latest_trade_date : '加载中...' }}
          </span>
        </div>
        <span
          class="connection-dot"
          :class="systemStatus?.db_connected ? 'connected' : 'disconnected'"
          :title="systemStatus?.db_connected ? '数据库已连接' : '数据库连接异常'"
        ></span>
      </div>
      <div class="header-actions">
        <button class="btn btn-ghost" :disabled="triggeringColdStart" @click="triggerColdStartFn">
          <span class="btn-icon">↻</span>
          {{ triggeringColdStart ? '执行中...' : '历史回补' }}
        </button>
        <button class="btn btn-primary" :disabled="triggering" @click="triggerIngest">
          <span class="btn-icon">⚡</span>
          {{ triggering ? '触发中...' : '数据摄取' }}
        </button>
      </div>
    </header>

    <!-- 加载 / 错误状态 -->
    <div v-if="statusLoading" class="loading-state animate-in stagger-1">
      <div class="loading-spinner"></div>
      <span>加载系统状态...</span>
    </div>
    <div v-else-if="error" class="error-banner animate-in stagger-1">
      <span class="error-icon">⚠</span>
      {{ error }}
    </div>

    <!-- 数据概览 stat grid -->
    <div class="stat-grid animate-in stagger-1">
      <div class="stat-card">
        <div class="stat-accent"></div>
        <div class="stat-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <rect x="2" y="3" width="20" height="14" rx="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line>
          </svg>
        </div>
        <div class="stat-label">指数总数</div>
        <div class="stat-value">{{ systemStatus?.active_index_count ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-accent accent-green"></div>
        <div class="stat-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <path d="M12 20V10M18 20V4M6 20v-4"></path>
          </svg>
        </div>
        <div class="stat-label">活跃策略</div>
        <div class="stat-value">{{ strategyStore.items.length || '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-accent accent-amber"></div>
        <div class="stat-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <rect x="3" y="4" width="18" height="18" rx="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line>
          </svg>
        </div>
        <div class="stat-label">最新交易日</div>
        <div class="stat-value">{{ systemStatus?.latest_trade_date ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-accent accent-purple"></div>
        <div class="stat-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            <circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline>
          </svg>
        </div>
        <div class="stat-label">数据频率</div>
        <div class="stat-value">{{ systemStatus?.frequency || 'daily' }}</div>
      </div>
    </div>

    <!-- 关注策略：星标策略当日执行摘要 -->
    <section class="section animate-in stagger-2">
      <div class="section-header">
        <h2 class="section-title">关注策略</h2>
        <div class="section-header-right">
          <span v-if="starredLoading" class="section-badge">加载中...</span>
          <span v-else-if="starredSummary" class="section-badge">{{ starredSummary.items.length }} 个星标</span>
        </div>
      </div>
      <div v-if="starredLoading" class="loading-state">
        <div class="loading-spinner"></div>
        <span>加载星标策略...</span>
      </div>
      <div v-else-if="starredSummary?.items.length" class="starred-grid">
        <div v-for="(item, idx) in starredSummary.items" :key="item.strategy_id" class="starred-card" :style="{ animationDelay: (0.05 * idx) + 's' }">
          <!-- 卡片头部：策略名称 + 星标按钮 -->
          <div class="starred-card-header">
            <RouterLink :to="`/strategies/${item.strategy_id}`" class="starred-name">
              {{ item.display_name }}
            </RouterLink>
            <button
              class="star-btn-small starred"
              title="取消星标"
              @click.stop="handleUnstar(item.strategy_id)"
            >★</button>
          </div>

          <!-- 调仓状态 -->
          <div class="starred-row">
            <span :class="['rebalance-badge', item.is_rebalance_day ? 'rebalance-yes' : 'rebalance-no']">
              {{ item.is_rebalance_day ? '今日调仓' : '非调仓日' }}
            </span>
            <span v-if="!item.is_rebalance_day" class="rebalance-detail">
              {{ formatRebalanceLabel(item) }}
            </span>
            <span v-if="item.data_date" class="exec-date">
              数据截止 {{ formatExecDate(item.data_date) }}
            </span>
          </div>

          <!-- 择时信号 -->
          <div v-if="item.timing?.regime" class="starred-row">
            <span class="starred-label">择时</span>
            <span :class="['regime-badge', `regime-${item.timing.regime}`]">
              {{ formatRegime(item.timing.regime) }}
            </span>
            <span class="regime-confidence">{{ item.timing.confidence?.toFixed(0) }}%</span>
          </div>

          <!-- 仓位概览 -->
          <div v-if="Object.keys(item.plan?.positions || {}).length > 0" class="starred-row">
            <span class="starred-label">持仓</span>
            <div class="position-tags">
              <span
                v-for="[code, weight] in topPositions(item.plan.positions)"
                :key="code"
                class="position-tag"
                :title="code"
              >
                {{ getIndexName(item.rankings, code) || code }} <strong>{{ (weight * 100).toFixed(0) }}%</strong>
              </span>
              <span
                v-if="Object.keys(item.plan.positions).length > 5"
                class="position-more"
              >+{{ Object.keys(item.plan.positions).length - 5 }}</span>
            </div>
          </div>

          <!-- 总仓位进度条 -->
          <div v-if="item.plan?.total_exposure != null" class="starred-row">
            <span class="starred-label">总仓位</span>
            <div class="exposure-bar-bg">
              <div
                class="exposure-bar"
                :style="{ width: (item.plan.total_exposure * 100).toFixed(0) + '%' }"
              ></div>
            </div>
            <span class="exposure-pct">{{ (item.plan.total_exposure * 100).toFixed(0) }}%</span>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">
        <span class="empty-icon">☆</span>
        <span>暂无关注策略，前往 <RouterLink to="/strategies" class="link-accent">策略中心</RouterLink> 添加关注</span>
      </div>
    </section>

    <!-- AI 舆情概览 -->
    <section class="section animate-in stagger-3">
      <div class="section-header">
        <h2 class="section-title">AI 舆情概览</h2>
        <div class="section-header-right">
          <span v-if="sentimentLoading" class="section-badge">加载中...</span>
          <span v-else-if="sentimentDate" class="section-badge">{{ sentimentDate }}</span>
        </div>
        <RouterLink to="/ai-factors" class="link-accent section-link">完整报告 →</RouterLink>
      </div>

      <div v-if="sentimentLoading" class="loading-state">
        <div class="loading-spinner"></div>
        <span>加载 AI 舆情数据...</span>
      </div>
      <div v-else-if="!sentimentDate || sentimentRows.length === 0" class="empty-state">
        <span class="empty-icon">📡</span>
        <span>暂无 AI 舆情数据</span>
      </div>
      <template v-else>
        <!-- 三列排名 -->
        <div class="sentiment-columns">
          <!-- 情绪前三（加权情绪降序） -->
          <div class="sentiment-col sentiment-col-positive">
            <div class="sentiment-col-title">
              <span class="col-icon">🟢</span>
              情绪前三
            </div>
            <div v-if="topPositive.length === 0" class="empty-sm">—</div>
            <div
              v-for="(row, idx) in topPositive"
              :key="row.asset_tag"
              class="sentiment-row"
              :class="{ 'sentiment-row-expanded': expandedTag === row.asset_tag }"
              :style="{ animationDelay: (0.06 * idx) + 's' }"
              @click="toggleTagExpand(row.asset_tag)"
            >
              <div class="sentiment-row-main">
                <span class="sentiment-tag">{{ row.asset_tag }}</span>
                <span class="sentiment-val sentiment-positive">{{ row.weighted_sentiment.toFixed(2) }}</span>
                <span class="sentiment-expand">{{ expandedTag === row.asset_tag ? '▲' : '▼' }}</span>
              </div>
              <!-- 展开新闻列表 -->
              <div v-if="expandedTag === row.asset_tag" class="sentiment-news-list">
                <div v-if="loadingTag === row.asset_tag" class="loading-sm">加载中...</div>
                <div v-else-if="(tagNewsMap[row.asset_tag] || []).length === 0" class="empty-sm">暂无新闻明细</div>
                <a
                  v-for="news in tagNewsMap[row.asset_tag] || []"
                  :key="news.title"
                  :href="news.url || '#'"
                  target="_blank"
                  class="sentiment-news-item"
                  :class="news.sentiment_score > 0.15 ? 'news-positive' : news.sentiment_score < -0.15 ? 'news-negative' : 'news-neutral'"
                >
                  <span class="news-dot"></span>
                  <span class="news-title">{{ news.title }}</span>
                  <span class="news-source">{{ news.source_name }}</span>
                </a>
              </div>
            </div>
          </div>

          <!-- 情绪倒三（加权情绪升序，末三位） -->
          <div class="sentiment-col sentiment-col-bottom">
            <div class="sentiment-col-title">
              <span class="col-icon">🔵</span>
              情绪倒三
            </div>
            <div v-if="topNegative.length === 0" class="empty-sm">—</div>
            <div
              v-for="(row, idx) in topNegative"
              :key="row.asset_tag"
              class="sentiment-row"
              :class="{ 'sentiment-row-expanded': expandedTag === row.asset_tag }"
              :style="{ animationDelay: (0.06 * idx) + 's' }"
              @click="toggleTagExpand(row.asset_tag)"
            >
              <div class="sentiment-row-main">
                <span class="sentiment-tag">{{ row.asset_tag }}</span>
                <span class="sentiment-val sentiment-negative">{{ row.weighted_sentiment.toFixed(2) }}</span>
                <span class="sentiment-expand">{{ expandedTag === row.asset_tag ? '▲' : '▼' }}</span>
              </div>
              <div v-if="expandedTag === row.asset_tag" class="sentiment-news-list">
                <div v-if="loadingTag === row.asset_tag" class="loading-sm">加载中...</div>
                <div v-else-if="(tagNewsMap[row.asset_tag] || []).length === 0" class="empty-sm">暂无新闻明细</div>
                <a
                  v-for="news in tagNewsMap[row.asset_tag] || []"
                  :key="news.title"
                  :href="news.url || '#'"
                  target="_blank"
                  class="sentiment-news-item"
                  :class="news.sentiment_score > 0.15 ? 'news-positive' : news.sentiment_score < -0.15 ? 'news-negative' : 'news-neutral'"
                >
                  <span class="news-dot"></span>
                  <span class="news-title">{{ news.title }}</span>
                  <span class="news-source">{{ news.source_name }}</span>
                </a>
              </div>
            </div>
          </div>

          <!-- 关注度最高 Top 3 -->
          <div class="sentiment-col sentiment-col-attention">
            <div class="sentiment-col-title">
              <span class="col-icon">🔥</span>
              关注度最高
            </div>
            <div v-if="topAttention.length === 0" class="empty-sm">—</div>
            <div
              v-for="(row, idx) in topAttention"
              :key="row.asset_tag"
              class="sentiment-row"
              :class="{ 'sentiment-row-expanded': expandedTag === row.asset_tag }"
              :style="{ animationDelay: (0.06 * idx) + 's' }"
              @click="toggleTagExpand(row.asset_tag)"
            >
              <div class="sentiment-row-main">
                <span class="sentiment-tag">{{ row.asset_tag }}</span>
                <span class="sentiment-val attention-val">{{ row.total_attention.toFixed(1) }}</span>
                <span class="sentiment-expand">{{ expandedTag === row.asset_tag ? '▲' : '▼' }}</span>
              </div>
              <div v-if="expandedTag === row.asset_tag" class="sentiment-news-list">
                <div v-if="loadingTag === row.asset_tag" class="loading-sm">加载中...</div>
                <div v-else-if="(tagNewsMap[row.asset_tag] || []).length === 0" class="empty-sm">暂无新闻明细</div>
                <a
                  v-for="news in tagNewsMap[row.asset_tag] || []"
                  :key="news.title"
                  :href="news.url || '#'"
                  target="_blank"
                  class="sentiment-news-item"
                  :class="news.sentiment_score > 0.15 ? 'news-positive' : news.sentiment_score < -0.15 ? 'news-negative' : 'news-neutral'"
                >
                  <span class="news-dot"></span>
                  <span class="news-title">{{ news.title }}</span>
                  <span class="news-source">{{ news.source_name }}</span>
                </a>
              </div>
            </div>
          </div>
        </div>

        <!-- 每日市场研判 -->
        <div v-if="synthesis" class="synthesis-card">
          <div class="synthesis-accent"></div>
          <div class="synthesis-content">
            <div class="synthesis-title">📋 每日市场研判</div>
            <div class="synthesis-body">{{ synthesis.content }}</div>
            <div v-if="synthesis.key_topics.length > 0" class="synthesis-topics">
              <span v-for="t in synthesis.key_topics" :key="t" class="chip chip-topic">{{ t }}</span>
            </div>
          </div>
        </div>
      </template>
    </section>

    <!-- 数据源状态 -->
    <section v-if="systemStatus" class="section animate-in stagger-4">
      <div class="section-header">
        <h2 class="section-title">数据源状态</h2>
      </div>
      <div class="source-grid">
        <div v-for="src in systemStatus.data_sources" :key="src.table_name" class="source-card">
          <div class="source-top">
            <div class="source-name">{{ src.source_name }}</div>
            <div class="source-table mono">{{ src.table_name }}</div>
          </div>
          <div class="source-stats">
            <div class="source-stat">
              <span class="source-stat-label">记录数</span>
              <span class="source-stat-val">{{ src.record_count.toLocaleString() }}</span>
            </div>
            <div class="source-stat">
              <span class="source-stat-label">最新日期</span>
              <span class="source-stat-val">{{ src.latest_trade_date ?? '—' }}</span>
            </div>
            <div class="source-stat">
              <span class="source-stat-label">最近入库</span>
              <span class="source-stat-val mono">{{ formatTime(src.latest_ingested_at) }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 数据质量 -->
    <section class="section animate-in stagger-5">
      <div class="section-header">
        <h2 class="section-title">数据质量</h2>
        <div class="section-header-right">
          <span v-if="quality" class="section-badge">{{ formatTime(quality.checked_at) }} 检查</span>
          <span v-if="qualityLoading" class="section-badge badge-pulse">检查中...</span>
        </div>
      </div>
      <div v-if="qualityLoading" class="loading-state">
        <div class="loading-spinner"></div>
        <span>检查数据质量...</span>
      </div>
      <div v-else-if="quality" class="quality-grid">
        <div
          v-for="group in qualityGroups"
          :key="group.key"
          class="quality-card"
          :class="{ 'quality-card-warn': group.data.stale.length > 0 || group.data.missing.length > 0 }"
        >
          <div class="quality-card-header">
            <span class="quality-name">{{ group.label }}</span>
            <span class="quality-ratio" :class="group.data.up_to_date === group.data.total ? 'ratio-ok' : 'ratio-warn'">
              {{ group.data.up_to_date }}/{{ group.data.total }}
            </span>
          </div>
          <div class="quality-date">最新: {{ group.data.latest_date ?? '—' }}</div>
          <div v-if="group.data.stale.length" class="quality-issues">
            <span class="issue-label warn">过期 {{ group.data.stale.length }}</span>
            <span v-for="item in group.data.stale.slice(0, 3)" :key="item.code" class="issue-item" :title="item.name + ' ' + item.latest_date">{{ item.code }}</span>
            <span v-if="group.data.stale.length > 3" class="issue-more">+{{ group.data.stale.length - 3 }}</span>
          </div>
          <div v-if="group.data.missing.length" class="quality-issues">
            <span class="issue-label danger">缺失 {{ group.data.missing.length }}</span>
            <span v-for="item in group.data.missing.slice(0, 3)" :key="item.code" class="issue-item" :title="item.name">{{ item.code }}</span>
            <span v-if="group.data.missing.length > 3" class="issue-more">+{{ group.data.missing.length - 3 }}</span>
          </div>
        </div>
      </div>
    </section>

    <!-- 最近运行 -->
    <section v-if="systemStatus" class="section animate-in stagger-6">
      <div class="section-header">
        <h2 class="section-title">最近运行</h2>
        <div class="section-header-right">
          <span class="section-badge">最近 {{ systemStatus.recent_runs.length }} 条</span>
        </div>
      </div>
      <div v-if="systemStatus.recent_runs.length === 0" class="empty-state">
        <span class="empty-icon">📋</span>
        <span>暂无运行记录</span>
      </div>
      <div v-else class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Run ID</th>
              <th>类型</th>
              <th>策略</th>
              <th>交易日</th>
              <th>状态</th>
              <th>开始时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in systemStatus.recent_runs" :key="item.run_id">
              <td class="mono text-muted">{{ item.run_id.slice(0, 8) }}...</td>
              <td>
                <span class="type-badge">{{ formatRunType(item.run_type) }}</span>
              </td>
              <td class="text-muted mono">{{ item.strategy_id ?? '—' }}</td>
              <td class="text-muted mono">{{ item.trade_date ?? '—' }}</td>
              <td>
                <span class="status-badge" :class="'status-' + item.status">
                  <span class="status-dot"></span>
                  {{ formatStatus(item.status) }}
                </span>
              </td>
              <td class="text-muted">{{ formatTime(item.started_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * 研究总览页面（合并自原 DashboardPage + DataStatusPage）。
 *
 * 展示平台统计概览、最新信号、数据源状态、数据质量、最近运行记录。
 * 支持手动触发数据摄取和历史回补。
 */

import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { DailySentimentResponse, DataQualityResponse, MarketSynthesisResponse, StarredSummaryResponse, SystemStatusResponse, TagNewsItem } from '../types/api'
import { fetchDataQuality, fetchSystemStatus, triggerColdStart, triggerDailyIngest } from '../api/runs'
import { fetchDailySentiment, fetchMarketSynthesis, fetchPreviousTradingDay, fetchSentimentNews } from '../api/aiFactors'
import { fetchStarredSummary } from '../api/strategies'
import { useStrategyStore } from '../stores/strategies'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'
import { notifySkippedRun } from '../composables/useRunSkipToast'

/** 获取因子指标描述的快捷方法 */
function fh(key: string): string {
  return getIndicator('factors', key)?.description ?? ''
}

const systemStatus = ref<SystemStatusResponse | null>(null)
const quality = ref<DataQualityResponse | null>(null)
const statusLoading = ref(false)
const qualityLoading = ref(false)
const error = ref<string | null>(null)
const triggering = ref(false)
const triggeringColdStart = ref(false)

const strategyStore = useStrategyStore()

const starredSummary = ref<StarredSummaryResponse | null>(null)
const starredLoading = ref(false)

// ──── AI 舆情概览 ────

const sentimentDate = ref<string | null>(null)
const sentimentRows = ref<DailySentimentResponse[]>([])
const synthesis = ref<MarketSynthesisResponse | null>(null)
const sentimentLoading = ref(false)
const expandedTag = ref<string | null>(null)
const loadingTag = ref<string | null>(null)
const tagNewsMap = ref<Record<string, TagNewsItem[]>>({})

/** 判断资产标签类型（与 AIFactorsPage 中逻辑一致）。 */
function getTagType(assetTag: string): 'special' | 'index' | 'sector' {
  if (assetTag === '_general' || assetTag === '_other') return 'special'
  if (/^\d{6}$/.test(assetTag)) return 'index'
  return 'sector'
}

/** 行业主题情绪 Top 3（加权情绪降序） */
const topPositive = computed(() =>
  sentimentRows.value
    .filter((r) => getTagType(r.asset_tag) === 'sector')
    .sort((a, b) => b.weighted_sentiment - a.weighted_sentiment)
    .slice(0, 3),
)

/** 行业主题情绪倒三（加权情绪升序，末三位在前） */
const topNegative = computed(() =>
  sentimentRows.value
    .filter((r) => getTagType(r.asset_tag) === 'sector')
    .sort((a, b) => a.weighted_sentiment - b.weighted_sentiment)
    .slice(0, 3),
)

/** 行业主题关注度 Top 3（总关注度降序） */
const topAttention = computed(() =>
  sentimentRows.value
    .filter((r) => getTagType(r.asset_tag) === 'sector')
    .sort((a, b) => b.total_attention - a.total_attention)
    .slice(0, 3),
)

/** 切换标签新闻展开/收起，首次展开时按需加载新闻明细。 */
async function toggleTagExpand(tag: string) {
  if (expandedTag.value === tag) {
    expandedTag.value = null
    return
  }
  expandedTag.value = tag
  if (!tagNewsMap.value[tag] && sentimentDate.value) {
    loadingTag.value = tag
    try {
      tagNewsMap.value[tag] = await fetchSentimentNews(sentimentDate.value, tag)
    } catch {
      tagNewsMap.value[tag] = []
    } finally {
      loadingTag.value = null
    }
  }
}

/** 加载 AI 舆情概览数据（定死展示前一交易日）。 */
async function loadSentimentOverview() {
  sentimentLoading.value = true
  try {
    const prevDate = await fetchPreviousTradingDay()
    if (!prevDate) return
    sentimentDate.value = prevDate

    const [rows, synth] = await Promise.all([
      fetchDailySentiment(prevDate),
      fetchMarketSynthesis(prevDate),
    ])
    sentimentRows.value = rows
    synthesis.value = synth
  } catch {
    // 舆情加载失败不影响主页面
  } finally {
    sentimentLoading.value = false
  }
}

/** 数据质量分组配置 */
const qualityGroups = computed(() => {
  if (!quality.value) return []
  return [
    { key: 'index_bars', label: '指数日线', data: quality.value.index_bars },
    { key: 'index_valuation', label: '指数估值', data: quality.value.index_valuation },
  ]
})


/** 格式化 ISO 时间戳为简短中文友好格式（北京时间） */
function formatTime(ts: string | null | undefined): string {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** 运行类型中文映射 */
function formatRunType(runType: string): string {
  const map: Record<string, string> = {
    daily_ingest: '日频入库',
    strategy_run: '策略运行',
    cold_start: '历史回补',
    startup_fill: '启动补全',
    index_refresh: '指数数据刷新',
    macro_refresh: '宏观数据刷新',
    factor_computation: '因子计算',
  }
  return map[runType] ?? runType
}

/** 运行状态中文映射 */
function formatStatus(status: string): string {
  const map: Record<string, string> = {
    pending: '待执行',
    running: '执行中',
    success: '成功',
    failed: '失败',
  }
  return map[status] ?? status
}

/** 格式化执行日期（YYYY-MM-DD → MM-DD） */
function formatExecDate(dateStr: string): string {
  const d = new Date(dateStr)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${mm}-${dd}`
}

/** 格式化调仓频率标签 */
function formatRebalanceLabel(item: { rebalance_frequency: string | null; rebalance_day_of_week: number | null; rebalance_day_of_month: number | null }): string {
  const weekNames = ['周一', '周二', '周三', '周四', '周五']
  if (item.rebalance_frequency === 'weekly' && item.rebalance_day_of_week != null) {
    return `每${weekNames[item.rebalance_day_of_week] || '五'}调仓`
  }
  if (item.rebalance_frequency === 'monthly' && item.rebalance_day_of_month != null) {
    return `每月${item.rebalance_day_of_month}日调仓`
  }
  return '每日调仓'
}

/** 格式化择时 regime */
function formatRegime(regime: string): string {
  const map: Record<string, string> = { offensive: '进攻', defensive: '防守', neutral: '中性' }
  return map[regime] ?? regime
}

/** 取持仓前5项 */
function topPositions(positions: Record<string, number>): Array<[string, number]> {
  return Object.entries(positions)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5)
}

/** 加载星标策略摘要 */
async function loadStarredSummary() {
  starredLoading.value = true
  try {
    starredSummary.value = await fetchStarredSummary()
  } catch {
    // 星标摘要加载失败不影响主页面
  } finally {
    starredLoading.value = false
  }
}

/** 从排名数据中查找指数代码对应的指数名称 */
function getIndexName(rankings: Array<{ index_code: string; name_cn: string }>, code: string): string | null {
  const found = rankings.find(r => r.index_code === code)
  return found?.name_cn ?? null
}

/** 取消星标 */
async function handleUnstar(strategyId: string) {
  await strategyStore.unstar(strategyId)
  // 重新加载星标摘要以刷新列表
  await loadStarredSummary()
}

/** 加载系统状态 */
async function loadStatus() {
  statusLoading.value = true
  error.value = null
  try {
    systemStatus.value = await fetchSystemStatus()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载失败，请稍后重试'
  } finally {
    statusLoading.value = false
  }
}

/** 加载数据质量报告 */
async function loadQuality() {
  qualityLoading.value = true
  try {
    quality.value = await fetchDataQuality()
  } catch {
    // 数据质量检查失败不影响主页面
  } finally {
    qualityLoading.value = false
  }
}

/** 触发全量历史回补（cold_start），完成后自动刷新 */
async function triggerColdStartFn() {
  triggeringColdStart.value = true
  try {
    const res = await triggerColdStart()
    notifySkippedRun(res.run_id)
    await Promise.all([loadStatus(), loadQuality()])
  } catch (e) {
    error.value = e instanceof Error ? e.message : '触发历史回补失败'
  } finally {
    triggeringColdStart.value = false
  }
}

/** 触发数据摄取，完成后自动刷新状态和质量 */
async function triggerIngest() {
  triggering.value = true
  try {
    const res = await triggerDailyIngest()
    notifySkippedRun(res.run_id)
    await Promise.all([loadStatus(), loadQuality()])
  } catch (e) {
    error.value = e instanceof Error ? e.message : '触发摄取失败'
  } finally {
    triggering.value = false
  }
}

onMounted(() =>
  Promise.all([
    loadStatus(),
    loadQuality(),
    strategyStore.loadAll(),
    loadStarredSummary(),
    loadSentimentOverview(),
  ]),
)
</script>

<style scoped>
/* ================================================================
   研究总览 — "精准终端" 主题样式
   美学: Bloomberg 终端 × 现代数据仪表盘
   ================================================================ */

/* ── 页面容器 & 背景纹理 ── */
.dashboard {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 24px;
  /* 点阵纹理：微妙的数据终端氛围 */
  background:
    radial-gradient(circle, rgba(59, 130, 246, 0.03) 1px, transparent 1px);
  background-size: 24px 24px;
  background-position: 0 0;
}

.dashboard-bg {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  /* 顶部渐变光晕 */
  background:
    radial-gradient(ellipse 80% 50% at 50% 0%, rgba(59, 130, 246, 0.06) 0%, transparent 60%),
    radial-gradient(ellipse 40% 30% at 80% 25%, rgba(34, 197, 94, 0.04) 0%, transparent 50%);
}

/* ── 入场动效 ── */
@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(16px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.animate-in {
  animation: fadeInUp 0.5s ease-out both;
}

.stagger-1 { animation-delay: 0.05s; }
.stagger-2 { animation-delay: 0.12s; }
.stagger-3 { animation-delay: 0.19s; }
.stagger-4 { animation-delay: 0.26s; }
.stagger-5 { animation-delay: 0.33s; }
.stagger-6 { animation-delay: 0.40s; }

/* ── 页头 ── */
.page-header {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 16px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: var(--radius);
  background: rgba(59, 130, 246, 0.12);
  color: var(--accent);
  flex-shrink: 0;
}

.header-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.02em;
  line-height: 1.2;
}

.page-subtitle {
  font-size: 12px;
  color: var(--text-muted);
  font-family: monospace;
}

/* 数据库连接状态指示器 */
.connection-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  transition: background 0.4s, box-shadow 0.4s;
  margin-left: 4px;
}
.connection-dot.connected {
  background: var(--success);
  box-shadow: 0 0 8px rgba(34, 197, 94, 0.5);
}
.connection-dot.disconnected {
  background: var(--danger);
  box-shadow: 0 0 8px rgba(239, 68, 68, 0.5);
}

/* ── 按钮 ── */
.header-actions {
  display: flex;
  gap: 10px;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 9px 18px;
  border-radius: 24px;
  border: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
  letter-spacing: 0.01em;
}

.btn-icon {
  font-size: 14px;
  line-height: 1;
}

.btn-primary {
  background: var(--accent);
  color: #fff;
  box-shadow: 0 2px 12px rgba(59, 130, 246, 0.3);
}
.btn-primary:hover:not(:disabled) {
  background: var(--accent-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 18px rgba(59, 130, 246, 0.4);
}

.btn-ghost {
  background: rgba(148, 163, 184, 0.08);
  color: var(--text-muted);
  border: 1px solid rgba(148, 163, 184, 0.15);
}
.btn-ghost:hover:not(:disabled) {
  background: rgba(148, 163, 184, 0.15);
  color: var(--text);
  border-color: rgba(148, 163, 184, 0.25);
}

.btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
  transform: none !important;
}

/* ── 加载 & 空状态 & 错误 ── */
.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 20px;
  color: var(--text-muted);
  font-size: 13px;
}

.loading-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid rgba(148, 163, 184, 0.2);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 40px 20px;
  color: var(--text-muted);
  font-size: 13px;
}

.empty-icon {
  font-size: 28px;
  opacity: 0.5;
  margin-bottom: 4px;
}

.error-banner {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.2);
  border-radius: var(--radius);
  color: var(--danger);
  font-size: 13px;
}

.error-icon {
  font-size: 16px;
  flex-shrink: 0;
}

/* ── 统计卡片网格 ── */
.stat-grid {
  position: relative;
  z-index: 1;
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.stat-card {
  position: relative;
  background: rgba(30, 41, 59, 0.7);
  border: 1px solid rgba(148, 163, 184, 0.1);
  border-radius: var(--radius);
  padding: 20px;
  overflow: hidden;
  transition: all 0.25s ease;
}

.stat-card:hover {
  border-color: rgba(148, 163, 184, 0.2);
  background: rgba(30, 41, 59, 0.9);
  transform: translateY(-2px);
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.25);
}

/* 左侧 accent 色条 */
.stat-accent {
  position: absolute;
  left: 0;
  top: 12px;
  bottom: 12px;
  width: 3px;
  border-radius: 0 2px 2px 0;
  background: var(--accent);
}
.stat-accent.accent-green  { background: var(--success); }
.stat-accent.accent-amber  { background: var(--warning); }
.stat-accent.accent-purple { background: #a855f7; }

.stat-icon {
  color: var(--text-muted);
  margin-bottom: 12px;
  opacity: 0.6;
}

.stat-label {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-weight: 500;
  margin-bottom: 4px;
}

.stat-value {
  font-size: 30px;
  font-weight: 700;
  color: var(--text);
  font-family: monospace;
  letter-spacing: -0.02em;
  line-height: 1.1;
}

/* ── Section 容器 ── */
.section {
  position: relative;
  z-index: 1;
  background: rgba(30, 41, 59, 0.55);
  border: 1px solid rgba(148, 163, 184, 0.08);
  border-radius: var(--radius);
  overflow: hidden;
  transition: border-color 0.3s;
}

.section:hover {
  border-color: rgba(148, 163, 184, 0.14);
}

.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.08);
  /* 顶部 accent 线 */
  border-top: 2px solid var(--accent);
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  letter-spacing: 0.01em;
  flex-shrink: 0;
}

.section-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.section-link {
  font-size: 12px;
  color: var(--accent);
  margin-left: auto;
  flex-shrink: 0;
  transition: color 0.15s;
}
.section-link:hover { color: var(--accent-hover); }

.section-badge {
  font-size: 11px;
  background: rgba(59, 130, 246, 0.1);
  color: var(--accent);
  padding: 3px 10px;
  border-radius: 20px;
  font-family: monospace;
  white-space: nowrap;
}

.badge-pulse {
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* ── 关注策略卡片 ── */
.starred-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 14px;
  padding: 16px 20px;
}

.starred-card {
  background: rgba(51, 65, 85, 0.45);
  border: 1px solid rgba(148, 163, 184, 0.08);
  border-radius: var(--radius-sm);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: all 0.25s ease;
  animation: fadeInUp 0.4s ease-out both;
}

.starred-card:hover {
  background: rgba(51, 65, 85, 0.7);
  border-color: rgba(148, 163, 184, 0.18);
  transform: translateY(-2px);
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
}

.starred-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.starred-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text);
  transition: color 0.15s;
  text-decoration: none;
}
.starred-name:hover { color: var(--accent); }

.star-btn-small {
  padding: 3px 8px;
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: var(--radius-sm);
  color: #f59e0b;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
  line-height: 1;
  flex-shrink: 0;
}
.star-btn-small:hover {
  background: rgba(245, 158, 11, 0.2);
  border-color: rgba(245, 158, 11, 0.4);
}

.starred-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.starred-label {
  font-size: 11px;
  color: var(--text-muted);
  min-width: 36px;
  flex-shrink: 0;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

/* 调仓状态标签 */
.rebalance-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 20px;
}

.rebalance-yes {
  background: rgba(34, 197, 94, 0.12);
  color: var(--success);
}

.rebalance-no {
  background: rgba(148, 163, 184, 0.1);
  color: var(--text-muted);
}

.rebalance-detail {
  font-size: 11px;
  color: var(--text-muted);
}

.exec-date {
  font-size: 11px;
  color: var(--text-muted);
  margin-left: auto;
  font-family: monospace;
}

/* 择时 regime 标签 */
.regime-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 20px;
}
.regime-offensive { background: rgba(34, 197, 94, 0.12); color: var(--success); }
.regime-defensive { background: rgba(239, 68, 68, 0.12); color: var(--danger); }
.regime-neutral   { background: rgba(59, 130, 246, 0.12); color: #60a5fa; }
.regime-confidence { font-size: 12px; color: var(--text-muted); font-family: monospace; }

/* 持仓标签 */
.position-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.position-tag {
  font-size: 11px;
  padding: 3px 10px;
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.12);
  border-radius: 12px;
  color: var(--text-muted);
  transition: all 0.15s;
}
.position-tag:hover {
  background: rgba(59, 130, 246, 0.14);
  border-color: rgba(59, 130, 246, 0.25);
}
.position-tag strong {
  color: var(--text);
  font-weight: 600;
}

.position-more {
  font-size: 11px;
  color: var(--text-muted);
  padding: 3px 6px;
}

/* 仓位进度条 */
.exposure-bar-bg {
  flex: 1;
  height: 5px;
  background: rgba(0, 0, 0, 0.3);
  border-radius: 3px;
  overflow: hidden;
  max-width: 150px;
}

.exposure-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #34d399);
  border-radius: 3px;
  transition: width 0.5s ease;
  position: relative;
}
/* 进度条微光效果 */
.exposure-bar::after {
  content: '';
  position: absolute;
  right: 0;
  top: 0;
  bottom: 0;
  width: 4px;
  background: rgba(255, 255, 255, 0.3);
  border-radius: 0 3px 3px 0;
}

.exposure-pct {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  font-family: monospace;
  min-width: 36px;
}

/* ── 数据源卡片网格 ── */
.source-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  padding: 16px 20px;
}

.source-card {
  background: rgba(51, 65, 85, 0.35);
  border: 1px solid rgba(148, 163, 184, 0.06);
  border-radius: var(--radius-sm);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: all 0.25s ease;
}

.source-card:hover {
  border-color: rgba(59, 130, 246, 0.2);
  box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.08);
}

.source-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.source-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}

.source-table {
  font-size: 10px;
  color: var(--text-muted);
  background: rgba(0, 0, 0, 0.2);
  padding: 2px 8px;
  border-radius: 10px;
}

.source-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.source-stat {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.source-stat-label {
  font-size: 10px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.source-stat-val {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  font-family: monospace;
}

/* ── 数据质量 ── */
.quality-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  padding: 16px 20px;
}

.quality-card {
  background: rgba(51, 65, 85, 0.35);
  border: 1px solid rgba(148, 163, 184, 0.06);
  border-left: 3px solid var(--success);
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  transition: all 0.25s ease;
}

.quality-card-warn {
  border-left-color: var(--warning);
  animation: warnGlow 2s ease-in-out infinite;
}

@keyframes warnGlow {
  0%, 100% { border-left-color: var(--warning); }
  50% { border-left-color: rgba(245, 158, 11, 0.4); }
}

.quality-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.quality-name {
  font-size: 13px;
  font-weight: 600;
}

.quality-ratio {
  font-size: 13px;
  font-weight: 700;
  font-family: monospace;
}

.ratio-ok { color: var(--success); }
.ratio-warn { color: var(--warning); }

.quality-date {
  font-size: 11px;
  color: var(--text-muted);
  font-family: monospace;
}

.quality-issues {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.issue-label {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
  white-space: nowrap;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.issue-label.warn {
  background: rgba(245, 158, 11, 0.12);
  color: var(--warning);
}

.issue-label.danger {
  background: rgba(239, 68, 68, 0.12);
  color: var(--danger);
}

.issue-item {
  font-family: monospace;
  font-size: 11px;
  color: var(--text-muted);
  background: rgba(0, 0, 0, 0.2);
  border-radius: 4px;
  padding: 1px 6px;
  cursor: default;
  transition: color 0.15s;
}

.issue-item:hover { color: var(--text); }

.issue-more {
  font-size: 11px;
  color: var(--text-muted);
}

/* ── 最近运行表格 ── */
.table-wrap {
  overflow-x: auto;
}

.data-table {
  width: 100%;
  border-collapse: collapse;
}

.data-table th {
  text-align: left;
  padding: 10px 20px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  background: rgba(0, 0, 0, 0.15);
  border-bottom: 1px solid rgba(148, 163, 184, 0.1);
}

.data-table td {
  padding: 12px 20px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.05);
  font-size: 13px;
}

.data-table tbody tr {
  transition: background 0.15s;
}

.data-table tbody tr:hover td {
  background: rgba(59, 130, 246, 0.04);
}

.data-table tbody tr:last-child td {
  border-bottom: none;
}

/* 状态徽章带发光圆点 */
.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.status-pending  { background: rgba(148, 163, 184, 0.1); color: var(--text-muted); }
.status-pending .status-dot  { background: var(--text-muted); }

.status-running  { background: rgba(59, 130, 246, 0.12); color: #60a5fa; }
.status-running .status-dot  { background: #60a5fa; box-shadow: 0 0 6px rgba(96, 165, 250, 0.5); animation: pulse 1.5s ease-in-out infinite; }

.status-success  { background: rgba(34, 197, 94, 0.12); color: var(--success); }
.status-success .status-dot  { background: var(--success); }

.status-failed   { background: rgba(239, 68, 68, 0.12); color: var(--danger); }
.status-failed .status-dot   { background: var(--danger); }

.type-badge {
  font-size: 11px;
  background: rgba(148, 163, 184, 0.08);
  color: var(--text-muted);
  padding: 3px 10px;
  border-radius: 20px;
  font-family: monospace;
  white-space: nowrap;
}

/* ── AI 舆情概览 ── */
.sentiment-columns {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  padding: 16px 20px 0;
}

.sentiment-col {
  background: rgba(51, 65, 85, 0.3);
  border: 1px solid rgba(148, 163, 184, 0.06);
  border-radius: var(--radius-sm);
  padding: 14px;
  transition: all 0.25s ease;
}

.sentiment-col:hover {
  border-color: rgba(148, 163, 184, 0.14);
}

.sentiment-col-positive { border-top: 2px solid var(--success); }
.sentiment-col-bottom { border-top: 2px solid #60a5fa; }
.sentiment-col-attention { border-top: 2px solid var(--warning); }

.sentiment-col-title {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.col-icon { font-size: 14px; }

.sentiment-row {
  cursor: pointer;
  border-radius: 6px;
  transition: background 0.15s;
  animation: fadeInUp 0.35s ease-out both;
}

.sentiment-row:hover {
  background: rgba(59, 130, 246, 0.06);
}

.sentiment-row-expanded {
  background: rgba(59, 130, 246, 0.08);
}

.sentiment-row-main {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
}

.sentiment-tag {
  font-weight: 600;
  font-size: 13px;
  min-width: 50px;
}

.sentiment-val {
  font-weight: 700;
  font-size: 13px;
  margin-left: auto;
  font-family: monospace;
}

.sentiment-positive {
  color: #4ade80;
}

.sentiment-negative {
  color: #f87171;
}

.attention-val {
  color: #fbbf24;
}

.sentiment-expand {
  font-size: 9px;
  color: var(--text-muted);
  margin-left: 4px;
  transition: transform 0.2s;
}

/* 新闻展开列表 */
.sentiment-news-list {
  padding: 0 10px 10px;
  max-height: 260px;
  overflow-y: auto;
}

.sentiment-news-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 8px;
  font-size: 11px;
  border-radius: 4px;
  text-decoration: none;
  transition: background 0.12s;
}

.sentiment-news-item:hover {
  background: rgba(255, 255, 255, 0.04);
}

.news-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  flex-shrink: 0;
}

.news-positive .news-dot { background: var(--success); }
.news-negative .news-dot { background: var(--danger); }
.news-neutral  .news-dot { background: var(--text-muted); }

.news-title {
  color: var(--text);
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.news-source {
  color: var(--text-muted);
  font-size: 10px;
  flex-shrink: 0;
  font-family: monospace;
}

/* 市场研判卡片 */
.synthesis-card {
  margin: 16px 20px;
  display: flex;
  gap: 0;
  background: rgba(51, 65, 85, 0.3);
  border: 1px solid rgba(148, 163, 184, 0.08);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.synthesis-accent {
  width: 3px;
  flex-shrink: 0;
  background: linear-gradient(180deg, var(--accent), rgba(59, 130, 246, 0.2));
}

.synthesis-content {
  padding: 18px;
  flex: 1;
}

.synthesis-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
}

.synthesis-body {
  font-size: 13px;
  line-height: 1.8;
  color: var(--text);
  white-space: pre-line;
  opacity: 0.9;
}

.synthesis-topics {
  margin-top: 12px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.chip-topic {
  background: rgba(59, 130, 246, 0.1);
  color: var(--accent);
  display: inline-block;
  font-size: 11px;
  padding: 3px 10px;
  border-radius: 12px;
  border: 1px solid rgba(59, 130, 246, 0.15);
}

/* ── 通用工具类 ── */
.text-muted { color: var(--text-muted); }
.text-accent { color: var(--accent); }
.mono { font-family: monospace; font-size: 12px; }
.link-accent { color: var(--accent); text-decoration: none; transition: color 0.15s; }
.link-accent:hover { color: var(--accent-hover); }

.loading-sm { padding: 8px; text-align: center; color: var(--text-muted); font-size: 11px; }
.empty-sm { padding: 8px; text-align: center; color: var(--text-muted); font-size: 11px; }

/* ── 滚动条美化 ── */
.sentiment-news-list::-webkit-scrollbar {
  width: 4px;
}

.sentiment-news-list::-webkit-scrollbar-track {
  background: transparent;
}

.sentiment-news-list::-webkit-scrollbar-thumb {
  background: rgba(148, 163, 184, 0.2);
  border-radius: 2px;
}

.sentiment-news-list::-webkit-scrollbar-thumb:hover {
  background: rgba(148, 163, 184, 0.35);
}

/* ── 响应式 ── */
@media (max-width: 900px) {
  .stat-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .sentiment-columns {
    grid-template-columns: 1fr;
  }

  .starred-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 640px) {
  .dashboard {
    gap: 16px;
  }

  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .header-actions {
    width: 100%;
  }

  .header-actions .btn {
    flex: 1;
    justify-content: center;
  }

  .stat-grid {
    grid-template-columns: 1fr;
    gap: 10px;
  }

  .stat-value {
    font-size: 24px;
  }

  .source-grid,
  .quality-grid {
    grid-template-columns: 1fr;
  }

  .source-stats {
    grid-template-columns: repeat(3, 1fr);
  }

  .section-header {
    flex-wrap: wrap;
  }
}
</style>
