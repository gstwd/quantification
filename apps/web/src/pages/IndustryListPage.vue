<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">申万行业</h1>
        <span class="count-badge">{{ items.length }} 个一级行业</span>
      </div>
      <div class="header-actions">
        <RouterLink to="/data-management?dataset=industry_universe" class="btn-secondary">维护行业信息</RouterLink>
        <RouterLink to="/data-management?dataset=industry_daily_bar" class="btn-secondary">维护行业日线</RouterLink>
      </div>
    </div>

    <div class="notice">
      缺失数 = “库内首根日线 → 最近交易日”区间内按交易日历统计的缺口（含中间缺口与
      尾部滞后）；头部截断不计入，补全/重拉后仍可能因上游滞后残留尾部缺失。
      成分股数量按最近交易日有效归属去重统计；“综合”(801230) 默认从 RRG 基准剔除。
      质量列与数据管理页同源（后端 data_health_snapshot），未检查过的行业显示为“—”。
    </div>

    <div v-if="message" class="refresh-banner" :class="messageOk ? 'banner-ok' : 'banner-err'">
      {{ message }}
    </div>

    <div class="card filter-card">
      <input
        v-model.trim="keyword"
        class="form-input filter-input"
        placeholder="行业代码 / 名称"
        @keyup.enter="handleSearch"
      />
      <button class="btn-secondary" @click="handleSearch">查询</button>
    </div>

    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="filteredItems.length === 0" class="empty">暂无行业数据（在数据管理页对 industry_universe 执行「补最新」）</div>
    <div v-else class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>行业代码</th>
            <th>名称</th>
            <th>成分股数</th>
            <th>最新收盘</th>
            <th>涨跌幅</th>
            <th>数据起止</th>
            <th>日线条数</th>
            <th>缺失条数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in filteredItems"
            :key="item.industry_code"
            class="clickable-row"
            @click="goDetail(item.industry_code)"
          >
            <td><span class="code-mono">{{ item.industry_code }}</span></td>
            <td>
              {{ item.name_cn }}
              <span v-if="item.is_benchmark_excluded" class="excluded-tag">基准剔除</span>
            </td>
            <td class="num-cell">{{ item.member_count ?? '—' }}</td>
            <td class="num-cell">{{ item.latest_close?.toFixed(2) ?? '—' }}</td>
            <td
              class="num-cell"
              :class="changeClass(item.latest_change_pct)"
            >
              {{ formatPct(item.latest_change_pct) }}
            </td>
            <td class="mono">
              <template v-if="item.data_start_date && item.data_end_date">
                {{ item.data_start_date }} ~ {{ item.data_end_date }}
              </template>
              <template v-else>—</template>
            </td>
            <td class="num-cell">{{ item.bar_count ?? '—' }}</td>
            <td class="num-cell" :class="{ 'text-warn': (item.missing_day_count ?? 0) > 0 }">
              {{ item.missing_day_count ?? '—' }}
            </td>
            <td class="action-cell" @click.stop>
              <RouterLink
                :to="{ path: '/data-management', query: { dataset: 'industry_daily_bar', partition: item.industry_code } }"
                class="btn-mini"
              >数据维护</RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 申万行业数据管理列表页。
 *
 * 展示 31 个申万一级行业的成分股数量、日线健康快照与最新行情；
 * 质量列取自后端统一健康快照（data_health_snapshot，与数据管理页同源），
 * 数据维护统一跳转至数据管理页按数据集或单行业执行。
 */

import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import {
  fetchIndustrySummaries,
  type IndustrySummaryItem,
} from '../api/industry'

const router = useRouter()
const items = ref<IndustrySummaryItem[]>([])
const keyword = ref('')
const loading = ref(false)
const message = ref('')
const messageOk = ref(true)

const filteredItems = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  if (!q) return items.value
  return items.value.filter(
    (item) =>
      item.industry_code.toLowerCase().includes(q) ||
      item.name_cn.toLowerCase().includes(q),
  )
})

function showMessage(text: string, ok: boolean): void {
  message.value = text
  messageOk.value = ok
  window.setTimeout(() => {
    message.value = ''
  }, 5000)
}

function changeClass(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return ''
  return pct >= 0 ? 'text-rise' : 'text-fall'
}

function formatPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return '—'
  return (pct >= 0 ? '+' : '') + pct.toFixed(2) + '%'
}

function goDetail(industryCode: string): void {
  void router.push(`/industries/${industryCode}`)
}

function handleSearch(): void {
  // 列表已本地过滤，仅保留交互语义
}

async function loadSummaries(): Promise<void> {
  loading.value = true
  try {
    items.value = await fetchIndustrySummaries()
  } catch {
    items.value = []
    showMessage('行业列表加载失败', false)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadSummaries()
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-actions {
  display: flex;
  gap: 8px;
}
.page-title {
  font-size: 22px;
  font-weight: 700;
}
.count-badge {
  font-size: 11px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
}
.notice {
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.25);
  color: #93c5fd;
  font-size: 12px;
  line-height: 1.6;
}
.filter-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
}
.filter-input {
  width: 240px;
}
.form-input {
  background: var(--surface-2, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 7px 10px;
  font-size: 13px;
  outline: none;
}
.poll-tip {
  margin-left: auto;
  color: var(--accent);
  font-size: 12px;
}
.refresh-banner {
  padding: 10px 14px;
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
.loading,
.empty {
  padding: 60px;
  text-align: center;
  color: var(--text-muted);
}
.table-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
}
.data-table th {
  text-align: left;
  padding: 10px 14px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: rgba(0, 0, 0, 0.1);
  white-space: nowrap;
}
.data-table td {
  padding: 10px 14px;
  border-bottom: 1px solid rgba(51, 65, 85, 0.5);
  font-size: 13px;
}
.data-table tr:last-child td {
  border-bottom: none;
}
.clickable-row {
  cursor: pointer;
}
.clickable-row:hover {
  background: rgba(59, 130, 246, 0.06);
}
.code-mono {
  font-family: monospace;
  font-weight: 600;
  color: var(--accent);
}
.excluded-tag {
  margin-left: 6px;
  font-size: 10px;
  color: #fbbf24;
  border: 1px solid rgba(251, 191, 36, 0.4);
  border-radius: 4px;
  padding: 0 4px;
}
.mono {
  font-family: monospace;
  color: var(--text-muted);
}
.num-cell {
  font-family: monospace;
  text-align: right;
}
.text-warn {
  color: #fbbf24;
}
.text-rise {
  color: var(--success, #22c55e);
}
.text-fall {
  color: var(--danger, #ef4444);
}
.action-cell {
  white-space: nowrap;
}
.btn-secondary {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
}
.btn-secondary:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--accent);
}
.btn-secondary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-mini {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: var(--radius-sm);
  padding: 3px 8px;
  font-size: 12px;
  cursor: pointer;
  margin-right: 6px;
}
.btn-mini:hover:not(:disabled) {
  border-color: var(--accent);
  color: var(--accent);
}
.btn-mini:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-danger {
  border-color: rgba(239, 68, 68, 0.4);
  color: #f87171;
}
</style>
