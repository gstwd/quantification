<template>
  <section>
    <h2>研究总览</h2>
    <div class="grid">
      <article class="card">
        <h3>系统状态</h3>
        <pre>{{ JSON.stringify(systemStatus, null, 2) }}</pre>
      </article>
      <article class="card">
        <h3>最新三因子信号</h3>
        <ul>
          <li v-for="row in signals" :key="row.etf_code">{{ row.etf_code }} - {{ row.signal_label }} ({{ row.signal_score }})</li>
        </ul>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { fetchSystemStatus } from '../api/runs'
import { useSignalStore } from '../stores/signals'

const systemStatus = ref<Record<string, unknown>>({})
const signalStore = useSignalStore()
const signals = computed(() => signalStore.items)

onMounted(async () => {
  systemStatus.value = await fetchSystemStatus()
  await signalStore.loadLatest('three_factor_guard')
})
</script>

<style scoped>
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.card { background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.08); }
</style>
