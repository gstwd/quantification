<template>
  <div class="page">
    <div v-if="store.loading && !store.currentEntry" class="loading">加载中...</div>
    <div v-else-if="store.error && !store.currentEntry" class="error-tip">{{ store.error }}</div>

    <template v-else-if="store.currentEntry">
      <div class="page-header">
        <div class="header-left">
          <RouterLink to="/journal/calendar" class="back-link">&larr; 日历</RouterLink>
          <h1 class="page-title">{{ store.currentEntry.trade_date }} 市场日志</h1>
          <span class="status-badge" :class="store.currentEntry.is_complete ? 'status-success' : 'status-pending'">
            {{ store.currentEntry.is_complete ? '已完成' : '草稿' }}
          </span>
        </div>
        <div class="header-actions">
          <select
            :value="store.currentEntry.market_phase ?? ''"
            class="phase-select"
            @change="onPhaseChange(($event.target as HTMLSelectElement).value)"
          >
            <option value="">-- 市场阶段 --</option>
            <option value="trending_up">趋势上涨</option>
            <option value="trending_down">趋势下跌</option>
            <option value="ranging">震荡</option>
            <option value="rotation">轮动</option>
            <option value="euphoria">情绪高潮</option>
            <option value="panic">恐慌</option>
            <option value="repair">修复</option>
          </select>
          <input
            type="text"
            class="summary-input"
            placeholder="一句话概括今日市场..."
            :value="store.currentEntry.one_line_summary ?? ''"
            @change="onSummaryChange(($event.target as HTMLInputElement).value)"
          />
          <button class="btn-primary" :disabled="store.saving" @click="handleSave">
            {{ store.saving ? '保存中...' : '保存' }}
          </button>
          <button class="btn-danger" @click="handleDelete">删除</button>
        </div>
      </div>

      <div class="tab-bar">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          class="tab-btn"
          :class="{ active: activeTab === tab.key }"
          @click="activeTab = tab.key"
        >{{ tab.label }}</button>
      </div>

      <!-- Tab: 市场概览 -->
      <div v-if="activeTab === 'overview'" class="tab-content">
        <SentimentScorePanel
          :modelValue="sentimentScores"
          @update:modelValue="onSentimentChange"
        />
        <div class="section-gap"></div>
        <MarketDataForm
          :modelValue="marketData"
          @update:modelValue="onMarketDataChange"
        />
      </div>

      <!-- Tab: 指数快照 -->
      <div v-if="activeTab === 'snapshot'" class="tab-content">
        <IndexSnapshotTable
          :snapshots="store.currentEntry.index_snapshots"
          :loading="store.saving"
          @refresh="onRefreshSnapshots"
        />
      </div>

      <!-- Tab: 观察笔记 -->
      <div v-if="activeTab === 'observations'" class="tab-content">
        <ObservationEditor
          v-for="obs in store.currentEntry.observations"
          :key="obs.id"
          :sectionKey="obs.section_key"
          :sectionLabel="obs.section_label"
          :guideHint="getGuideHint(obs.section_key)"
          :modelValue="obs.content"
          :sortOrder="obs.sort_order"
          @update:modelValue="(v: string) => onObsChange(obs.section_key, v)"
        />
        <div class="obs-footer">
          <span class="word-count">总字数：{{ store.currentEntry.word_count }}</span>
          <button class="btn-secondary" :disabled="store.saving" @click="handleSaveObs">
            {{ store.saving ? '保存中...' : '保存笔记' }}
          </button>
        </div>
      </div>

      <!-- Tab: AI 分析 -->
      <div v-if="activeTab === 'ai'" class="tab-content">
        <div class="ai-panel">
          <div v-if="!store.currentEntry.ai_analysis && !aiTriggered" class="ai-empty">
            <p>尚未生成 AI 分析。点击下方按钮使用 LLM 分析当日市场。</p>
            <button class="btn-primary" :disabled="store.saving" @click="handleTriggerAI">
              {{ store.saving ? '提交中...' : '生成 AI 分析' }}
            </button>
          </div>
          <div v-else-if="aiTriggered && !store.currentEntry.ai_analysis" class="ai-pending">
            <p>AI 分析已提交，功能将在后续版本中开放。</p>
          </div>
          <div v-else-if="store.currentEntry.ai_analysis" class="ai-result">
            <div class="ai-section" v-if="store.currentEntry.ai_analysis.market_summary">
              <h4>市场总结</h4>
              <p>{{ store.currentEntry.ai_analysis.market_summary }}</p>
            </div>
            <div class="ai-section" v-if="store.currentEntry.ai_analysis.phase_judgment">
              <h4>阶段判断</h4>
              <p>{{ store.currentEntry.ai_analysis.phase_judgment }}</p>
            </div>
            <div class="ai-section" v-if="store.currentEntry.ai_analysis.style_judgment">
              <h4>风格判断</h4>
              <p>{{ store.currentEntry.ai_analysis.style_judgment }}</p>
            </div>
            <div class="ai-section" v-if="store.currentEntry.ai_analysis.risk_alert">
              <h4>风险提示</h4>
              <p>{{ store.currentEntry.ai_analysis.risk_alert }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- 标签栏 -->
      <div class="tag-bar">
        <TagSelector
          :selectedTags="store.currentEntry.tags"
          :allTags="store.tags"
          @update:selectedTags="onTagsChange"
          @create-tag="onCreateTag"
        />
      </div>

      <!-- 元数据 -->
      <div class="entry-meta">
        <span>创建：{{ formatTime(store.currentEntry.created_at) }}</span>
        <span>更新：{{ formatTime(store.currentEntry.updated_at) }}</span>
        <span>字数：{{ store.currentEntry.word_count }}</span>
      </div>
    </template>

    <!-- 无日志状态 -->
    <div v-else class="empty-state">
      <p>日志未找到</p>
      <RouterLink to="/journal/calendar" class="btn-primary">返回日历</RouterLink>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter, RouterLink } from 'vue-router'
import { useJournalStore } from '../stores/journal'
import SentimentScorePanel from '../components/journal/SentimentScorePanel.vue'
import MarketDataForm from '../components/journal/MarketDataForm.vue'
import IndexSnapshotTable from '../components/journal/IndexSnapshotTable.vue'
import ObservationEditor from '../components/journal/ObservationEditor.vue'
import TagSelector from '../components/journal/TagSelector.vue'
import type { JournalMarketData, TagSummary } from '../types/api'

const route = useRoute()
const router = useRouter()
const store = useJournalStore()

const activeTab = ref('overview')
const aiTriggered = ref(false)

const tabs = [
  { key: 'overview', label: '市场概览' },
  { key: 'snapshot', label: '指数快照' },
  { key: 'observations', label: '观察笔记' },
  { key: 'ai', label: 'AI 分析' },
]

const sentimentScores = computed(() => ({
  market_temperature: store.currentEntry?.market_temperature ?? null,
  profit_effect: store.currentEntry?.profit_effect ?? null,
  risk_preference: store.currentEntry?.risk_preference ?? null,
  trading_difficulty: store.currentEntry?.trading_difficulty ?? null,
  market_consistency: store.currentEntry?.market_consistency ?? null,
}))

const marketData = computed<JournalMarketData>(() => ({
  market_up_stocks: store.currentEntry?.market_data?.market_up_stocks ?? null,
  market_down_stocks: store.currentEntry?.market_data?.market_down_stocks ?? null,
  market_flat_stocks: store.currentEntry?.market_data?.market_flat_stocks ?? null,
  limit_up_stocks: store.currentEntry?.market_data?.limit_up_stocks ?? null,
  limit_down_stocks: store.currentEntry?.market_data?.limit_down_stocks ?? null,
  total_turnover_yi: store.currentEntry?.market_data?.total_turnover_yi ?? null,
  turnover_vs_prev_pct: store.currentEntry?.market_data?.turnover_vs_prev_pct ?? null,
  north_bound_net_yi: store.currentEntry?.market_data?.north_bound_net_yi ?? null,
  margin_balance_change_yi: store.currentEntry?.market_data?.margin_balance_change_yi ?? null,
  size_style: store.currentEntry?.market_data?.size_style ?? null,
  growth_style: store.currentEntry?.market_data?.growth_style ?? null,
  sector_leading: store.currentEntry?.market_data?.sector_leading ?? null,
  top_sectors: store.currentEntry?.market_data?.top_sectors ?? null,
  bottom_sectors: store.currentEntry?.market_data?.bottom_sectors ?? null,
  data_source: store.currentEntry?.market_data?.data_source ?? null,
  notes: store.currentEntry?.market_data?.notes ?? null,
}))

const OBS_GUIDES: Record<string, string> = {
  biggest_phenomenon: '今天市场最突出的一个现象是什么？区别于日常波动的显著特征。',
  strongest_direction: '哪些板块/风格/因子表现最强？背后逻辑是什么？',
  weakest_direction: '哪些板块/风格/因子表现最弱？是趋势性走弱还是轮动？',
  reason_analysis: '你认为今天市场走势的主要原因是什么（政策/资金/情绪/外盘）？',
  biggest_question: '今天让你最困惑、最不确定的一点是什么？',
  if_continues_up: '如果这种走势延续，哪些方向最受益？会触发什么？',
  if_turns_down: '如果市场突然转跌，最脆弱的方向是哪些？',
  reflection: '今天在市场认知或交易心态上有何收获或教训？',
  experience: '今天可以抽象为一条什么样的经验规则（便于未来复用）？',
  watch_next: '明天/接下来最应该关注什么变化或指标？',
}

function getGuideHint(key: string): string {
  return OBS_GUIDES[key] ?? ''
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// ===== Event handlers =====

function onSentimentChange(vals: Record<string, number | null>): void {
  if (!store.currentEntry) return
  store.currentEntry.market_temperature = vals.market_temperature ?? null
  store.currentEntry.profit_effect = vals.profit_effect ?? null
  store.currentEntry.risk_preference = vals.risk_preference ?? null
  store.currentEntry.trading_difficulty = vals.trading_difficulty ?? null
  store.currentEntry.market_consistency = vals.market_consistency ?? null
}

function onMarketDataChange(data: JournalMarketData): void {
  if (!store.currentEntry) return
  store.currentEntry.market_data = data
}

function onPhaseChange(phase: string): void {
  if (!store.currentEntry) return
  store.currentEntry.market_phase = phase || null
}

function onSummaryChange(value: string): void {
  if (!store.currentEntry) return
  store.currentEntry.one_line_summary = value || null
}

function onObsChange(key: string, content: string): void {
  if (!store.currentEntry) return
  const obs = store.currentEntry.observations.find((o) => o.section_key === key)
  if (obs) obs.content = content || null
  // Update local word count
  store.currentEntry.word_count = store.currentEntry.observations.reduce(
    (sum, o) => sum + (o.content?.length || 0), 0
  )
}

async function onTagsChange(tags: TagSummary[]): Promise<void> {
  if (!store.currentEntry) return
  const tagIds = tags.map((t) => t.id)
  const ok = await store.updateEntryTags(store.currentEntry.id, tagIds)
  if (!ok) alert(store.error || '设置标签失败')
}

async function onCreateTag(name: string): Promise<void> {
  const tag = await store.addTag({ name })
  if (tag && store.currentEntry) {
    const newTags = [...store.currentEntry.tags, tag]
    const tagIds = newTags.map((t) => t.id)
    await store.updateEntryTags(store.currentEntry.id, tagIds)
  }
}

async function onRefreshSnapshots(): Promise<void> {
  if (!store.currentEntry) return
  await store.refreshEntrySnapshots(store.currentEntry.id)
}

async function handleSave(): Promise<void> {
  if (!store.currentEntry) return
  const e = store.currentEntry
  const ok = await store.updateEntry(e.id, {
    market_temperature: e.market_temperature,
    profit_effect: e.profit_effect,
    risk_preference: e.risk_preference,
    trading_difficulty: e.trading_difficulty,
    market_consistency: e.market_consistency,
    market_phase: e.market_phase,
    one_line_summary: e.one_line_summary,
    is_complete: e.is_complete,
    market_data: e.market_data ? { ...e.market_data } : null,
  })
  if (!ok) alert(store.error || '保存失败')
}

async function handleSaveObs(): Promise<void> {
  if (!store.currentEntry) return
  const obsList = store.currentEntry.observations.map((o) => ({
    section_key: o.section_key,
    content: o.content,
  }))
  await store.saveEntryObservations(store.currentEntry.id, { observations: obsList })
}

async function handleDelete(): Promise<void> {
  if (!store.currentEntry) return
  if (!confirm(`确定删除 ${store.currentEntry.trade_date} 的日志吗？此操作不可撤销。`)) return
  const ok = await store.removeEntry(store.currentEntry.id)
  if (ok) {
    router.push('/journal/calendar')
  } else {
    alert(store.error || '删除失败')
  }
}

async function handleTriggerAI(): Promise<void> {
  if (!store.currentEntry) return
  const ok = await store.requestAIAnalysis(store.currentEntry.id)
  if (ok) aiTriggered.value = true
  else alert(store.error || '触发AI分析失败')
}

// ===== Init =====
onMounted(async () => {
  await store.loadTags()

  const entryId = route.params.entryId as string | undefined
  const dateParam = route.params.date as string | undefined

  if (entryId) {
    await store.loadEntry(entryId)
  } else if (dateParam) {
    await store.loadEntryByDate(dateParam)
  }
})
</script>

<style scoped>
.page { padding: 24px; max-width: 1100px; margin: 0 auto; }
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
  flex-wrap: wrap;
  gap: 12px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.back-link {
  font-size: 13px;
  color: var(--accent);
  text-decoration: none;
}
.back-link:hover { text-decoration: underline; }
.page-title { margin: 0; font-size: 20px; font-weight: 700; color: var(--text); }
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.phase-select {
  padding: 6px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 13px;
}
.summary-input {
  padding: 6px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 13px;
  width: 240px;
}
.btn-primary {
  padding: 8px 18px;
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
}
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
.btn-secondary {
  padding: 8px 18px;
  background: transparent;
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
}
.btn-secondary:hover { background: var(--surface-2); }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-danger {
  padding: 8px 18px;
  background: transparent;
  color: #ef4444;
  border: 1px solid #ef4444;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
}
.btn-danger:hover { background: rgba(239,68,68,0.1); }
.status-badge {
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 500;
}
.status-pending { background: rgba(234,179,8,.15); color: #eab308; }
.status-success { background: rgba(34,197,94,.15); color: #22c55e; }

.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 2px solid var(--border);
  margin-bottom: 20px;
}
.tab-btn {
  padding: 10px 20px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 14px;
  transition: color 0.15s, border-color 0.15s;
}
.tab-btn:hover { color: var(--text); }
.tab-btn.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}

.tab-content { min-height: 300px; }
.section-gap { height: 16px; }

.obs-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}
.word-count { font-size: 13px; color: var(--text-muted); }

.ai-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 24px;
}
.ai-empty, .ai-pending { text-align: center; padding: 40px 0; color: var(--text-muted); }
.ai-empty p, .ai-pending p { margin-bottom: 16px; }
.ai-section { margin-bottom: 16px; }
.ai-section h4 { font-size: 14px; font-weight: 600; color: var(--text); margin: 0 0 6px; }
.ai-section p { font-size: 13px; color: var(--text); line-height: 1.6; margin: 0; }

.tag-bar {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}

.entry-meta {
  display: flex;
  gap: 20px;
  margin-top: 12px;
  font-size: 11px;
  color: var(--text-muted);
}

.loading { text-align: center; padding: 60px 0; color: var(--text-muted); font-size: 14px; }
.error-tip { padding: 12px 16px; background: rgba(239,68,68,.1); border: 1px solid rgba(239,68,68,.3); border-radius: var(--radius-sm); color: #ef4444; font-size: 13px; margin-bottom: 16px; }
.empty-state { text-align: center; padding: 60px 0; color: var(--text-muted); }
.empty-state p { margin-bottom: 16px; font-size: 15px; }
</style>
