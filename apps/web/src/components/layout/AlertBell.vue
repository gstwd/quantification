<template>
  <div class="alert-bell">
    <button class="bell-btn" type="button" :title="`站内告警（未读 ${unread}）`" @click="toggle">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
        <path d="M13.73 21a2 2 0 0 1-3.46 0" />
      </svg>
      <span>告警</span>
      <span v-if="unread > 0" class="badge">{{ unread > 99 ? '99+' : unread }}</span>
    </button>
    <div v-if="open" class="panel">
      <div class="panel-header">
        <span>站内告警</span>
        <button v-if="unread > 0" type="button" class="link" @click="readAll">全部已读</button>
      </div>
      <div v-if="items.length === 0" class="empty">暂无告警</div>
      <div
        v-for="item in items"
        :key="item.id"
        :class="['item', `sev-${item.severity}`, { unread: !item.read_at }]"
        @click="openItem(item)"
      >
        <div class="title">{{ item.title }}</div>
        <div class="meta">{{ formatCnTime(item.created_at ?? '') }} · {{ kindLabel(item.kind) }}</div>
      </div>
      <div class="hint">告警只做提醒：不会自动调参、上下线或产生交易信号。</div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 侧栏告警铃：每分钟拉取一次未读数（usePolling），点开查看最近告警并标记已读。
 */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { fetchAlerts, markAlertRead, markAllAlertsRead } from '../../api/alerts'
import { usePolling } from '../../composables/usePolling'
import type { AlertItem, AlertListResponse } from '../../types/api'
import { formatCnTime } from '../../utils/date'

const router = useRouter()
const open = ref(false)
const unread = ref(0)
const items = ref<AlertItem[]>([])

/** 用最新列表刷新状态 */
function apply(data: AlertListResponse): void {
  unread.value = data.unread_count
  items.value = data.items
}

const polling = usePolling<AlertListResponse>({
  fetcher: () => fetchAlerts(false, 30),
  // 告警需要持续关注，永不判定完成
  isDone: () => false,
  intervalMs: 60_000,
  onData: apply,
})

/** 告警类型中文名 */
function kindLabel(kind: string): string {
  const labels: Record<string, string> = {
    health_degraded: '健康等级恶化',
    lifecycle_refresh_failed: '定时体检失败',
    data_sync_failed: '数据同步失败',
  }
  return labels[kind] ?? kind
}

async function toggle(): Promise<void> {
  open.value = !open.value
  if (open.value) apply(await fetchAlerts(false, 30))
}

async function openItem(item: AlertItem): Promise<void> {
  if (!item.read_at) {
    await markAlertRead(item.id)
    apply(await fetchAlerts(false, 30))
  }
  open.value = false
  if (item.strategy_id) router.push('/lifecycle')
  else if (item.kind === 'data_sync_failed') router.push('/data-management')
}

async function readAll(): Promise<void> {
  await markAllAlertsRead()
  apply(await fetchAlerts(false, 30))
}

onMounted(() => polling.start())
</script>

<style scoped>
.alert-bell { position: relative; margin: 0 12px 12px; }
.bell-btn {
  display: flex; align-items: center; gap: 8px; width: 100%;
  background: transparent; border: 1px solid var(--border); border-radius: var(--radius-sm);
  color: var(--text-muted); padding: 6px 10px; cursor: pointer; font-size: 13px;
}
.bell-btn:hover { color: var(--text); border-color: var(--accent); }
.badge {
  margin-left: auto; background: var(--danger, #ef4444); color: #fff;
  border-radius: 10px; padding: 0 6px; font-size: 11px; line-height: 18px;
}
.panel {
  position: absolute; left: 0; top: calc(100% + 6px); z-index: 50; width: 320px; max-height: 420px; overflow-y: auto;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
}
.panel-header { display: flex; justify-content: space-between; padding: 8px 12px; font-weight: 600; font-size: 13px; }
.link { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 12px; }
.empty, .hint { padding: 10px 12px; color: var(--text-muted); font-size: 12px; }
.item { padding: 8px 12px; border-top: 1px solid var(--border); cursor: pointer; border-left: 3px solid transparent; }
.item:hover { background: var(--surface-2); }
.item.unread .title { font-weight: 600; }
.sev-critical { border-left-color: var(--danger, #ef4444); }
.sev-warning { border-left-color: #f59e0b; }
.title { font-size: 13px; color: var(--text); }
.meta { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
</style>
