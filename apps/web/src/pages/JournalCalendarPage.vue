<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">市场日志</h1>
      </div>
      <div class="header-actions">
        <button class="btn-primary" @click="goToToday">今日日志</button>
      </div>
    </div>

    <div class="calendar-toolbar">
      <button class="btn-nav" @click="prevMonth">&lt;</button>
      <span class="current-month">{{ year }} 年 {{ month }} 月</span>
      <button class="btn-nav" @click="nextMonth">&gt;</button>
      <button class="btn-nav btn-today" @click="goToCurrentMonth">本月</button>
    </div>

    <div v-if="store.loading" class="loading">加载中...</div>
    <div v-else-if="store.error" class="error-tip">{{ store.error }}</div>

    <div v-else class="calendar-grid">
      <div class="weekday-header">
        <span v-for="d in weekdays" :key="d">{{ d }}</span>
      </div>
      <div class="days-grid">
        <div
          v-for="(day, idx) in calendarDays"
          :key="idx"
          class="day-cell"
          :class="dayCellClass(day)"
          @click="onDayClick(day)"
        >
          <span class="day-number">{{ day.dayOfMonth }}</span>
          <div v-if="day.hasEntry" class="day-info">
            <span class="day-phase">{{ phaseLabel(day.marketPhase) }}</span>
            <div class="day-tags" v-if="day.tags.length > 0">
              <span
                v-for="tag in day.tags.slice(0, 2)"
                :key="tag.id"
                class="mini-tag"
                :style="{ background: tag.color }"
              ></span>
              <span v-if="day.tags.length > 2" class="more-tags">+{{ day.tags.length - 2 }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="phase-legend">
      <span v-for="p in phaseList" :key="p.key" class="legend-item">
        <span class="legend-dot" :class="'phase-' + p.key"></span>
        {{ p.label }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useJournalStore } from '../stores/journal'
import type { CalendarDay } from '../types/api'

const router = useRouter()
const store = useJournalStore()

const weekdays = ['一', '二', '三', '四', '五', '六', '日']

const phaseList = [
  { key: 'trending_up', label: '趋势上涨' },
  { key: 'trending_down', label: '趋势下跌' },
  { key: 'ranging', label: '震荡' },
  { key: 'rotation', label: '轮动' },
  { key: 'euphoria', label: '情绪高潮' },
  { key: 'panic', label: '恐慌' },
  { key: 'repair', label: '修复' },
]

const now = new Date()
const year = ref(now.getFullYear())
const month = ref(now.getMonth() + 1)

const calendarDays = computed(() => {
  if (!store.calendarData) return []
  const days = store.calendarData.days
  if (days.length === 0) return []

  const firstDate = new Date(days[0].date + 'T00:00:00')
  const startDayOfWeek = (firstDate.getDay() + 6) % 7 // Monday=0

  const result: Array<CalendarDay & { dayOfMonth: number; isEmpty: boolean }> = []

  // Fill leading empty cells
  for (let i = 0; i < startDayOfWeek; i++) {
    result.push({
      date: '', is_trading_day: false, has_entry: false, entry_id: null,
      market_phase: null, market_temperature: null, tags: [], one_line_summary: null,
      dayOfMonth: 0, isEmpty: true,
    })
  }

  for (const day of days) {
    const d = new Date(day.date + 'T00:00:00')
    result.push({ ...day, dayOfMonth: d.getDate(), isEmpty: false })
  }

  return result
})

function dayCellClass(day: any): Record<string, boolean> {
  const cls: Record<string, boolean> = {}
  if (day.isEmpty) cls['cell-empty'] = true
  else if (!day.is_trading_day) cls['cell-nontrading'] = true
  else if (day.has_entry) {
    cls['cell-has-entry'] = true
    if (day.market_phase) cls['phase-' + day.market_phase] = true
  } else {
    cls['cell-no-entry'] = true
  }
  return cls
}

function phaseLabel(phase: string | null): string {
  const found = phaseList.find((p) => p.key === phase)
  return found ? found.label : ''
}

function prevMonth(): void {
  if (month.value === 1) { year.value--; month.value = 12 }
  else month.value--
  loadCalendar()
}

function nextMonth(): void {
  if (month.value === 12) { year.value++; month.value = 1 }
  else month.value++
  loadCalendar()
}

function goToCurrentMonth(): void {
  const n = new Date()
  year.value = n.getFullYear()
  month.value = n.getMonth() + 1
  loadCalendar()
}

async function onDayClick(day: any): Promise<void> {
  if (day.isEmpty || !day.is_trading_day) return
  if (day.hasEntry && day.entry_id) {
    router.push(`/journal/${day.entry_id}`)
  } else {
    // Create entry for this date
    const entry = await store.createEntry(day.date)
    if (entry) {
      router.push(`/journal/${entry.id}`)
    }
  }
}

async function goToToday(): Promise<void> {
  const today = new Date().toISOString().slice(0, 10)
  const entry = await store.loadEntryByDate(today)
  if (entry) {
    router.push(`/journal/${entry.id}`)
  } else {
    // Try to create
    const created = await store.createEntry(today)
    if (created) {
      router.push(`/journal/${created.id}`)
    }
  }
}

function loadCalendar(): void {
  store.loadCalendar(year.value, month.value)
}

onMounted(() => {
  loadCalendar()
})
</script>

<style scoped>
.page { padding: 24px; max-width: 1100px; margin: 0 auto; }
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.page-title { font-size: 22px; font-weight: 700; color: var(--text); margin: 0; }
.btn-primary {
  padding: 8px 20px;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
}
.btn-primary:hover { background: var(--accent-hover); }

.calendar-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.current-month { font-size: 18px; font-weight: 600; color: var(--text); min-width: 140px; text-align: center; }
.btn-nav {
  padding: 4px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  cursor: pointer;
  font-size: 14px;
}
.btn-nav:hover { background: var(--surface-2); }
.btn-today { font-size: 12px; padding: 4px 10px; }

.weekday-header {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 2px;
  margin-bottom: 4px;
}
.weekday-header span {
  text-align: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted);
  padding: 6px 0;
}

.days-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 2px;
}
.day-cell {
  aspect-ratio: 1;
  border-radius: var(--radius-sm);
  padding: 6px;
  cursor: default;
  transition: background 0.1s;
  min-height: 72px;
  display: flex;
  flex-direction: column;
}
.day-number { font-size: 13px; font-weight: 500; color: var(--text-muted); }
.cell-empty { background: transparent; }
.cell-nontrading { background: var(--surface-2); opacity: 0.4; }
.cell-no-entry { background: var(--surface); cursor: pointer; }
.cell-no-entry:hover { background: var(--surface-2); }
.cell-has-entry { background: var(--surface); cursor: pointer; }

.cell-has-entry.phase-trending_up { border-left: 3px solid #22c55e; }
.cell-has-entry.phase-trending_down { border-left: 3px solid #3b82f6; }
.cell-has-entry.phase-ranging { border-left: 3px solid #eab308; }
.cell-has-entry.phase-rotation { border-left: 3px solid #f97316; }
.cell-has-entry.phase-euphoria { border-left: 3px solid #ef4444; }
.cell-has-entry.phase-panic { border-left: 3px solid #991b1b; }
.cell-has-entry.phase-repair { border-left: 3px solid #14b8a6; }

.day-info { margin-top: 4px; }
.day-phase { font-size: 10px; color: var(--text-muted); display: block; }
.day-tags { display: flex; gap: 3px; margin-top: 3px; flex-wrap: wrap; }
.mini-tag { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.more-tags { font-size: 9px; color: var(--text-muted); }

.phase-legend {
  margin-top: 16px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
}
.legend-item { font-size: 12px; color: var(--text-muted); display: flex; align-items: center; gap: 4px; }
.legend-dot { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.legend-dot.phase-trending_up { background: #22c55e; }
.legend-dot.phase-trending_down { background: #3b82f6; }
.legend-dot.phase-ranging { background: #eab308; }
.legend-dot.phase-rotation { background: #f97316; }
.legend-dot.phase-euphoria { background: #ef4444; }
.legend-dot.phase-panic { background: #991b1b; }
.legend-dot.phase-repair { background: #14b8a6; }

.loading { text-align: center; padding: 60px 0; color: var(--text-muted); font-size: 14px; }
.error-tip { padding: 12px 16px; background: rgba(239,68,68,.1); border: 1px solid rgba(239,68,68,.3); border-radius: var(--radius-sm); color: #ef4444; font-size: 13px; margin-bottom: 16px; }
</style>
