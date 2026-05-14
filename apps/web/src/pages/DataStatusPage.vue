<template>
  <section>
    <h2>数据状态</h2>
    <div class="card">
      <pre>{{ JSON.stringify(status, null, 2) }}</pre>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { fetchSystemStatus } from '../api/runs'

const status = ref<Record<string, unknown>>({})

onMounted(async () => {
  status.value = await fetchSystemStatus()
})
</script>

<style scoped>
.card { background: #fff; border-radius: 12px; padding: 16px; }
</style>
