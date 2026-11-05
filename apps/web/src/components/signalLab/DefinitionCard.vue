<template>
  <div class="definition">
    <div class="def-head">
      <span class="def-title">{{ definition.label }}</span>
      <span class="def-source">{{ definition.source }}</span>
    </div>
    <div class="def-row">
      <span class="def-label">公式</span>
      <code class="def-formula">{{ definition.formula }}</code>
    </div>
    <div class="def-row">
      <span class="def-label">原理</span>
      <span class="def-text">{{ definition.principle }}</span>
    </div>
    <div class="def-row">
      <span class="def-label">依赖字段</span>
      <span class="def-text">{{ fieldText }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 因子 / 信号的公式、原理与数据依赖说明卡片。 */
import { computed } from 'vue'

import type { SignalLabDefinition } from '../../types/api'

const props = defineProps<{ definition: SignalLabDefinition }>()

const FIELD_LABELS: Record<string, string> = {
  open: '开盘',
  high: '最高',
  low: '最低',
  close: '收盘',
  volume: '成交量',
  amount: '成交额',
}

const fieldText = computed(() =>
  props.definition.required_fields.map((f) => FIELD_LABELS[f] ?? f).join('、'),
)
</script>

<style scoped>
.definition {
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
}
.def-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.def-title {
  font-weight: 600;
  font-size: 14px;
}
.def-source {
  color: var(--text-muted);
  font-size: 12px;
}
.def-row {
  display: flex;
  gap: 10px;
  font-size: 12px;
  line-height: 1.7;
}
.def-label {
  flex: 0 0 56px;
  color: #64748b;
}
.def-formula {
  color: #93c5fd;
  font-family: var(--font-mono);
  white-space: pre-wrap;
  word-break: break-word;
}
.def-text {
  color: #cbd5e1;
}
</style>
