<template>
  <section>
    <h2>ETF 池</h2>
    <p v-if="store.loading">加载中...</p>
    <table v-else class="table">
      <thead>
        <tr>
          <th>代码</th>
          <th>名称</th>
          <th>交易所</th>
          <th>跟踪指数</th>
          <th>类别</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in store.items" :key="item.etf_code">
          <td><RouterLink :to="`/etfs/${item.etf_code}`">{{ item.etf_code }}</RouterLink></td>
          <td>{{ item.name_cn }}</td>
          <td>{{ item.exchange }}</td>
          <td>{{ item.tracking_index_name }}</td>
          <td>{{ item.category }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { useEtfStore } from '../stores/etfs'

const store = useEtfStore()

onMounted(async () => {
  await store.loadAll()
})
</script>

<style scoped>
.table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { text-align: left; padding: 12px; border-bottom: 1px solid #e2e8f0; }
</style>
