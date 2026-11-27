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
        <RouterLink to="/data-management" class="btn btn-primary">数据管理</RouterLink>
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
        <div class="stat-label">启用策略</div>
        <div class="stat-value">{{ strategyStore.loading && !strategyStore.items.length ? '—' : activeStrategyCount }}</div>
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
        <div class="stat-label">最近入库（北京时间）</div>
        <div class="stat-value">{{ latestIngestedText }}</div>
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
            <span v-if="item.is_selection_rebalance_day" class="rebalance-detail">选股腿到期</span>
            <span v-if="item.is_risk_rebalance_day" class="rebalance-detail">风险腿到期</span>
            <span v-if="legsIdentical(item)" class="rebalance-detail">
              两腿同频：{{ formatRebalanceLabel(item.selection_rebalance_schedule, item.rebalance_frequency, item.rebalance_day_of_week, item.rebalance_day_of_month) }}
            </span>
            <template v-else>
              <span class="rebalance-detail">
                选股腿：{{ formatRebalanceLabel(item.selection_rebalance_schedule, item.rebalance_frequency, item.rebalance_day_of_week, item.rebalance_day_of_month) }}
              </span>
              <span class="rebalance-detail">
                风险腿：{{ formatRebalanceLabel(item.risk_rebalance_schedule, 'daily', null, null) }}
              </span>
            </template>
            <span v-if="item.data_date" class="exec-date">
              数据截止 {{ formatExecDate(item.data_date) }}
            </span>
          </div>

          <!-- 双腿计划说明：仅选股腿/仅风险腿到期时，下方持仓不是完整重建目标 -->
          <div v-if="item.plan_mode === 'selection_only' || item.plan_mode === 'risk_only'" class="starred-row">
            <span class="rebalance-detail">{{ formatPlanMode(item) }}</span>
          </div>

          <!-- 择时信号 -->
          <div v-if="item.timing?.regime" class="starred-row">
            <span class="starred-label">择时</span>
            <span :class="['regime-badge', `regime-${item.timing.regime}`]">
              {{ formatRegime(item.timing.regime) }}
            </span>
            <span v-if="item.timing.confidence != null" class="regime-confidence" title="择时信号确信度（0-100），不是仓位">
              确信度 {{ item.timing.confidence.toFixed(0) }}%
            </span>
          </div>

          <!-- 仓位概览 -->
          <div v-if="nonZeroCount(item.plan?.positions) > 0" class="starred-row">
            <span class="starred-label">持仓</span>
            <div class="position-tags">
              <span
                v-for="[code, weight] in topPositions(item.plan.positions)"
                :key="code"
                class="position-tag"
                :title="code"
              >
                {{ getIndexName(item.rankings, code) || code }} <strong>{{ formatWeight(weight) }}</strong>
              </span>
              <span
                v-if="nonZeroCount(item.plan.positions) > 5"
                class="position-more"
              >+{{ nonZeroCount(item.plan.positions) - 5 }}</span>
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
            <span class="exposure-pct">{{ formatWeight(item.plan.total_exposure) }}</span>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">
        <span class="empty-icon">☆</span>
        <span>暂无关注策略，前往 <RouterLink to="/strategies" class="link-accent">策略中心</RouterLink> 添加关注</span>
      </div>
    </section>

    <!-- 最近运行 -->
    <section v-if="systemStatus" class="section animate-in stagger-4">
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
import { getErrorMessage } from '../api/client'
import { regimeLabel, runStatusLabel, runTypeLabel } from '../utils/labels'
/**
 * 研究总览页面（合并自原 DashboardPage + DataStatusPage）。
 *
 * 展示平台统计概览、最新信号、最近运行记录。
 * 数据维护统一入口在"数据管理"页；本页只做状态浏览。
 */

import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import type { StarredStrategyItem, StarredSummaryResponse, SystemStatusResponse } from '../types/api'
import { fetchSystemStatus } from '../api/runs'
import { fetchStarredSummary } from '../api/strategies'
import { useStrategyStore } from '../stores/strategies'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'

const systemStatus = ref<SystemStatusResponse | null>(null)
const statusLoading = ref(false)
const error = ref<string | null>(null)

const strategyStore = useStrategyStore()

/** 启用状态的策略数量（不含草稿 / 禁用） */
const activeStrategyCount = computed(() => strategyStore.items.filter(i => i.status === 'active').length)

/** 各数据表最近一次入库时间中的最大值（北京时间） */
const latestIngestedText = computed(() => {
  const times = (systemStatus.value?.data_sources ?? [])
    .map(src => src.latest_ingested_at)
    .filter((t): t is string => !!t)
  if (times.length === 0) return '—'
  const latest = times.reduce((a, b) => (new Date(a).getTime() >= new Date(b).getTime() ? a : b))
  return formatTime(latest)
})

const starredSummary = ref<StarredSummaryResponse | null>(null)
const starredLoading = ref(false)

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

/** 运行类型中文映射（含已下线类型，供历史运行记录展示） */
function formatRunType(runType: string): string {
  return runTypeLabel(runType)
}

/** 运行状态中文映射 */
function formatStatus(status: string): string {
  return runStatusLabel(status)
}

/** 格式化执行日期（YYYY-MM-DD → MM-DD） */
function formatExecDate(dateStr: string): string {
  const [year, month, day] = dateStr.split('-').map(Number)
  const d = new Date(year, month - 1, day)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `${mm}-${dd}`
}

/** 两条腿的日程是否等价（等价时合并展示为单腿口径） */
function legsIdentical(item: StarredStrategyItem): boolean {
  const selection = item.selection_rebalance_schedule
  const risk = item.risk_rebalance_schedule
  if (!selection || !risk) return true
  const keys = ['frequency', 'day_of_week', 'week_parity', 'day_of_month']
  return keys.every(k => (selection[k] ?? null) === (risk[k] ?? null))
}

/** 格式化调仓频率标签 */
function formatRebalanceLabel(
  schedule: Record<string, unknown> | null,
  fallbackFrequency: string | null,
  fallbackDayOfWeek: number | null,
  fallbackDayOfMonth: number | null,
): string {
  const weekNames = ['周一', '周二', '周三', '周四', '周五']
  const frequency = (schedule?.frequency as string | undefined) || fallbackFrequency || 'daily'
  const dayOfWeek = (schedule?.day_of_week as number | undefined) ?? fallbackDayOfWeek
  const dayOfMonth = (schedule?.day_of_month as number | undefined) ?? fallbackDayOfMonth
  if (frequency === 'biweekly') {
    const parity = schedule?.week_parity === 'odd' ? '奇数' : '偶数'
    return `双周${parity}周（ISO）${weekNames[dayOfWeek ?? 4]}`
  }
  if (frequency === 'weekly' && dayOfWeek != null) {
    return `每${weekNames[dayOfWeek] || '五'}调仓`
  }
  if (frequency === 'monthly' && dayOfMonth != null) {
    return `每月${dayOfMonth}日调仓`
  }
  return '每日调仓'
}

/**
 * 生成双腿单独到期时的操作说明。
 *
 * 系统不跟踪实盘持仓，仅风险腿到期时无法给出具体成分权重，只能说明目标总仓位；
 * 仅选股腿到期时引擎给出的是新成分，实际应维持当前总仓位。
 *
 * @param item - 星标策略执行摘要
 * @returns 面向用户的计划说明文字
 */
function formatPlanMode(item: StarredStrategyItem): string {
  if (item.plan_mode === 'risk_only') {
    return `仅风险腿到期：维持现有成分，总仓位调至 ${formatWeight(item.timing_target_exposure)}`
  }
  if (item.plan_mode === 'selection_only') {
    return '仅选股腿到期：替换为下列成分，维持当前总仓位（按单资产上限裁剪）'
  }
  return ''
}

/** 格式化择时 regime */
function formatRegime(regime: string): string {
  return regimeLabel(regime)
}

/** 权重 > 0 的持仓（目标权重为 0 的成分不展示） */
function nonZeroPositions(positions: Record<string, number> | null | undefined): Array<[string, number]> {
  return Object.entries(positions ?? {}).filter(([, w]) => w > 1e-9)
}

/** 权重 > 0 的持仓数量 */
function nonZeroCount(positions: Record<string, number> | null | undefined): number {
  return nonZeroPositions(positions).length
}

/** 取权重最高的前 5 项持仓 */
function topPositions(positions: Record<string, number>): Array<[string, number]> {
  return nonZeroPositions(positions)
    .sort(([, a], [, b]) => b - a)
    .slice(0, 5)
}

/**
 * 仓位比例格式化：整数百分比原样显示，否则保留 1 位小数，
 * 避免 2.5% 四舍五入成 3% 后与总仓位对不上。
 */
function formatWeight(weight: number): string {
  const pct = weight * 100
  const rounded = Math.round(pct * 10) / 10
  return Number.isInteger(rounded) ? `${rounded}%` : `${rounded.toFixed(1)}%`
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
    error.value = getErrorMessage(e, '加载失败，请稍后重试')
  } finally {
    statusLoading.value = false
  }
}

onMounted(() =>
  Promise.all([
    loadStatus(),
    strategyStore.loadAll(),
    loadStarredSummary(),
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
  font-family: var(--font-mono);
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
  font-family: var(--font-mono);
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
  font-family: var(--font-mono);
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
  font-family: var(--font-mono);
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
.regime-confidence { font-size: 12px; color: var(--text-muted); font-family: var(--font-mono); }

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
  font-family: var(--font-mono);
  min-width: 36px;
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

.status-partial_success { background: rgba(245, 158, 11, 0.12); color: var(--warning); }
.status-partial_success .status-dot { background: var(--warning); }

.status-skipped  { background: rgba(148, 163, 184, 0.1); color: var(--text-muted); }
.status-skipped .status-dot  { background: var(--text-muted); }

.status-cancelled { background: rgba(148, 163, 184, 0.1); color: var(--text-muted); }
.status-cancelled .status-dot { background: var(--text-muted); }

.status-failed   { background: rgba(239, 68, 68, 0.12); color: var(--danger); }
.status-failed .status-dot   { background: var(--danger); }

.type-badge {
  font-size: 11px;
  background: rgba(148, 163, 184, 0.08);
  color: var(--text-muted);
  padding: 3px 10px;
  border-radius: 20px;
  font-family: var(--font-mono);
  white-space: nowrap;
}

/* ── 通用工具类 ── */
.text-muted { color: var(--text-muted); }
.text-accent { color: var(--accent); }
.mono { font-family: var(--font-mono); font-size: 12px; }
.link-accent { color: var(--accent); text-decoration: none; transition: color 0.15s; }
.link-accent:hover { color: var(--accent-hover); }

/* ── 响应式 ── */
@media (max-width: 900px) {
  .stat-grid {
    grid-template-columns: repeat(2, 1fr);
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

  .section-header {
    flex-wrap: wrap;
  }
}
</style>
