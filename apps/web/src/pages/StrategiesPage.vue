<template>
  <section>
    <h2>策略中心</h2>
    <p v-if="store.loading">加载中...</p>
    <div v-else class="grid">
      <article v-for="item in store.items" :key="item.strategy_id" class="card">
        <h3><RouterLink :to="`/strategies/${item.strategy_id}`">{{ item.display_name }}</RouterLink></h3>
        <p>{{ item.description }}</p>
        <p>版本：{{ item.version }}</p>
        <p>输入：{{ item.required_inputs.join(', ') }}</p>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { useStrategyStore } from '../stores/strategies'

const store = useStrategyStore()

onMounted(async () => {
  await store.loadAll()
})
</script>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.card { background: #fff; border-radius: 12px; padding: 16px; }
</style>
