<template>
  <section>
    <h2>ETF 详情</h2>
    <p v-if="store.loading">加载中...</p>
    <div v-else-if="store.current" class="card">
      <h3>{{ store.current.name_cn }} ({{ store.current.etf_code }})</h3>
      <p>交易所：{{ store.current.exchange }}</p>
      <p>跟踪指数：{{ store.current.tracking_index_name }}</p>
      <p>基金公司：{{ store.current.fund_company }}</p>
      <p>类别：{{ store.current.category }}</p>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'

import { useEtfStore } from '../stores/etfs'

const props = defineProps<{ etfCode: string }>()
const store = useEtfStore()

onMounted(async () => {
  await store.loadOne(props.etfCode)
})
</script>

<style scoped>
.card { background: #fff; border-radius: 12px; padding: 16px; }
</style>
