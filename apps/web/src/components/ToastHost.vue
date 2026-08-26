<template>
  <div class="toast-host">
    <TransitionGroup name="toast">
      <div v-for="item in store.items" :key="item.id" :class="['toast-item', `toast-${item.level}`]">
        <div class="toast-head">
          <span class="toast-level">{{ levelLabel(item.level) }}</span>
          <button class="toast-close" title="关闭" @click="store.remove(item.id)">×</button>
        </div>
        <div v-if="item.title" class="toast-title">{{ item.title }}</div>
        <div class="toast-message">{{ item.message }}</div>
      </div>
    </TransitionGroup>
  </div>
</template>

<script setup lang="ts">
import { onUnmounted, watch } from 'vue'

import { useToastStore, type ToastLevel } from '../stores/toast'

const store = useToastStore()

/** 已排定自动关闭的定时器，key=弹窗 id */
const timers = new Map<number, ReturnType<typeof setTimeout>>()

function levelLabel(level: ToastLevel): string {
  const map: Record<ToastLevel, string> = { info: '信息', warning: '警告', error: '错误' }
  return map[level]
}

// 新弹窗出现时排定自动关闭定时器
watch(
  () => store.items.map((item) => item.id),
  () => {
    for (const item of store.items) {
      if (!timers.has(item.id)) {
        timers.set(
          item.id,
          setTimeout(() => {
            store.remove(item.id)
            timers.delete(item.id)
          }, item.duration),
        )
      }
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  for (const timer of timers.values()) clearTimeout(timer)
  timers.clear()
})
</script>

<style scoped>
.toast-host {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-width: 380px;
}

.toast-item {
  background: var(--surface);
  border: 1px solid var(--border);
  border-left-width: 4px;
  border-radius: var(--radius-sm);
  box-shadow: var(--shadow);
  padding: 10px 12px;
  min-width: 260px;
}

.toast-info { border-left-color: var(--accent); }
.toast-warning { border-left-color: var(--warning); }
.toast-error { border-left-color: var(--danger); }

.toast-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.toast-level {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
}
.toast-info .toast-level { color: var(--accent); }
.toast-warning .toast-level { color: var(--warning); }
.toast-error .toast-level { color: var(--danger); }

.toast-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
  margin-bottom: 2px;
}

.toast-message {
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-muted);
  word-break: break-all;
}

.toast-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
}
.toast-close:hover { color: var(--text); }

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translateX(16px);
}
</style>
