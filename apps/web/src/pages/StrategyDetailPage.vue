<template>
  <section>
    <h2>策略详情</h2>
    <div v-if="store.current" class="card">
      <h3>{{ store.current.display_name }}</h3>
      <p>{{ store.current.description }}</p>
      <p>频率：{{ store.current.frequency }}</p>
      <p>资产范围：{{ store.current.asset_scope }}</p>
      <h4>参数 Schema</h4>
      <pre>{{ JSON.stringify(store.current.parameter_schema, null, 2) }}</pre>
      <h4>因子</h4>
      <pre>{{ JSON.stringify(store.current.factors, null, 2) }}</pre>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'

import { useStrategyStore } from '../stores/strategies'

const props = defineProps<{ strategyId: string }>()
const store = useStrategyStore()

onMounted(async () => {
  await store.loadOne(props.strategyId)
})
</script>

<style scoped>
.card { background: #fff; border-radius: 12px; padding: 16px; }
</style>
