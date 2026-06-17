<template>
  <div class="sentiment-panel">
    <div class="panel-header">
      <h3>主观评分</h3>
      <span class="panel-hint">0=极低，50=中性，100=极高。留空表示未评估。</span>
    </div>
    <div class="score-grid">
      <div
        v-for="score in scores"
        :key="score.key"
        class="score-item"
        :class="{ 'score-filled': score.value !== null }"
      >
        <div class="score-header">
          <label :for="`score-${score.key}`">{{ score.label }}</label>
          <span class="score-value" :class="scoreLevelClass(score.value)">{{
            score.value !== null ? score.value : '--'
          }}</span>
        </div>
        <input
          :id="`score-${score.key}`"
          type="range"
          min="0"
          max="100"
          step="1"
          :value="score.value ?? 50"
          class="score-slider"
          @input="onSliderChange(score.key, $event)"
          @change="onSliderChange(score.key, $event)"
        />
        <div class="score-labels">
          <span class="label-low">0</span>
          <span class="label-mid">50</span>
          <span class="label-high">100</span>
        </div>
        <div class="score-guide">
          <span class="guide-tier tier-cold" :class="{ active: score.value !== null && score.value <= 20 }">
            极低 0-20
          </span>
          <span class="guide-tier tier-low" :class="{ active: score.value !== null && score.value > 20 && score.value <= 40 }">
            偏低 21-40
          </span>
          <span class="guide-tier tier-mid" :class="{ active: score.value !== null && score.value > 40 && score.value <= 60 }">
            中性 41-60
          </span>
          <span class="guide-tier tier-high" :class="{ active: score.value !== null && score.value > 60 && score.value <= 80 }">
            偏高 61-80
          </span>
          <span class="guide-tier tier-hot" :class="{ active: score.value !== null && score.value > 80 }">
            极高 81-100
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
const SCORE_DEFS = [
  { key: 'market_temperature', label: '市场温度' },
  { key: 'profit_effect', label: '赚钱效应' },
  { key: 'risk_preference', label: '风险偏好' },
  { key: 'trading_difficulty', label: '交易难度' },
  { key: 'market_consistency', label: '市场一致性' },
] as const

const props = defineProps<{
  modelValue: Record<string, number | null>
}>()

const emit = defineEmits<{
  'update:modelValue': [value: Record<string, number | null>]
}>()

const scores = computed(() =>
  SCORE_DEFS.map((s) => ({
    key: s.key,
    label: s.label,
    value: props.modelValue[s.key] ?? null,
  }))
)

function onSliderChange(key: string, event: Event): void {
  const val = parseInt((event.target as HTMLInputElement).value, 10)
  emit('update:modelValue', { ...props.modelValue, [key]: val })
}

function scoreLevelClass(value: number | null): string {
  if (value === null) return ''
  if (value <= 20) return 'level-cold'
  if (value <= 40) return 'level-low'
  if (value <= 60) return 'level-mid'
  if (value <= 80) return 'level-high'
  return 'level-hot'
}
</script>

<script lang="ts">
import { computed, defineComponent } from 'vue'
export default defineComponent({ name: 'SentimentScorePanel' })
</script>

<style scoped>
.sentiment-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 20px;
}
.panel-header {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 16px;
}
.panel-header h3 {
  margin: 0;
  font-size: 15px;
  color: var(--text);
}
.panel-hint {
  font-size: 12px;
  color: var(--text-muted);
}
.score-grid {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.score-item {
  padding: 8px 0;
}
.score-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.score-header label {
  font-size: 13px;
  color: var(--text);
}
.score-value {
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--text-muted);
  min-width: 32px;
  text-align: right;
}
.score-value.level-cold { color: #60a5fa; }
.score-value.level-low { color: #94a3b8; }
.score-value.level-mid { color: #fbbf24; }
.score-value.level-high { color: #f97316; }
.score-value.level-hot { color: #ef4444; }
.score-slider {
  width: 100%;
  height: 6px;
  -webkit-appearance: none;
  appearance: none;
  background: var(--surface-2);
  border-radius: 3px;
  outline: none;
  cursor: pointer;
}
.score-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--accent);
  cursor: pointer;
}
.score-labels {
  display: flex;
  justify-content: space-between;
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 2px;
}
.score-guide {
  display: flex;
  gap: 2px;
  margin-top: 4px;
}
.guide-tier {
  flex: 1;
  text-align: center;
  font-size: 9px;
  padding: 2px 0;
  border-radius: 2px;
  color: var(--text-muted);
  background: transparent;
}
.guide-tier.active {
  color: #fff;
  font-weight: 600;
}
.guide-tier.tier-cold.active { background: #3b82f6; }
.guide-tier.tier-low.active { background: #64748b; }
.guide-tier.tier-mid.active { background: #ca8a04; }
.guide-tier.tier-high.active { background: #ea580c; }
.guide-tier.tier-hot.active { background: #dc2626; }
</style>
