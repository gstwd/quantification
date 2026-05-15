<template>
  <div class="page">
    <div v-if="store.loading" class="loading">加载中...</div>
    <template v-else-if="store.current">
      <div class="page-header">
        <h1 class="page-title">{{ store.current.display_name }}</h1>
        <div class="chips">
          <span class="chip chip-version">v{{ store.current.version }}</span>
          <span class="chip chip-freq">{{ store.current.frequency }}</span>
          <span class="chip chip-scope">{{ store.current.asset_scope }}</span>
        </div>
      </div>
      <p class="description">{{ store.current.description }}</p>

      <div class="two-col">
        <div class="card">
          <div class="card-header">元数据</div>
          <div class="meta-list">
            <div class="meta-row">
              <span class="meta-key">策略 ID</span>
              <span class="meta-val mono">{{ store.current.strategy_id }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-key">频率</span>
              <span class="meta-val">{{ store.current.frequency }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-key">资产范围</span>
              <span class="meta-val">{{ store.current.asset_scope }}</span>
            </div>
            <div class="meta-row">
              <span class="meta-key">必要输入</span>
              <div class="chips-inline">
                <span v-for="inp in store.current.required_inputs" :key="inp" class="chip chip-input">{{ inp }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">因子定义</div>
          <div v-if="store.current.factors.length === 0" class="empty">无因子定义</div>
          <div v-else class="factor-list">
            <div v-for="(f, i) in store.current.factors" :key="i" class="factor-item">
              <div class="factor-id mono">{{ f.factor_id ?? f.id ?? `factor_${i}` }}</div>
              <div class="factor-desc text-muted">{{ f.description ?? f.name ?? '' }}</div>
              <div v-if="f.weight !== undefined" class="factor-weight">权重 {{ f.weight }}</div>
            </div>
          </div>
        </div>
      </div>
    </template>
    <div v-else class="loading">策略不存在</div>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'

import { useStrategyStore } from '../stores/strategies'

const props = defineProps<{ strategyId: string }>()
const store = useStrategyStore()
onMounted(() => store.loadOne(props.strategyId))
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.loading { padding: 60px; text-align: center; color: var(--text-muted); }

.page-header { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.page-title { font-size: 22px; font-weight: 700; }

.chips { display: flex; gap: 6px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; }
.chip-version { background: rgba(59,130,246,0.15); color: #60a5fa; }
.chip-freq { background: rgba(34,197,94,0.12); color: #4ade80; }
.chip-scope { background: var(--surface-2); color: var(--text-muted); }
.chip-input { background: var(--surface-2); color: var(--text-muted); }

.description { color: var(--text-muted); font-size: 14px; line-height: 1.7; max-width: 700px; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.card-header {
  padding: 12px 20px;
  font-size: 13px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.meta-list { padding: 8px 0; }
.meta-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 10px 20px;
  border-bottom: 1px solid rgba(51,65,85,0.4);
}
.meta-row:last-child { border-bottom: none; }
.meta-key { font-size: 12px; color: var(--text-muted); min-width: 80px; padding-top: 2px; }
.meta-val { font-size: 13px; }
.chips-inline { display: flex; gap: 4px; flex-wrap: wrap; }

.empty { padding: 32px 20px; text-align: center; color: var(--text-muted); }

.factor-list { padding: 8px 0; }
.factor-item {
  padding: 12px 20px;
  border-bottom: 1px solid rgba(51,65,85,0.4);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.factor-item:last-child { border-bottom: none; }
.factor-id { font-size: 13px; font-weight: 600; }
.factor-desc { font-size: 12px; }
.factor-weight { font-size: 11px; color: var(--accent); }

.mono { font-family: monospace; }
.text-muted { color: var(--text-muted); }
</style>
