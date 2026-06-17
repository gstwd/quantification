<template>
  <div class="observation-editor">
    <div class="editor-header">
      <span class="section-order">{{ sortOrder }}.</span>
      <label class="section-label">{{ sectionLabel }}</label>
      <span class="word-count" v-if="charCount > 0">{{ charCount }} 字</span>
    </div>
    <p class="guide-hint">{{ guideHint }}</p>
    <textarea
      class="editor-textarea"
      :value="modelValue ?? ''"
      :placeholder="`记录${sectionLabel}...`"
      rows="3"
      @input="onInput"
    ></textarea>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  sectionKey: string
  sectionLabel: string
  guideHint: string
  modelValue: string | null
  sortOrder: number
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const charCount = computed(() => (props.modelValue ?? '').length)

function onInput(event: Event): void {
  const val = (event.target as HTMLTextAreaElement).value
  emit('update:modelValue', val)
}
</script>

<script lang="ts">
import { computed, defineComponent } from 'vue'
export default defineComponent({ name: 'ObservationEditor' })
</script>

<style scoped>
.observation-editor {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 16px;
}
.editor-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.section-order {
  font-size: 12px;
  color: var(--text-muted);
  min-width: 20px;
}
.section-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  flex: 1;
}
.word-count {
  font-size: 11px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
.guide-hint {
  font-size: 12px;
  color: var(--text-muted);
  margin: 0 0 8px 28px;
  line-height: 1.4;
}
.editor-textarea {
  display: block;
  width: 100%;
  min-height: 72px;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 13px;
  line-height: 1.6;
  font-family: inherit;
  resize: vertical;
  outline: none;
  transition: border-color 0.15s;
}
.editor-textarea:focus {
  border-color: var(--accent);
}
.editor-textarea::placeholder {
  color: var(--text-muted);
  opacity: 0.6;
}
</style>
