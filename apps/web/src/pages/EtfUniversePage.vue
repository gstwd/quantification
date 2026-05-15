<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">ETF 池</h1>
      <span class="count-badge">{{ filtered.length }} 只</span>
    </div>

    <div class="toolbar">
      <div class="search-wrap">
        <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input v-model="query" class="search-input" placeholder="搜索代码或名称..." />
      </div>
    </div>

    <div class="table-wrap">
      <div v-if="store.loading" class="loading">加载中...</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>交易所</th>
            <th>跟踪指数</th>
            <th>类别</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="etf in filtered" :key="etf.etf_code" class="clickable-row" @click="$router.push(`/etfs/${etf.etf_code}`)">
            <td><span class="code-mono">{{ etf.etf_code }}</span></td>
            <td class="name-cell">{{ etf.name_cn }}</td>
            <td>
              <span class="badge" :class="etf.exchange === 'SSE' ? 'badge-sse' : 'badge-szse'">{{ etf.exchange }}</span>
            </td>
            <td class="text-muted">{{ etf.tracking_index_name }}</td>
            <td>
              <span class="badge badge-category">{{ etf.category }}</span>
            </td>
            <td>
              <span class="status-dot" :class="etf.is_active ? 'dot-active' : 'dot-inactive'"></span>
              <span class="text-muted">{{ etf.is_active ? '活跃' : '下架' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!store.loading && filtered.length === 0" class="empty">未找到匹配的 ETF</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { useEtfStore } from '../stores/etfs'

const store = useEtfStore()
const query = ref('')

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return store.items
  return store.items.filter(e => e.etf_code.includes(q) || e.name_cn.toLowerCase().includes(q))
})

onMounted(() => store.loadAll())
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }

.page-header { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 22px; font-weight: 700; }
.count-badge {
  font-size: 12px;
  background: var(--surface-2);
  color: var(--text-muted);
  padding: 2px 10px;
  border-radius: 20px;
}

.toolbar { display: flex; }
.search-wrap { position: relative; display: flex; align-items: center; }
.search-icon { position: absolute; left: 10px; color: var(--text-muted); pointer-events: none; }
.search-input {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 8px 12px 8px 32px;
  width: 260px;
  outline: none;
  transition: border-color 0.15s;
}
.search-input:focus { border-color: var(--accent); }
.search-input::placeholder { color: var(--text-muted); }

.table-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.loading, .empty { padding: 40px; text-align: center; color: var(--text-muted); }

.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  text-align: left;
  padding: 10px 20px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: rgba(0,0,0,0.1);
}
.data-table td { padding: 13px 20px; border-bottom: 1px solid rgba(51, 65, 85, 0.5); }
.data-table tr:last-child td { border-bottom: none; }

.clickable-row { cursor: pointer; }
.clickable-row:hover td { background: rgba(255,255,255,0.03); }

.code-mono { font-family: monospace; font-size: 13px; color: var(--accent); font-weight: 600; }
.name-cell { font-weight: 500; }
.text-muted { color: var(--text-muted); font-size: 13px; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge-sse { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
.badge-szse { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.badge-category { background: var(--surface-2); color: var(--text-muted); }

.status-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.dot-active { background: var(--success); }
.dot-inactive { background: var(--text-muted); }
</style>
