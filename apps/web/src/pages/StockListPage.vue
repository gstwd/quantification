<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">个股数据</h1>
        <span class="count-badge">{{ total.toLocaleString() }} 个</span>
      </div>
      <div class="header-actions">
        <button class="btn-secondary" :disabled="loading" @click="loadStocks">
          {{ loading ? '加载中...' : '刷新' }}
        </button>
      </div>
    </div>

    <div class="notice">
      缺失数为“上市日（或默认 2013-01-01）到最近交易日”的交易日历全口径统计，
      停牌/长期无成交的日期也会计入，补全后可能仍大于 0。
    </div>

    <div class="card filter-card">
      <input
        v-model.trim="keyword"
        class="form-input filter-input"
        placeholder="股票代码 / 名称"
        @keyup.enter="handleSearch"
      />
      <select v-model="industryCode" class="form-select" @change="handleSearch">
        <option value="">全部行业</option>
        <option v-for="item in industries" :key="item.industry_code" :value="item.industry_code">
          {{ item.name_cn }}
        </option>
      </select>
      <select v-model="status" class="form-select" @change="handleSearch">
        <option value="">全部状态</option>
        <option value="active">活跃</option>
        <option value="delisted">已退市</option>
      </select>
      <button class="btn-secondary" @click="handleSearch">查询</button>
    </div>

    <div v-if="message" class="refresh-banner" :class="messageOk ? 'banner-ok' : 'banner-err'">
      {{ message }}
    </div>

    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="items.length === 0" class="empty">暂无个股数据（在数据管理页对 stock_universe 执行「补最新」）</div>
    <div v-else class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>股票代码</th>
            <th>名称</th>
            <th>申万一级行业</th>
            <th>上市时间</th>
            <th>数据起止</th>
            <th>日线条数</th>
            <th>缺失条数</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in items" :key="item.stock_code">
            <td>
              <span class="code-mono">{{ item.stock_code }}</span>
              <span v-if="!item.is_active" class="delist-tag">退市</span>
            </td>
            <td>{{ item.name_cn || '—' }}</td>
            <td>{{ item.industry_name || '—' }}</td>
            <td class="mono">{{ item.ipo_date ?? '—' }}</td>
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
            <td class="action-cell">
              <RouterLink
                :to="{ path: '/data-management', query: { dataset: 'stock_daily_close', partition: item.stock_code } }"
                class="btn-mini"
              >数据维护</RouterLink>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="total > limit" class="pagination">
      <button class="btn-secondary" :disabled="offset <= 0" @click="changePage(offset - limit)">
        上一页
      </button>
      <span class="page-info">
        {{ offset + 1 }}-{{ Math.min(offset + limit, total) }} / {{ total }}
      </span>
      <button
        class="btn-secondary"
        :disabled="offset + limit >= total"
        @click="changePage(offset + limit)"
      >
        下一页
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 个股数据列表页：展示个股元数据与日线健康快照（后端 data_health_snapshot），
 * 单股维护统一由数据管理页承接，页面不提供任务触发入口。
 */

import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchIndustryIndexes, type IndustryIndexSummary } from '../api/industry'
import {
  fetchStockSummaries,
  type StockSummary,
} from '../api/stocks'

const items = ref<StockSummary[]>([])
const industries = ref<IndustryIndexSummary[]>([])
const total = ref(0)
const offset = ref(0)
const limit = 20
const keyword = ref('')
const industryCode = ref('')
const status = ref('')
const loading = ref(false)
const message = ref('')
const messageOk = ref(true)

function showMessage(text: string, ok: boolean): void {
  message.value = text
  messageOk.value = ok
  window.setTimeout(() => {
    message.value = ''
  }, 5000)
}

async function loadStocks(): Promise<void> {
  loading.value = true
  try {
    const data = await fetchStockSummaries({
      offset: offset.value,
      limit,
      keyword: keyword.value || undefined,
      industryCode: industryCode.value || undefined,
      status: (status.value || undefined) as 'active' | 'delisted' | undefined,
    })
    items.value = data.items
    total.value = data.total
  } catch {
    items.value = []
    total.value = 0
    showMessage('个股列表加载失败', false)
  } finally {
    loading.value = false
  }
}

function handleSearch(): void {
  offset.value = 0
  void loadStocks()
}

function changePage(nextOffset: number): void {
  offset.value = nextOffset
  void loadStocks()
}

async function loadIndustries(): Promise<void> {
  try {
    industries.value = await fetchIndustryIndexes()
  } catch {
    industries.value = []
  }
}

onMounted(() => {
  void loadIndustries()
  void loadStocks()
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
  line-height: 1.5;
}
.filter-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
}
.filter-input {
  width: 220px;
}
.form-select,
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
.code-mono {
  font-family: monospace;
  font-weight: 600;
  color: var(--accent);
}
.delist-tag {
  margin-left: 6px;
  font-size: 10px;
  color: #f87171;
  border: 1px solid rgba(239, 68, 68, 0.4);
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
.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
}
.page-info {
  font-size: 13px;
  color: var(--text-muted);
  font-family: monospace;
}
</style>
