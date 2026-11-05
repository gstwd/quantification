<template>
  <!--
    研究看板（只读）。

    把研究台账、研究战役与待人工确认的候选放在一处审阅：研究线的试验次数 N 与预算、
    假设树与结论、自动研究循环的进度与花费、候选的 10 项验收清单与逐项证据。
    页面不提供任何写操作：登记假设、开启战役、finish / promote 只在 CLI 中执行。
  -->
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">研究看板</h1>
      <p class="subtitle">
        研究线、假设台账、自动研究战役与待确认候选的只读视图。N 按研究线累计（基线、优化候选、稳健性变体共享），
        预算用尽的研究线应收尾换方向；promote 与上线由人工在 CLI 执行。
      </p>
    </div>

    <div class="tabs">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        type="button"
        :class="['tab', { active: activeTab === tab.key }]"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}<span v-if="tab.key === 'candidates' && candidates.length" class="tab-count">{{ candidates.length }}</span>
      </button>
    </div>

    <div v-if="error" class="error-tip">{{ error }}</div>

    <!-- ═══ 研究线 ═══ -->
    <div v-if="activeTab === 'lineages'" class="layout">
      <div class="list">
        <div v-if="lineagesLoading && lineages.length === 0" class="empty">加载中...</div>
        <div v-else-if="lineages.length === 0" class="empty">暂无研究线</div>
        <div
          v-for="line in lineages"
          :key="line.lineage_id"
          :class="['list-card', { active: line.lineage_id === selectedLineage }]"
          @click="selectLineage(line.lineage_id)"
        >
          <div class="list-top">
            <span class="name" :title="line.lineage_id">{{ line.root_display_name || line.lineage_id }}</span>
            <span v-if="line.running_campaigns" class="chip chip-active">战役运行中</span>
          </div>
          <div class="budget-bar" :title="`已用 ${line.budget.used} / 预算 ${line.budget.budget}`">
            <div class="budget-fill" :class="budgetClass(line.budget)" :style="{ width: budgetPercent(line.budget) }" />
          </div>
          <div class="list-meta">
            <span v-if="line.error" class="chip chip-warn" :title="line.error">N 统计失败</span>
            <span v-else title="N 为 Deflated Sharpe 的试验次数；预算按研究线已用试验计">
              N = {{ line.n_trials }} · 预算 {{ line.budget.used }}/{{ line.budget.budget }}
            </span>
            <span>{{ line.members.length }} 个成员{{ line.variant_count ? ` + ${line.variant_count} 变体` : '' }}</span>
          </div>
          <div class="chips">
            <span v-for="(count, status) in line.hypothesis_counts" :key="status" :class="['chip', statusClass(String(status))]">
              {{ statusText(String(status)) }} {{ count }}
            </span>
            <span v-if="line.validation_consumed" class="chip chip-warn">验证期已消费</span>
          </div>
        </div>
      </div>

      <div class="detail">
        <div v-if="!ledger" class="card empty-card">选择左侧研究线查看假设台账</div>
        <template v-else>
          <div v-if="ledger.lineage" class="card">
            <div class="card-head"><span class="card-title">试验次数 N 的来源</span></div>
            <div v-if="ledger.lineage.error" class="error-text">{{ ledger.lineage.error }}</div>
            <div class="kv-grid">
              <div v-for="(value, key) in ledger.lineage.n_trials_breakdown" :key="key">
                <span class="k">{{ BREAKDOWN_TEXT[String(key)] ?? key }}</span>
                <span class="v">{{ value }}</span>
              </div>
              <div class="wide">
                <span class="k">成员策略</span>
                <span class="v mono small">
                  {{ ledger.lineage.members.join('、') }}
                  <template v-if="ledger.lineage.variant_count">；另含 {{ ledger.lineage.variant_count }} 个稳健性变体草稿</template>
                </span>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-head">
              <span class="card-title">假设台账（{{ ledger.hypotheses.length }}）</span>
            </div>
            <div v-if="ledger.hypotheses.length === 0" class="empty-inline">这条研究线还没有登记假设</div>
            <div
              v-for="node in hypothesisTree"
              :key="node.item.hypothesis_id"
              class="hypothesis"
              :style="{ marginLeft: `${node.depth * 20}px` }"
            >
              <div class="hypothesis-head" @click="toggleHypothesis(node.item.hypothesis_id)">
                <span :class="['chip', statusClass(node.item.status)]">{{ statusText(node.item.status) }}</span>
                <span class="hypothesis-statement">{{ node.item.statement }}</span>
                <span class="muted small">{{ node.item.category }} · {{ node.item.trial_rows }} 次检验</span>
              </div>
              <div class="hypothesis-sub">
                <span v-if="node.item.pre_registration?.criteria">判定标准：{{ node.item.pre_registration.criteria }}</span>
              </div>
              <div v-if="node.item.conclusion" class="hypothesis-conclusion">结论：{{ node.item.conclusion }}</div>
              <div v-if="expandedHypothesis === node.item.hypothesis_id && hypothesisDetail" class="hypothesis-detail">
                <div class="muted small">
                  {{ node.item.hypothesis_id }} · {{ node.item.created_by || '—' }} · 登记于 {{ formatCnTime(node.item.created_at) }}
                  <span v-if="node.item.campaign_id"> · 战役 {{ node.item.campaign_id }}</span>
                </div>
                <div v-if="hypothesisDetail.rationale" class="small">依据：{{ hypothesisDetail.rationale }}</div>
                <table v-if="hypothesisDetail.trials.length" class="table">
                  <thead>
                    <tr><th>来源</th><th>关联</th><th>次数</th><th>计入 N</th><th>口径</th><th>备注</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="trial in hypothesisDetail.trials" :key="trial.trial_id">
                      <td>{{ trialKindLabel(trial.kind) }}</td>
                      <td class="mono small">{{ trial.ref_id || '—' }}</td>
                      <td>{{ trial.trial_count }}</td>
                      <td>{{ trial.counted_in_n ? '是' : trial.kind === 'diagnostic' ? '否（诊断记录）' : '否（关联）' }}{{ trial.is_estimated ? ' · 估算' : '' }}</td>
                      <td class="small">{{ trial.execution_model ? executionModelLabel(trial.execution_model) : '—' }}</td>
                      <td class="small">{{ trial.note || '—' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- ═══ 待确认候选 ═══ -->
    <div v-else-if="activeTab === 'candidates'" class="layout">
      <div class="list">
        <div v-if="candidates.length === 0" class="empty">暂无待确认的候选</div>
        <div
          v-for="item in candidates"
          :key="item.optimization_id"
          :class="['list-card', { active: item.optimization_id === selectedCandidate }]"
          @click="selectCandidate(item.optimization_id)"
        >
          <div class="list-top">
            <span class="name" :title="item.hypothesis">{{ item.hypothesis }}</span>
          </div>
          <div class="list-meta">
            <span :class="['chip', item.status === 'accepted' ? 'chip-active' : 'chip-warn']">
              {{ item.status === 'accepted' ? '已接受·待 promote' : '待裁决' }}
            </span>
            <span v-if="item.review_verdict" :class="['chip', verdictClass(item.review_verdict)]">
              评审：{{ VERDICT_TEXT[item.review_verdict] ?? item.review_verdict }}
            </span>
          </div>
          <div class="list-sub mono">{{ item.strategy_id }} → {{ item.candidate_strategy_id }}</div>
        </div>
      </div>

      <div class="detail">
        <div v-if="!candidateDetail" class="card empty-card">选择左侧候选查看验收清单</div>
        <template v-else>
          <div class="card">
            <div class="card-head">
              <span class="card-title">验收清单（{{ passedCount }}/{{ candidateDetail.acceptance_checklist.length }} 通过）</span>
              <span class="muted small">{{ candidateDetail.execution_model }} · {{ candidateDetail.optimization_id }}</span>
            </div>
            <div v-if="candidateDetail.acceptance_checklist.length === 0" class="empty-inline">
              会话尚未汇总指标（回测未全部完成），暂无清单
            </div>
            <div v-for="item in candidateDetail.acceptance_checklist" :key="item.key" class="check-row">
              <span :class="['check-mark', item.pass ? 'pass' : 'fail']">{{ item.pass ? '✓' : '✗' }}</span>
              <div class="check-body">
                <div>{{ item.description }}</div>
                <details v-if="item.evidence" class="evidence">
                  <summary>证据</summary>
                  <pre>{{ JSON.stringify(item.evidence, null, 2) }}</pre>
                </details>
              </div>
            </div>
          </div>

          <div v-if="candidateDetail.metrics_full" class="card">
            <div class="card-head"><span class="card-title">全区间指标（毛口径）</span></div>
            <table class="table">
              <thead><tr><th>指标</th><th>基线</th><th>候选</th></tr></thead>
              <tbody>
                <tr v-for="metric in METRIC_KEYS" :key="metric.key">
                  <td>{{ metric.label }}</td>
                  <td>{{ formatMetric(candidateDetail.metrics_full.baseline?.[metric.key]) }}</td>
                  <td>{{ formatMetric(candidateDetail.metrics_full.candidate?.[metric.key]) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="card">
            <div class="card-head"><span class="card-title">评审与下一步</span></div>
            <p v-if="candidateDetail.review_reason" class="small">评审意见：{{ candidateDetail.review_reason }}</p>
            <p v-else class="small muted">没有研究战役评审记录（人工会话）</p>
            <p class="small muted">
              页面只读。确认后在 CLI 执行：
              <code v-if="candidateDetail.status === 'evaluated'">optimization finish {{ candidateDetail.optimization_id }} --verdict accept|reject</code>
              <code v-else>optimization finish 时未 promote：可 strategy update {{ candidateDetail.strategy_id }} --file &lt;候选配置&gt; 或另建策略（--lineage {{ candidateDetail.lineage_id }}）</code>
            </p>
            <p v-if="candidateDetail.missing_backtest_ids.length" class="small warn">
              有 {{ candidateDetail.missing_backtest_ids.length }} 条引用的回测已被删除，相关指标缺失
            </p>
          </div>
        </template>
      </div>
    </div>

    <!-- ═══ 研究战役 ═══ -->
    <div v-else class="layout">
      <div class="list">
        <div v-if="campaigns.length === 0" class="empty">暂无研究战役（用 CLI research campaign start 开启）</div>
        <div
          v-for="item in campaigns"
          :key="item.campaign_id"
          :class="['list-card', { active: item.campaign_id === selectedCampaign }]"
          @click="selectCampaign(item.campaign_id)"
        >
          <div class="list-top">
            <span class="name" :title="item.objective">{{ item.objective }}</span>
            <span :class="['chip', campaignStatusClass(item.status)]">{{ CAMPAIGN_STATUS_TEXT[item.status] ?? item.status }}</span>
          </div>
          <div class="list-sub mono">{{ item.strategy_id }}</div>
          <div class="list-sub">{{ formatCnTime(item.created_at) }}</div>
        </div>
      </div>

      <div class="detail">
        <div v-if="!campaignDetail" class="card empty-card">选择左侧战役查看进度</div>
        <template v-else>
          <div class="card">
            <div class="card-head">
              <span class="card-title">{{ campaignDetail.campaign_id }}</span>
              <span class="muted small">
                花费 ${{ campaignDetail.total_cost_usd.toFixed(2) }}
                <template v-if="campaignPolling"> · 自动刷新中</template>
              </span>
            </div>
            <p class="small">{{ campaignDetail.objective }}</p>
            <div class="kv-grid">
              <div v-for="limit in campaignLimits" :key="limit.label">
                <span class="k">{{ limit.label }}</span>
                <span class="v">{{ limit.text }}</span>
              </div>
              <div v-if="campaignDetail.stop_reason" class="wide">
                <span class="k">停止原因</span>
                <span class="v">{{ campaignDetail.stop_reason }}</span>
              </div>
              <div v-else-if="campaignDetail.progress.next" class="wide">
                <span class="k">下一步</span>
                <span class="v">第 {{ campaignDetail.progress.next.round }} 轮 {{ ROLE_TEXT[campaignDetail.progress.next.role] ?? campaignDetail.progress.next.role }}</span>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><span class="card-title">步骤（{{ campaignDetail.steps.length }}）</span></div>
            <div v-if="campaignDetail.steps.length === 0" class="empty-inline">尚未开始</div>
            <table v-else class="table">
              <thead><tr><th>轮</th><th>角色</th><th>状态</th><th>结果</th><th>摘要</th><th>花费</th></tr></thead>
              <tbody>
                <tr v-for="step in campaignDetail.steps" :key="step.step_id">
                  <td>{{ step.round }}</td>
                  <td>{{ ROLE_TEXT[step.role] ?? step.role }}</td>
                  <td><span :class="['chip', stepStatusClass(step.status)]">{{ STEP_STATUS_TEXT[step.status] ?? step.status }}</span></td>
                  <td class="small">{{ step.outcome ? (VERDICT_TEXT[step.outcome] ?? step.outcome) : '—' }}</td>
                  <td class="small summary-cell">{{ step.summary || step.error || '—' }}</td>
                  <td class="small">{{ step.cost_usd != null ? `$${step.cost_usd.toFixed(2)}` : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div v-if="campaignDetail.hypotheses.length" class="card">
            <div class="card-head"><span class="card-title">战役内假设</span></div>
            <div v-for="h in campaignDetail.hypotheses" :key="h.hypothesis_id" class="hypothesis">
              <div class="hypothesis-head">
                <span :class="['chip', statusClass(h.status)]">{{ statusText(h.status) }}</span>
                <span class="hypothesis-statement">{{ h.statement }}</span>
              </div>
              <div v-if="h.conclusion" class="hypothesis-conclusion">结论：{{ h.conclusion }}</div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 研究看板页面：研究线 / 待确认候选 / 研究战役三个页签，全部只读。
 * 运行中的战役详情用 usePolling 每 30 秒刷新一次，战役结束后自动停止。
 */

import { computed, onMounted, ref } from 'vue'

import {
  fetchResearchCampaign,
  fetchResearchCampaigns,
  fetchResearchCandidate,
  fetchResearchCandidates,
  fetchResearchHypotheses,
  fetchResearchHypothesis,
  fetchResearchLineages,
} from '../api/research'
import { getErrorMessage } from '../api/client'
import { executionModelLabel, trialKindLabel } from '../utils/labels'
import { usePolling } from '../composables/usePolling'
import type {
  ResearchCampaign,
  ResearchCampaignDetail,
  ResearchCandidate,
  ResearchCandidateDetail,
  ResearchHypothesis,
  ResearchHypothesisDetail,
  ResearchLedgerResponse,
  ResearchLineage,
  ResearchTrialBudget,
} from '../types/api'
import { formatCnTime } from '../utils/date'

type TabKey = 'lineages' | 'candidates' | 'campaigns'

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'lineages', label: '研究线与台账' },
  { key: 'candidates', label: '待确认候选' },
  { key: 'campaigns', label: '研究战役' },
]

const STATUS_TEXT: Record<string, string> = {
  open: '待检验',
  testing: '检验中',
  accepted: '接受',
  rejected: '否决',
  inconclusive: '无定论',
  blocked_by_schema: '受限于配置',
}
const BREAKDOWN_TEXT: Record<string, string> = {
  robustness: '稳健性变体',
  optimization: '已评估优化会话',
  research_batch: '研究批量评估变体',
  manual: '人工 / 补录',
  estimated: '其中估算',
}
const VERDICT_TEXT: Record<string, string> = {
  recommend_accept: '推荐接受',
  reject: '否决',
  needs_more: '需补证据',
  review_requested: '请求评审',
  rejected: '否决',
  blocked: '受限于配置',
  no_idea: '无新方向',
}
const ROLE_TEXT: Record<string, string> = { researcher: '研究员', reviewer: '评审员' }
const CAMPAIGN_STATUS_TEXT: Record<string, string> = { running: '运行中', stopped: '已停止', completed: '已完成' }
const STEP_STATUS_TEXT: Record<string, string> = { running: '运行中', done: '完成', failed: '失败' }
const METRIC_KEYS: Array<{ key: string; label: string }> = [
  { key: 'sharpe_ratio', label: '夏普' },
  { key: 'annualized_return_pct', label: '年化收益 %' },
  { key: 'max_drawdown_pct', label: '最大回撤 %' },
  { key: 'cumulative_return_pct', label: '累计收益 %' },
]

const activeTab = ref<TabKey>('lineages')
const error = ref('')

const lineages = ref<ResearchLineage[]>([])
const selectedLineage = ref('')
const ledger = ref<ResearchLedgerResponse | null>(null)
const expandedHypothesis = ref('')
const hypothesisDetail = ref<ResearchHypothesisDetail | null>(null)

const candidates = ref<ResearchCandidate[]>([])
const selectedCandidate = ref('')
const candidateDetail = ref<ResearchCandidateDetail | null>(null)

const campaigns = ref<ResearchCampaign[]>([])
const selectedCampaign = ref('')
const campaignDetail = ref<ResearchCampaignDetail | null>(null)

/** 把接口错误转为页面提示 */
function showError(err: unknown, fallback: string): void {
  error.value = `${fallback}：${getErrorMessage(err, '未知错误')}`
}

function statusText(status: string): string {
  return STATUS_TEXT[status] ?? status
}

function statusClass(status: string): string {
  if (status === 'accepted') return 'chip-active'
  if (status === 'rejected') return 'chip-danger'
  if (status === 'testing' || status === 'open') return 'chip-warn'
  return 'chip-disabled'
}

function verdictClass(verdict: string): string {
  if (verdict === 'recommend_accept') return 'chip-active'
  if (verdict === 'reject') return 'chip-danger'
  return 'chip-warn'
}

function campaignStatusClass(status: string): string {
  if (status === 'running') return 'chip-active'
  if (status === 'completed') return 'chip-disabled'
  return 'chip-warn'
}

function stepStatusClass(status: string): string {
  if (status === 'done') return 'chip-active'
  if (status === 'failed') return 'chip-danger'
  return 'chip-warn'
}

function budgetPercent(budget: ResearchTrialBudget): string {
  if (budget.budget <= 0) return '0%'
  return `${Math.min(100, (budget.used / budget.budget) * 100).toFixed(0)}%`
}

function budgetClass(budget: ResearchTrialBudget): string {
  const ratio = budget.budget > 0 ? budget.used / budget.budget : 0
  if (ratio >= 1) return 'over'
  if (ratio >= 0.8) return 'high'
  return ''
}

function formatMetric(value: number | null | undefined): string {
  return value == null ? '—' : value.toFixed(3)
}

/** 假设树：根假设在前，子假设按登记顺序缩进排列在父假设之后 */
const hypothesisTree = computed((): Array<{ item: ResearchHypothesis; depth: number }> => {
  const items = ledger.value?.hypotheses ?? []
  const ids = new Set(items.map(item => item.hypothesis_id))
  const children = new Map<string, ResearchHypothesis[]>()
  const roots: ResearchHypothesis[] = []
  for (const item of [...items].reverse()) {
    if (item.parent_id && ids.has(item.parent_id)) {
      const list = children.get(item.parent_id) ?? []
      list.push(item)
      children.set(item.parent_id, list)
    } else {
      roots.push(item)
    }
  }
  const result: Array<{ item: ResearchHypothesis; depth: number }> = []
  const walk = (item: ResearchHypothesis, depth: number): void => {
    result.push({ item, depth })
    for (const child of children.get(item.hypothesis_id) ?? []) walk(child, depth + 1)
  }
  roots.reverse().forEach(root => walk(root, 0))
  return result
})

const passedCount = computed(
  () => candidateDetail.value?.acceptance_checklist.filter(item => item.pass).length ?? 0,
)

const campaignLimits = computed((): Array<{ label: string; text: string }> => {
  const progress = campaignDetail.value?.progress
  if (!progress) return []
  return [
    { label: '新增试验', text: `${progress.trials_used ?? 0} / ${progress.max_trials ?? '—'}` },
    { label: '研究轮数', text: `${progress.rounds_done ?? 0} / ${progress.max_rounds ?? '—'}` },
    { label: '连续否决', text: `${progress.consecutive_rejects ?? 0} / ${progress.max_consecutive_rejects ?? '—'}` },
    { label: '诊断命令', text: `${progress.diagnostics_used ?? 0} / ${progress.max_diagnostics ?? '—'}` },
    { label: '已用时长（小时）', text: `${(progress.elapsed_hours ?? 0).toFixed(2)} / ${progress.max_hours ?? '—'}` },
    {
      label: '研究线预算',
      text: progress.lineage_budget ? `${progress.lineage_budget.used} / ${progress.lineage_budget.budget}` : '—',
    },
  ]
})

/** 研究线列表加载中（区分「加载中」与「暂无研究线」） */
const lineagesLoading = ref(false)

async function loadLineages(): Promise<void> {
  lineagesLoading.value = true
  try {
    lineages.value = await fetchResearchLineages()
    if (!selectedLineage.value && lineages.value.length) await selectLineage(lineages.value[0].lineage_id)
  } catch (err) {
    showError(err, '加载研究线失败')
  } finally {
    lineagesLoading.value = false
  }
}

async function selectLineage(lineageId: string): Promise<void> {
  selectedLineage.value = lineageId
  expandedHypothesis.value = ''
  hypothesisDetail.value = null
  try {
    ledger.value = await fetchResearchHypotheses({ strategyId: lineageId })
  } catch (err) {
    showError(err, '加载假设台账失败')
  }
}

async function toggleHypothesis(hypothesisId: string): Promise<void> {
  if (expandedHypothesis.value === hypothesisId) {
    expandedHypothesis.value = ''
    hypothesisDetail.value = null
    return
  }
  expandedHypothesis.value = hypothesisId
  try {
    hypothesisDetail.value = await fetchResearchHypothesis(hypothesisId)
  } catch (err) {
    showError(err, '加载假设详情失败')
  }
}

async function loadCandidates(): Promise<void> {
  try {
    candidates.value = await fetchResearchCandidates()
  } catch (err) {
    showError(err, '加载候选失败')
  }
}

async function selectCandidate(optimizationId: string): Promise<void> {
  selectedCandidate.value = optimizationId
  candidateDetail.value = null
  try {
    candidateDetail.value = await fetchResearchCandidate(optimizationId)
  } catch (err) {
    showError(err, '加载候选详情失败')
  }
}

const campaignPollingTarget = ref('')
const { start: startCampaignPolling, stop: stopCampaignPolling, polling: campaignPolling } = usePolling<ResearchCampaignDetail>({
  fetcher: () => fetchResearchCampaign(campaignPollingTarget.value),
  isDone: data => data.status !== 'running',
  intervalMs: 30000,
  onData: data => {
    if (data.campaign_id === selectedCampaign.value) campaignDetail.value = data
  },
})

async function loadCampaigns(): Promise<void> {
  try {
    campaigns.value = await fetchResearchCampaigns()
  } catch (err) {
    showError(err, '加载研究战役失败')
  }
}

async function selectCampaign(campaignId: string): Promise<void> {
  selectedCampaign.value = campaignId
  stopCampaignPolling()
  try {
    campaignDetail.value = await fetchResearchCampaign(campaignId)
    if (campaignDetail.value.status === 'running') {
      campaignPollingTarget.value = campaignId
      void startCampaignPolling()
    }
  } catch (err) {
    showError(err, '加载战役详情失败')
  }
}

function switchTab(tab: TabKey): void {
  activeTab.value = tab
  error.value = ''
  if (tab !== 'campaigns') stopCampaignPolling()
}

onMounted(async () => {
  await Promise.all([loadLineages(), loadCandidates(), loadCampaigns()])
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-title { font-size: 22px; font-weight: 700; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; max-width: 820px; }
.error-tip { padding: 10px 14px; border-radius: var(--radius-sm); background: rgba(239, 68, 68, 0.12); color: var(--danger); }
.empty { padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px; }
.empty-inline { color: var(--text-muted); font-size: 13px; }

.tabs { display: flex; gap: 8px; border-bottom: 1px solid var(--border); }
.tab {
  padding: 8px 14px; border: none; background: transparent; color: var(--text-muted);
  font-size: 13px; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -1px;
}
.tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.tab-count {
  margin-left: 6px; padding: 0 6px; border-radius: 999px; font-size: 11px;
  background: var(--accent); color: #fff;
}

.layout { display: grid; grid-template-columns: 320px 1fr; gap: 16px; align-items: start; }
.list { display: flex; flex-direction: column; gap: 10px; }
.list-card {
  padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface); cursor: pointer; display: flex; flex-direction: column; gap: 6px;
}
.list-card.active { border-color: var(--accent); }
.list-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.list-top > .chip { flex: 0 0 auto; white-space: nowrap; }
.name { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.list-meta { display: flex; align-items: center; justify-content: space-between; gap: 6px; font-size: 12px; color: var(--text-muted); }
.list-sub { font-size: 12px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }

.budget-bar { height: 6px; border-radius: 3px; background: var(--border); overflow: hidden; }
.budget-fill { height: 100%; background: var(--accent); }
.budget-fill.high { background: var(--warning); }
.budget-fill.over { background: var(--danger); }

.detail { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.card { padding: 16px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.empty-card { color: var(--text-muted); }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.card-title { font-weight: 600; }
.kv-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; }
.kv-grid .wide { grid-column: 1 / -1; }
.k { display: block; font-size: 12px; color: var(--text-muted); }
.v { font-size: 14px; }

.hypothesis { padding: 10px 0; border-top: 1px solid var(--border); display: flex; flex-direction: column; gap: 4px; }
.hypothesis:first-of-type { border-top: none; }
.hypothesis-head { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.hypothesis-statement { font-weight: 500; flex: 1; min-width: 0; }
.hypothesis-sub { font-size: 12px; color: var(--text-muted); }
.hypothesis-conclusion { font-size: 12px; }
.hypothesis-detail { margin-top: 6px; padding: 10px; border-radius: var(--radius-sm); background: var(--bg); display: flex; flex-direction: column; gap: 6px; }

.check-row { display: flex; gap: 10px; padding: 8px 0; border-top: 1px solid var(--border); }
.check-row:first-of-type { border-top: none; }
.check-mark { width: 20px; text-align: center; font-weight: 700; }
.check-mark.pass { color: var(--success); }
.check-mark.fail { color: var(--danger); }
.check-body { flex: 1; min-width: 0; font-size: 13px; }
.evidence summary { font-size: 12px; color: var(--text-muted); cursor: pointer; }
.evidence pre { font-size: 11px; white-space: pre-wrap; word-break: break-all; margin: 6px 0 0; }

.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th, .table td { padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
.table th { font-size: 12px; color: var(--text-muted); font-weight: 500; }
.summary-cell { max-width: 420px; white-space: pre-wrap; }

.chip { padding: 2px 8px; border-radius: 999px; font-size: 11px; border: 1px solid var(--border); white-space: nowrap; }
.chip-active { color: var(--success); border-color: var(--success); }
.chip-warn { color: var(--warning); border-color: var(--warning); }
.chip-danger { color: var(--danger); border-color: var(--danger); }
.chip-disabled { color: var(--text-muted); }

.muted { color: var(--text-muted); }
.small { font-size: 12px; }
.mono { font-family: var(--font-mono); }
.warn { color: var(--warning); }
.error-text { color: var(--danger); font-size: 12px; margin-bottom: 8px; }
code { font-size: 12px; padding: 1px 4px; border-radius: 4px; background: var(--bg); }
</style>
