<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">指数数据</h1>
        <span class="count-badge">{{ indexes.length }} 个</span>
      </div>
    </div>

    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="indexes.length === 0" class="empty">暂无指数数据</div>
    <div v-else class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>指数代码</th>
            <th>名称</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in indexes"
            :key="item.index_code"
            class="clickable-row"
            @click="$router.push(`/indexes/${item.index_code}`)"
          >
            <td><span class="code-mono">{{ item.index_code }}</span></td>
            <td>{{ item.index_name }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchBenchmarkIndexes } from '../api/market_data'
import type { BenchmarkIndex } from '../types/api'

const indexes = ref<BenchmarkIndex[]>([])
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  try {
    indexes.value = await fetchBenchmarkIndexes()
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 24px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.header-left { display: flex; align-items: center; gap: 10px; }
.page-title { font-size: 22px; font-weight: 700; }
.count-badge {
  font-size: 11px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
}
.loading, .empty { padding: 60px; text-align: center; color: var(--text-muted); }

.table-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  text-align: left; padding: 10px 20px;
  font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: rgba(0,0,0,0.1);
}
.data-table td { padding: 12px 20px; border-bottom: 1px solid rgba(51,65,85,0.5); }
.data-table tr:last-child td { border-bottom: none; }
.clickable-row { cursor: pointer; transition: background 0.1s; }
.clickable-row:hover td { background: rgba(59,130,246,0.06); }
.code-mono { font-family: monospace; font-size: 13px; font-weight: 600; color: var(--accent); }
</style>
