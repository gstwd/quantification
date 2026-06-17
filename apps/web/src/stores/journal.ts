import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  fetchJournalCalendar,
  fetchJournalEntries,
  createJournalEntry,
  fetchJournalEntry,
  fetchJournalEntryByDate,
  updateJournalEntry,
  deleteJournalEntry,
  refreshSnapshots,
  saveObservations,
  fetchTags,
  createTag,
  setEntryTags,
  triggerAIAnalysis,
} from '../api/journal'
import type {
  CalendarResponse,
  JournalEntryCreate,
  JournalEntryDetail,
  JournalEntrySummary,
  JournalEntryUpdate,
  ObservationsBatchUpdate,
  SetTagsRequest,
  TagCreate,
  TagSummary,
  PaginatedResponse,
} from '../types/api'

export const useJournalStore = defineStore('journal', () => {
  // ===== 状态 =====
  const currentEntry = ref<JournalEntryDetail | null>(null)
  const calendarData = ref<CalendarResponse | null>(null)
  const tags = ref<TagSummary[]>([])
  const entryList = ref<JournalEntrySummary[]>([])
  const listTotal = ref(0)
  const loading = ref(false)
  const saving = ref(false)
  const error = ref<string | null>(null)

  // ===== 日历 =====
  async function loadCalendar(year: number, month?: number): Promise<void> {
    loading.value = true
    error.value = null
    try {
      calendarData.value = await fetchJournalCalendar(year, month)
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '加载日历失败'
    } finally {
      loading.value = false
    }
  }

  // ===== 日志 CRUD =====
  async function loadEntry(entryId: string): Promise<void> {
    loading.value = true
    error.value = null
    try {
      currentEntry.value = await fetchJournalEntry(entryId)
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '加载日志失败'
    } finally {
      loading.value = false
    }
  }

  async function loadEntryByDate(tradeDate: string): Promise<JournalEntryDetail | null> {
    loading.value = true
    error.value = null
    try {
      currentEntry.value = await fetchJournalEntryByDate(tradeDate)
      return currentEntry.value
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '加载日志失败'
      return null
    } finally {
      loading.value = false
    }
  }

  async function createEntry(tradeDate: string): Promise<JournalEntryDetail | null> {
    saving.value = true
    error.value = null
    try {
      const req: JournalEntryCreate = { trade_date: tradeDate }
      currentEntry.value = await createJournalEntry(req)
      return currentEntry.value
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '创建日志失败'
      return null
    } finally {
      saving.value = false
    }
  }

  async function updateEntry(entryId: string, data: JournalEntryUpdate): Promise<boolean> {
    saving.value = true
    error.value = null
    try {
      currentEntry.value = await updateJournalEntry(entryId, data)
      return true
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '更新日志失败'
      return false
    } finally {
      saving.value = false
    }
  }

  async function removeEntry(entryId: string): Promise<boolean> {
    saving.value = true
    error.value = null
    try {
      await deleteJournalEntry(entryId)
      currentEntry.value = null
      return true
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '删除日志失败'
      return false
    } finally {
      saving.value = false
    }
  }

  async function loadEntryList(params: {
    date_from?: string
    date_to?: string
    tag?: string
    phase?: string
    is_complete?: boolean
    offset?: number
    limit?: number
  } = {}): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const res: PaginatedResponse<JournalEntrySummary> = await fetchJournalEntries(params)
      entryList.value = res.items
      listTotal.value = res.total
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '加载列表失败'
    } finally {
      loading.value = false
    }
  }

  // ===== 快照 =====
  async function refreshEntrySnapshots(entryId: string): Promise<void> {
    saving.value = true
    try {
      const snapshots = await refreshSnapshots(entryId)
      if (currentEntry.value) {
        currentEntry.value.index_snapshots = snapshots
      }
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '刷新快照失败'
    } finally {
      saving.value = false
    }
  }

  // ===== 观察 =====
  async function saveEntryObservations(entryId: string, data: ObservationsBatchUpdate): Promise<void> {
    saving.value = true
    error.value = null
    try {
      const rows = await saveObservations(entryId, data)
      if (currentEntry.value) {
        currentEntry.value.observations = rows
        // 更新字数
        currentEntry.value.word_count = rows.reduce((sum, r) => sum + (r.content?.length || 0), 0)
      }
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '保存观察失败'
    } finally {
      saving.value = false
    }
  }

  // ===== 标签 =====
  async function loadTags(): Promise<void> {
    try {
      tags.value = await fetchTags()
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '加载标签失败'
    }
  }

  async function addTag(data: TagCreate): Promise<TagSummary | null> {
    try {
      const tag = await createTag(data)
      tags.value.push(tag)
      return tag
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '创建标签失败'
      return null
    }
  }

  async function updateEntryTags(entryId: string, tagIds: string[]): Promise<boolean> {
    saving.value = true
    error.value = null
    try {
      const req: SetTagsRequest = { tag_ids: tagIds }
      const updatedTags = await setEntryTags(entryId, req)
      if (currentEntry.value) {
        currentEntry.value.tags = updatedTags
      }
      return true
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '设置标签失败'
      return false
    } finally {
      saving.value = false
    }
  }

  // ===== AI 分析 =====
  async function requestAIAnalysis(entryId: string): Promise<boolean> {
    saving.value = true
    error.value = null
    try {
      await triggerAIAnalysis(entryId)
      return true
    } catch (e: unknown) {
      error.value = e instanceof Error ? e.message : '触发AI分析失败'
      return false
    } finally {
      saving.value = false
    }
  }

  // ===== 工具方法 =====
  function clearError(): void {
    error.value = null
  }

  return {
    currentEntry,
    calendarData,
    tags,
    entryList,
    listTotal,
    loading,
    saving,
    error,
    loadCalendar,
    loadEntry,
    loadEntryByDate,
    createEntry,
    updateEntry,
    removeEntry,
    loadEntryList,
    refreshEntrySnapshots,
    saveEntryObservations,
    loadTags,
    addTag,
    updateEntryTags,
    requestAIAnalysis,
    clearError,
  }
})
