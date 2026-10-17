<template>
  <div v-if="issues.length || notes.length" class="issue-list">
    <div v-for="item in issues" :key="item.code + item.message" class="issue" :class="item.level">
      <span class="issue-level">{{ LEVEL_TEXT[item.level] ?? item.level }}</span>
      {{ item.message }}
    </div>
    <ul v-if="notes.length" class="notes">
      <li v-for="note in notes" :key="note">{{ note }}</li>
    </ul>
  </div>
</template>

<script setup lang="ts">
/** 数据问题与口径说明列表。 */
import type { SignalLabIssue } from '../../types/api'

withDefaults(defineProps<{ issues: SignalLabIssue[]; notes?: string[] }>(), {
  notes: () => [],
})

const LEVEL_TEXT: Record<string, string> = { info: '提示', warn: '注意', error: '错误' }
</script>

<style scoped>
.issue-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.issue {
  font-size: 12px;
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  line-height: 1.6;
}
.issue.info {
  background: rgba(59, 130, 246, 0.08);
  color: #93c5fd;
}
.issue.warn {
  background: rgba(245, 158, 11, 0.1);
  color: #fcd34d;
}
.issue.error {
  background: rgba(239, 68, 68, 0.12);
  color: #fca5a5;
}
.issue-level {
  font-weight: 600;
  margin-right: 6px;
}
.notes {
  margin: 0;
  padding-left: 18px;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.7;
}
</style>
