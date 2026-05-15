<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">策略中心</h1>
    </div>
    <div v-if="store.loading" class="loading">加载中...</div>
    <div v-else class="grid">
      <div v-for="item in store.items" :key="item.strategy_id" class="strategy-card">
        <div class="card-top">
          <h3 class="strategy-name">{{ item.display_name }}</h3>
          <div class="chips">
            <span class="chip chip-version">v{{ item.version }}</span>
            <span class="chip chip-freq">{{ item.frequency }}</span>
            <span class="chip chip-scope">{{ item.asset_scope }}</span>
          </div>
        </div>
        <p class="strategy-desc">{{ item.description }}</p>
        <div class="card-footer">
          <div class="inputs-label">输入：<span class="text-muted">{{ item.required_inputs.join(', ') }}</span></div>
          <RouterLink :to="`/strategies/${item.strategy_id}`" class="detail-btn">查看详情 →</RouterLink>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink } from 'vue-router'

import { useStrategyStore } from '../stores/strategies'

const store = useStrategyStore()
onMounted(() => store.loadAll())
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-header { display: flex; align-items: center; }
.page-title { font-size: 22px; font-weight: 700; }
.loading { padding: 60px; text-align: center; color: var(--text-muted); }

.grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }

.strategy-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color 0.15s;
}
.strategy-card:hover { border-color: var(--accent); }

.card-top { display: flex; flex-direction: column; gap: 8px; }
.strategy-name { font-size: 16px; font-weight: 600; }

.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; }
.chip-version { background: rgba(59,130,246,0.15); color: #60a5fa; }
.chip-freq { background: rgba(34,197,94,0.12); color: #4ade80; }
.chip-scope { background: var(--surface-2); color: var(--text-muted); }

.strategy-desc { font-size: 13px; color: var(--text-muted); line-height: 1.6; flex: 1; }

.card-footer { display: flex; align-items: center; justify-content: space-between; margin-top: auto; }
.inputs-label { font-size: 12px; color: var(--text-muted); }
.text-muted { color: var(--text-muted); }

.detail-btn {
  font-size: 13px;
  color: var(--accent);
  font-weight: 500;
  transition: color 0.15s;
}
.detail-btn:hover { color: var(--accent-hover); }
</style>
