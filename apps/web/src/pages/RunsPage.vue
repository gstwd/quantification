<template>
  <section>
    <h2>运行记录</h2>
    <p v-if="loading">加载中...</p>
    <table v-else class="table">
      <thead>
        <tr>
          <th>Run ID</th>
          <th>类型</th>
          <th>交易日</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in runs" :key="item.run_id">
          <td>{{ item.run_id }}</td>
          <td>{{ item.run_type }}</td>
          <td>{{ item.trade_date }}</td>
          <td>{{ item.status }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchRuns } from '../api/runs'
import type { ResearchRunSummary } from '../types/api'

const runs = ref<ResearchRunSummary[]>([])
const loading = ref(false)

onMounted(async () => {
  loading.value = true
  try {
    runs.value = await fetchRuns()
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { text-align: left; padding: 12px; border-bottom: 1px solid #e2e8f0; }
</style>
