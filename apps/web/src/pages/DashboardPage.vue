<template>
  <div class="page">
    <!-- 页头：标题 + 最新交易日 + 数据库连接指示灯 + 操作按钮 -->
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">研究总览</h1>
        <span class="page-subtitle">{{ systemStatus?.latest_trade_date ? '最新交易日 ' + systemStatus?.latest_trade_date : '' }}</span>
        <span
          class="connection-dot"
          :class="systemStatus?.db_connected ? 'connected' : 'disconnected'"
          :title="systemStatus?.db_connected ? '数据库已连接' : '数据库连接异常'"
        ></span>
      </div>
      <div class="header-actions">
        <button class="btn btn-secondary" :disabled="triggeringColdStart" @click="triggerColdStartFn">
          {{ triggeringColdStart ? '执行中...' : '历史回补（冷启动）' }}
        </button>
        <button class="btn btn-primary" :disabled="triggering" @click="triggerIngest">
          {{ triggering ? '触发中...' : '触发数据摄取（增量）' }}
        </button>
      </div>
    </div>

    <!-- 加载 / 错误状态 -->
    <div v-if="statusLoading" class="loading">加载中...</div>
    <div v-else-if="error" class="error-tip">{{ error }}</div>

    <!-- 数据概览 stat grid -->
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-label">ETF 总数</div>
        <div class="stat-value">{{ systemStatus?.active_etf_count ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">活跃策略</div>
        <div class="stat-value">{{ strategyStore.items.length || '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">最新交易日</div>
        <div class="stat-value">{{ systemStatus?.latest_trade_date ?? '—' }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">数据频率</div>
        <div class="stat-value">{{ systemStatus?.frequency || 'daily' }}</div>
      </div>
    </div>

    <!-- 关注策略：星标策略当日执行摘要 -->
    <div class="section">
      <div class="section-header">
        <h2 class="section-title">关注策略</h2>
        <span v-if="starredLoading" class="section-badge">加载中...</span>
        <span v-else-if="starredSummary" class="section-badge">{{ starredSummary.items.length }} 个星标</span>
      </div>
      <div v-if="starredLoading" class="loading">加载中...</div>
      <div v-else-if="starredSummary?.items.length" class="starred-grid">
        <div v-for="item in starredSummary.items" :key="item.strategy_id" class="starred-card">
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
              执行日 {{ formatExecDate(item.data_date) }}
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
              >
                {{ code }} <strong>{{ (weight * 100).toFixed(0) }}%</strong>
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
      <div v-else class="empty">
        暂无关注策略，前往
        <RouterLink to="/strategies" class="link-accent">策略中心</RouterLink>
        添加关注
      </div>
    </div>

    <!-- 数据源状态 -->
    <div v-if="systemStatus" class="section">
      <div class="section-header">
        <h2 class="section-title">数据源状态</h2>
      </div>
      <div class="source-grid">
        <div v-for="src in systemStatus.data_sources" :key="src.table_name" class="source-card">
          <div class="source-name">{{ src.source_name }}</div>
          <div class="source-table mono text-muted">{{ src.table_name }}</div>
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
    </div>

    <!-- 数据质量 -->
    <div class="section">
      <div class="section-header">
        <h2 class="section-title">数据质量</h2>
        <span v-if="quality" class="section-badge">{{ formatTime(quality.checked_at) }} 检查</span>
        <span v-if="qualityLoading" class="section-badge">检查中...</span>
      </div>
      <div v-if="qualityLoading" class="quality-loading">检查中...</div>
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
    </div>

    <!-- 最近运行 -->
    <div v-if="systemStatus" class="section">
      <div class="section-header">
        <h2 class="section-title">最近运行</h2>
        <span class="section-badge">最近 {{ systemStatus.recent_runs.length }} 条</span>
      </div>
      <div v-if="systemStatus.recent_runs.length === 0" class="empty">暂无运行记录</div>
      <table v-else class="data-table">
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
              <span class="status-badge" :class="'status-' + item.status">{{ formatStatus(item.status) }}</span>
            </td>
            <td class="text-muted">{{ formatTime(item.started_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
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

import type { DataQualityResponse, StarredSummaryResponse, SystemStatusResponse } from '../types/api'
import { fetchDataQuality, fetchSystemStatus, triggerColdStart, triggerDailyIngest } from '../api/runs'
import { fetchStarredSummary } from '../api/strategies'
import { useStrategyStore } from '../stores/strategies'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'

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

/** 数据质量分组配置 */
const qualityGroups = computed(() => {
  if (!quality.value) return []
  return [
    { key: 'etf_bars', label: 'ETF 日线', data: quality.value.etf_bars },
    { key: 'etf_shares', label: 'ETF 份额', data: quality.value.etf_shares },
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
    universe_refresh: '标的刷新',
    cold_start: '历史回补',
    startup_fill: '启动补全',
    etf_refresh: 'ETF 行情刷新',
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
    await triggerColdStart()
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
    await triggerDailyIngest()
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
  ]),
)
</script>

<style scoped>
/* === 页面布局 === */
.page { display: flex; flex-direction: column; gap: 24px; }

/* === 页头 === */
.page-header { display: flex; align-items: center; justify-content: space-between; }
.header-left { display: flex; align-items: center; gap: 10px; }
.page-title { font-size: 22px; font-weight: 700; color: var(--text); }
.page-subtitle { font-size: 13px; color: var(--text-muted); }

/* 数据库连接状态指示器 */
.connection-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  display: inline-block;
  transition: background 0.3s;
}
.connection-dot.connected { background: var(--success); }
.connection-dot.disconnected { background: var(--danger); }

/* === 按钮 === */
.header-actions { display: flex; gap: 8px; }
.btn {
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  border: none;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s;
}
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover { background: var(--accent-hover); }
.btn-secondary { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); }
.btn-secondary:hover { background: var(--surface); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* === 加载 / 空 / 错误状态 === */
.loading, .empty { padding: 32px 20px; color: var(--text-muted); text-align: center; }
.error-tip {
  padding: 16px 20px;
  background: rgba(239, 68, 68, 0.1);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: var(--radius);
  color: var(--danger);
  font-size: 13px;
}

/* === 数据概览 stat grid === */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
}
.stat-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }
.stat-value { font-size: 28px; font-weight: 700; color: var(--text); }

/* === Section 容器 === */
.section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.section-header {
  display: flex; align-items: center; gap: 10px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
}
.section-title { font-size: 15px; font-weight: 600; }
.section-badge {
  font-size: 11px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
}

/* === 数据源卡片网格 === */
.source-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 12px;
  padding: 16px 20px;
}
.source-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 16px;
  display: flex; flex-direction: column; gap: 12px;
}
.source-name { font-size: 14px; font-weight: 600; color: var(--text); }
.source-table { font-size: 11px; }
.source-stats { display: flex; flex-direction: column; gap: 6px; }
.source-stat { display: flex; justify-content: space-between; align-items: center; }
.source-stat-label { font-size: 12px; color: var(--text-muted); }
.source-stat-val { font-size: 13px; font-weight: 500; color: var(--text); }

/* === 表格 === */
.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  text-align: left; padding: 10px 20px;
  font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
}
.data-table td { padding: 12px 20px; border-bottom: 1px solid rgba(51,65,85,0.5); }
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: rgba(255,255,255,0.02); }

.code-link { color: var(--accent); font-family: monospace; font-size: 13px; }
.code-link:hover { text-decoration: underline; }

.score-cell { display: flex; align-items: center; gap: 10px; }
.score-bar-bg { flex: 1; height: 6px; background: var(--surface-2); border-radius: 3px; overflow: hidden; max-width: 100px; }
.score-bar { height: 100%; border-radius: 3px; transition: width 0.3s; }
.score-num { font-size: 13px; font-weight: 600; color: var(--text); min-width: 36px; }

.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.badge-high { background: rgba(34, 197, 94, 0.15); color: var(--signal-high); }
.badge-mid { background: rgba(245, 158, 11, 0.15); color: var(--signal-mid); }
.badge-low { background: rgba(148, 163, 184, 0.15); color: var(--signal-low); }

/* === 徽章 === */
.type-badge {
  font-size: 11px;
  background: var(--surface-2);
  color: var(--text-muted);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
}
.status-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 20px;
  font-size: 11px; font-weight: 600; text-transform: uppercase;
}
.status-pending  { background: rgba(148,163,184,0.15); color: var(--text-muted); }
.status-running  { background: rgba(59,130,246,0.15); color: #60a5fa; }
.status-success  { background: rgba(34,197,94,0.15); color: var(--success); }
.status-failed   { background: rgba(239,68,68,0.15); color: var(--danger); }

/* === 数据质量 === */
.quality-loading { padding: 24px 20px; color: var(--text-muted); font-size: 13px; }
.quality-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  padding: 16px 20px;
}
.quality-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 14px 16px;
  display: flex; flex-direction: column; gap: 8px;
}
.quality-card-warn { border-color: rgba(245,158,11,0.4); }
.quality-card-header { display: flex; align-items: center; justify-content: space-between; }
.quality-name { font-size: 13px; font-weight: 600; }
.quality-ratio { font-size: 13px; font-weight: 700; font-family: monospace; }
.ratio-ok { color: var(--success); }
.ratio-warn { color: #f59e0b; }
.quality-date { font-size: 11px; color: var(--text-muted); }
.quality-issues { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.issue-label {
  font-size: 10px; font-weight: 600; padding: 1px 6px;
  border-radius: 20px; white-space: nowrap;
}
.issue-label.warn { background: rgba(245,158,11,0.15); color: #f59e0b; }
.issue-label.danger { background: rgba(239,68,68,0.15); color: var(--danger); }
.issue-item {
  font-family: monospace; font-size: 11px; color: var(--text-muted);
  background: rgba(0,0,0,0.2); border-radius: 4px; padding: 1px 5px;
  cursor: default;
}
.issue-more { font-size: 11px; color: var(--text-muted); }

/* === 通用工具类 === */
.text-muted { color: var(--text-muted); }
.mono { font-family: monospace; font-size: 12px; }

/* === 关注策略卡片 === */
.starred-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 12px;
  padding: 16px 20px;
}
.starred-card {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.starred-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.starred-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--accent);
  transition: color 0.15s;
}
.starred-name:hover { color: var(--accent-hover); }

.star-btn-small {
  padding: 2px 6px;
  background: transparent;
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: var(--radius-sm);
  color: #f59e0b;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.15s;
  line-height: 1;
}
.star-btn-small:hover { background: rgba(245, 158, 11, 0.1); }

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
}

/* 调仓状态标签 */
.rebalance-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
}
.rebalance-yes {
  background: rgba(34, 197, 94, 0.15);
  color: var(--success);
}
.rebalance-no {
  background: rgba(148, 163, 184, 0.15);
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
}

/* 择时 regime 标签 */
.regime-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
}
.regime-offensive { background: rgba(34, 197, 94, 0.15); color: var(--success); }
.regime-defensive { background: rgba(239, 68, 68, 0.15); color: var(--danger); }
.regime-neutral   { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
.regime-confidence { font-size: 12px; color: var(--text-muted); }

/* 持仓标签 */
.position-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.position-tag {
  font-size: 11px;
  padding: 2px 8px;
  background: rgba(59, 130, 246, 0.08);
  border-radius: 12px;
  color: var(--text-muted);
}
.position-tag strong { color: var(--text); }
.position-more {
  font-size: 11px;
  color: var(--text-muted);
  padding: 2px 4px;
}

/* 仓位进度条 */
.exposure-bar-bg {
  flex: 1;
  height: 6px;
  background: var(--surface);
  border-radius: 3px;
  overflow: hidden;
  max-width: 140px;
}
.exposure-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), #34d399);
  border-radius: 3px;
  transition: width 0.3s;
}
.exposure-pct {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
}

.link-accent { color: var(--accent); }

/* === 响应式 === */
@media (max-width: 640px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .source-grid { grid-template-columns: 1fr; }
  .stat-value { font-size: 22px; }
}
</style>
