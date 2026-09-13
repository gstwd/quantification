<template>
  <!--
    策略生命周期页面。

    只展示"人工标记上线之后"的监控与诊断：上线策略列表、单策略的冻结快照、
    最新健康体检结论与历史快照。健康数据由页面按钮手动触发同步计算，
    系统不会自动变更状态、也不会自动调整策略参数。
  -->
  <div class="page">
    <div class="page-header">
      <div class="header-row">
        <div>
          <h1 class="page-title">策略生命周期</h1>
          <p class="subtitle">
            上线后监控与诊断。诊断阈值全部取自该策略自身的研究期分布；
            系统只计算健康等级，不自动改状态、不自动调参。
          </p>
        </div>
        <button class="btn-primary" @click="showOnline = true">标记上线</button>
      </div>
    </div>

    <div v-if="error" class="error-tip">{{ error }}</div>
    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="items.length === 0" class="empty">
      暂无上线策略。点击"标记上线"后，系统会冻结当前配置与研究期分布，开始监控。
    </div>

    <div v-else class="layout">
      <div class="list">
        <div
          v-for="item in items"
          :key="item.strategy_id"
          class="list-card"
          :class="{ active: item.strategy_id === selectedId }"
          @click="select(item.strategy_id)"
        >
          <div class="list-top">
            <span class="name">{{ item.display_name || item.strategy_id }}</span>
            <span :class="['chip', statusClass(item.lifecycle_status)]">
              {{ statusText(item.lifecycle_status) }}
            </span>
          </div>
          <div class="list-meta">
            <span>上线 {{ item.live_at }}（{{ item.live_days }} 天）</span>
            <span :class="['chip', healthClass(item.health_level)]">
              {{ healthText(item.health_level) }}
            </span>
          </div>
          <div class="list-sub">
            {{ item.last_refreshed_at ? `最近体检 ${formatCnTime(item.last_refreshed_at)}` : '尚未体检' }}
          </div>
        </div>
      </div>

      <div v-if="detail" class="detail">
        <div class="card">
          <div class="card-head">
            <span class="card-title">{{ detail.display_name || detail.strategy_id }}</span>
            <div class="actions">
              <button class="btn-secondary" :disabled="busy" @click="handleRefresh">
                {{ busy ? '计算中...' : '刷新体检' }}
              </button>
              <button
                v-if="detail.lifecycle_status === 'LIVE'"
                class="btn-secondary"
                :disabled="busy"
                @click="handleStatus('SUSPENDED')"
              >
                暂停观察
              </button>
              <button
                v-else-if="detail.lifecycle_status === 'SUSPENDED'"
                class="btn-secondary"
                :disabled="busy"
                @click="handleStatus('LIVE')"
              >
                恢复运行
              </button>
              <button
                v-if="detail.lifecycle_status !== 'RETIRED'"
                class="btn-danger"
                :disabled="busy"
                @click="handleStatus('RETIRED')"
              >
                退役
              </button>
            </div>
          </div>
          <div class="kv-grid">
            <div><span class="k">状态</span><span class="v">{{ statusText(detail.lifecycle_status) }}</span></div>
            <div><span class="k">上线日期</span><span class="v">{{ detail.live_at }}</span></div>
            <div><span class="k">监控天数</span><span class="v">{{ detail.live_days }}</span></div>
            <div><span class="k">冻结配置哈希</span><span class="v mono">{{ shortHash(detail.frozen_config_hash) }}</span></div>
            <div v-if="detail.note" class="wide">
              <span class="k">上线备注</span><span class="v">{{ detail.note }}</span>
            </div>
          </div>
          <p class="hint">
            上线后策略配置已冻结：如需变更请先暂停，或走优化会话生成候选策略后 promote 新版本。
          </p>
        </div>

        <div v-if="!latest" class="card empty-card">
          尚未体检。点击"刷新体检"会同步执行一次上线后回测，并与冻结的研究期分布比对。
        </div>

        <template v-else>
          <div class="card">
            <div class="card-head">
              <span class="card-title">最新体检（{{ latest.as_of_date }}）</span>
              <span :class="['chip', healthClass(latest.health_level)]">
                {{ healthText(latest.health_level) }}
              </span>
            </div>
            <div class="diagnosis">
              <div><span class="k">诊断</span><span class="v">{{ diagnosisText(latest.diagnosis) }}</span></div>
              <div><span class="k">建议动作</span><span class="v">{{ actionText(latest.recommended_action) }}</span></div>
            </div>
            <ul class="reasons">
              <li v-for="(reason, i) in latest.reasons" :key="i">{{ reason }}</li>
            </ul>
          </div>

          <div class="card">
            <div class="card-head"><span class="card-title">净成本口径表现</span></div>
            <div class="kv-grid">
              <div><span class="k">成本假设</span><span class="v">{{ metrics.cost_bps ?? '—' }} bp</span></div>
              <div><span class="k">年化收益</span><span class="v">{{ pct(metrics.net?.annualized_return_pct) }}</span></div>
              <div><span class="k">年化夏普</span><span class="v">{{ num(metrics.net?.sharpe_ratio) }}</span></div>
              <div><span class="k">年化超额</span><span class="v">{{ pct(metrics.net?.excess_return_pct) }}</span></div>
              <div><span class="k">成本拖累</span><span class="v">{{ pct(metrics.net?.cost_drag_pct_per_year) }}/年</span></div>
              <div><span class="k">年化换手</span><span class="v">{{ num(metrics.gross?.annualized_turnover) }}x</span></div>
              <div><span class="k">当前回撤</span><span class="v">{{ pct(metrics.evaluation?.drawdown?.current_pct) }}</span></div>
              <div>
                <span class="k">回撤分位</span>
                <span class="v">{{ pct(metrics.evaluation?.drawdown?.percentile_pct) }}</span>
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-head"><span class="card-title">相对研究期分布的分位与期望差</span></div>
            <table class="table">
              <thead>
                <tr>
                  <th>窗口</th>
                  <th>超额收益</th>
                  <th>超额分位</th>
                  <th>夏普</th>
                  <th>夏普分位</th>
                  <th>期望差</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(entry, label) in windowRows" :key="label">
                  <td>{{ label }}</td>
                  <td>{{ entry.available ? pct(entry.excess_return_pct) : '样本不足' }}</td>
                  <td>{{ entry.available ? pct(entry.excess_return_percentile_pct) : '—' }}</td>
                  <td>{{ entry.available ? num(entry.sharpe_ratio) : '—' }}</td>
                  <td>{{ entry.available ? pct(entry.sharpe_percentile_pct) : '—' }}</td>
                  <td>{{ entry.available ? pct(entry.expectation_gap_pct) : '—' }}</td>
                </tr>
              </tbody>
            </table>
            <p class="hint">
              分位为"相对该策略研究期同窗口分布"的位置；样本不足的窗口不显示结论——
              上线初期的收益样本本就无法确认有效性，只能用于否决。
            </p>
          </div>

          <div class="card">
            <div class="card-head"><span class="card-title">因子 IC（Rank IC 均值）</span></div>
            <div v-if="icDecay" class="ic-decay">
              <span class="k">IC 衰减（前半段 → 后半段）</span>
              <span class="v" :class="{ warn: icDecaying }">
                {{ num(icDecay.first_half_mean) }} → {{ num(icDecay.second_half_mean) }}
                <template v-if="icDecaying">（衰减中）</template>
              </span>
            </div>
            <table v-if="factorRows.length" class="table">
              <thead>
                <tr><th>因子</th><th>观测数</th><th>IC 均值</th><th>ICIR</th><th>IC&gt;0 占比</th></tr>
              </thead>
              <tbody>
                <tr v-for="row in factorRows" :key="row.factorId">
                  <td class="mono">{{ row.factorId }}</td>
                  <td>{{ row.count }}</td>
                  <td>{{ num(row.ic_mean) }}</td>
                  <td>{{ num(row.ic_ir) }}</td>
                  <td>{{ row.ic_positive_ratio != null ? pct(row.ic_positive_ratio * 100) : '—' }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="hint">该策略未引用资产级因子，或监控区间内因子值不足。</p>
          </div>
        </template>

        <div v-if="detail.snapshots.length" class="card">
          <div class="card-head"><span class="card-title">体检历史</span></div>
          <table class="table">
            <thead>
              <tr><th>日期</th><th>健康等级</th><th>诊断</th><th>建议动作</th><th>计算时间</th></tr>
            </thead>
            <tbody>
              <tr v-for="snap in detail.snapshots" :key="snap.id">
                <td>{{ snap.as_of_date }}</td>
                <td><span :class="['chip', healthClass(snap.health_level)]">{{ healthText(snap.health_level) }}</span></td>
                <td>{{ diagnosisText(snap.diagnosis) }}</td>
                <td>{{ actionText(snap.recommended_action) }}</td>
                <td>{{ formatCnTime(snap.computed_at) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 标记上线弹窗 -->
    <div v-if="showOnline" class="modal-overlay" @click.self="showOnline = false">
      <div class="modal">
        <h2 class="modal-title">标记策略上线</h2>
        <p class="hint">
          上线时会冻结当前配置快照，并生成研究期（2016-01-01 ~ 2025-12-31）分布作为监控参照系。
          若无可复用的研究期回测，会同步执行一次十年回测，耗时较长。
        </p>
        <div class="form-group">
          <label class="form-label">策略</label>
          <select v-model="onlineForm.strategy_id" class="form-select">
            <option value="" disabled>请选择策略</option>
            <option v-for="s in onlineCandidates" :key="s.strategy_id" :value="s.strategy_id">
              {{ s.display_name }}（{{ s.strategy_id }}）
            </option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">上线日期</label>
          <input v-model="onlineForm.live_at" class="form-input" type="date" />
        </div>
        <div class="form-group">
          <label class="form-label">备注（研究结论、上线依据）</label>
          <textarea v-model="onlineForm.note" class="form-textarea" rows="2"></textarea>
        </div>
        <div v-if="error" class="form-error">{{ error }}</div>
        <div class="modal-actions">
          <button class="btn-secondary" @click="showOnline = false">取消</button>
          <button class="btn-primary" :disabled="busy || !onlineForm.strategy_id" @click="handleOnline">
            {{ busy ? '处理中...' : '确认上线' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 策略生命周期页面。
 *
 * 数据通过 `@/api/lifecycle` 直接获取（只读展示 + 人工触发的写操作），
 * 不引入 Pinia store——该页面没有跨页面共享的可变状态。
 */
import { computed, onMounted, ref } from 'vue'

import {
  fetchLifecycleDetail,
  fetchLifecycles,
  onlineStrategy,
  refreshLifecycle,
  updateLifecycleStatus,
} from '../api/lifecycle'
import { useStrategyStore } from '../stores/strategies'
import type { LifecycleDetail, LifecycleSummary } from '../types/api'
import { formatCnTime, todayCn } from '../utils/date'

// 上线策略摘要列表
const items = ref<LifecycleSummary[]>([])
// 当前选中的策略详情
const detail = ref<LifecycleDetail | null>(null)
const selectedId = ref<string | null>(null)
const loading = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
const showOnline = ref(false)

const strategyStore = useStrategyStore()

// 标记上线表单
const onlineForm = ref({ strategy_id: '', live_at: todayCn(), note: '' })

// 尚未上线的策略（可被标记上线）
const onlineCandidates = computed(() => {
  const online = new Set(items.value.map(item => item.strategy_id))
  return strategyStore.items.filter(item => !online.has(item.strategy_id))
})

// 最新一次体检快照
const latest = computed(() => detail.value?.snapshots?.[0] ?? null)

/** 单个监控窗口的体检结果（后端 JSONB 结构） */
interface WindowEntry {
  available?: boolean
  excess_return_pct?: number
  excess_return_percentile_pct?: number
  sharpe_ratio?: number
  sharpe_percentile_pct?: number
  expectation_gap_pct?: number | null
}

/** 单因子 IC 明细（后端 JSONB 结构） */
interface FactorIcEntry {
  count?: number
  ic_mean?: number | null
  ic_ir?: number | null
  ic_positive_ratio?: number | null
}

/** 体检指标结构（后端 JSONB，字段全部可选） */
interface SnapshotMetrics {
  cost_bps?: number
  gross?: { annualized_turnover?: number; annualized_return_pct?: number }
  net?: {
    annualized_return_pct?: number
    sharpe_ratio?: number
    excess_return_pct?: number | null
    cost_drag_pct_per_year?: number
  }
  evaluation?: {
    drawdown?: { current_pct?: number; max_pct?: number; percentile_pct?: number | null }
    windows?: Record<string, WindowEntry>
  }
  factors?: {
    per_factor?: Record<string, FactorIcEntry>
    ic_mean?: number | null
    ic_decay?: { first_half_mean?: number | null; second_half_mean?: number | null } | null
  }
}

// 最新快照的指标字典（后端为 JSONB，按结构窄化后使用）
const metrics = computed<SnapshotMetrics>(
  () => (latest.value?.metrics ?? {}) as unknown as SnapshotMetrics,
)

// 监控窗口行（1M/3M/6M/12M/24M）
const windowRows = computed<Record<string, WindowEntry>>(
  () => metrics.value.evaluation?.windows ?? {},
)

// 因子 IC 明细行
const factorRows = computed(() => {
  const perFactor = metrics.value.factors?.per_factor ?? {}
  return Object.entries(perFactor).map(([factorId, value]) => ({
    factorId,
    count: value.count ?? 0,
    ic_mean: value.ic_mean ?? null,
    ic_ir: value.ic_ir ?? null,
    ic_positive_ratio: value.ic_positive_ratio ?? null,
  }))
})

// IC 前后半段均值：上线初期唯一能积累出统计量的衰减证据
const icDecay = computed(() => metrics.value.factors?.ic_decay ?? null)

// 后半段 IC 低于前半段时标记为衰减中
const icDecaying = computed(() => {
  const first = icDecay.value?.first_half_mean
  const second = icDecay.value?.second_half_mean
  return typeof first === 'number' && typeof second === 'number' && second < first
})

/**
 * 加载上线策略列表，并默认选中第一个。
 */
async function loadList(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    items.value = await fetchLifecycles()
    if (selectedId.value && !items.value.some(i => i.strategy_id === selectedId.value)) {
      selectedId.value = null
      detail.value = null
    }
    if (!selectedId.value && items.value.length) {
      await select(items.value[0].strategy_id)
    }
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载生命周期列表失败'
  } finally {
    loading.value = false
  }
}

/**
 * 选中某个策略并加载其生命周期详情。
 *
 * @param strategyId - 策略 ID
 */
async function select(strategyId: string): Promise<void> {
  selectedId.value = strategyId
  error.value = null
  try {
    detail.value = await fetchLifecycleDetail(strategyId)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载生命周期详情失败'
  }
}

/**
 * 人工标记上线。
 */
async function handleOnline(): Promise<void> {
  busy.value = true
  error.value = null
  try {
    const result = await onlineStrategy(onlineForm.value.strategy_id, {
      live_at: onlineForm.value.live_at,
      note: onlineForm.value.note || null,
    })
    showOnline.value = false
    onlineForm.value = { strategy_id: '', live_at: todayCn(), note: '' }
    await loadList()
    await select(result.strategy_id)
  } catch (e) {
    error.value = e instanceof Error ? e.message : '标记上线失败'
  } finally {
    busy.value = false
  }
}

/**
 * 人工触发一次健康体检（同步计算，耗时取决于监控区间长度）。
 */
async function handleRefresh(): Promise<void> {
  if (!selectedId.value) return
  busy.value = true
  error.value = null
  try {
    await refreshLifecycle(selectedId.value)
    await select(selectedId.value)
    await loadList()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '刷新体检失败'
  } finally {
    busy.value = false
  }
}

/**
 * 人工变更生命周期状态。
 *
 * @param status - 目标状态
 */
async function handleStatus(status: 'LIVE' | 'SUSPENDED' | 'RETIRED'): Promise<void> {
  if (!selectedId.value) return
  busy.value = true
  error.value = null
  try {
    await updateLifecycleStatus(selectedId.value, { status })
    await select(selectedId.value)
    await loadList()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '状态变更失败'
  } finally {
    busy.value = false
  }
}

/** 生命周期状态中文文案 */
function statusText(status: string): string {
  return { LIVE: '运行中', SUSPENDED: '暂停观察', RETIRED: '已退役' }[status] ?? status
}

/** 生命周期状态样式类 */
function statusClass(status: string): string {
  return { LIVE: 'chip-active', SUSPENDED: 'chip-warn', RETIRED: 'chip-disabled' }[status] ?? ''
}

/** 健康等级中文文案 */
function healthText(level: string | null): string {
  if (!level) return '未体检'
  return { HEALTHY: '健康', WATCH: '观察', WARNING: '警告', CRITICAL: '严重' }[level] ?? level
}

/** 健康等级样式类 */
function healthClass(level: string | null): string {
  if (!level) return 'chip-disabled'
  return {
    HEALTHY: 'chip-active',
    WATCH: 'chip-warn',
    WARNING: 'chip-warn',
    CRITICAL: 'chip-danger',
  }[level] ?? 'chip-disabled'
}

/** 诊断结论中文文案 */
function diagnosisText(diagnosis: string): string {
  return {
    NORMAL: '正常范围',
    INSUFFICIENT_DATA: '样本不足',
    DRAWDOWN_EXTREME: '回撤超出历史范围',
    ALPHA_DECAY: '超额收益衰减',
  }[diagnosis] ?? diagnosis
}

/** 建议动作中文文案 */
function actionText(action: string): string {
  return {
    KEEP: '维持运行',
    WATCH: '持续观察',
    REDUCE_RISK: '降低风险暴露',
    RESEARCH: '重新研究',
  }[action] ?? action
}

/** 数字格式化（保留 2 位小数，缺失显示占位符） */
function num(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

/** 百分比格式化（后端已按 % 或 0-100 提供，缺失显示占位符） */
function pct(value: unknown): string {
  return typeof value === 'number' ? `${value.toFixed(2)}%` : '—'
}

/** 截断哈希展示 */
function shortHash(hash: string): string {
  return hash ? `${hash.slice(0, 12)}…` : '—'
}

onMounted(async () => {
  if (!strategyStore.items.length) {
    await strategyStore.loadAll()
  }
  await loadList()
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.header-row { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.page-title { font-size: 22px; font-weight: 700; }
.subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; max-width: 720px; }
.loading, .empty { padding: 40px; text-align: center; color: var(--text-muted); }
.error-tip { padding: 10px 14px; border-radius: var(--radius-sm); background: rgba(239, 68, 68, 0.12); color: var(--danger); }

.layout { display: grid; grid-template-columns: 300px 1fr; gap: 16px; align-items: start; }
.list { display: flex; flex-direction: column; gap: 10px; }
.list-card {
  padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--surface); cursor: pointer; display: flex; flex-direction: column; gap: 6px;
}
.list-card.active { border-color: var(--accent); }
.list-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.name { font-weight: 600; }
.list-meta { display: flex; align-items: center; justify-content: space-between; font-size: 12px; color: var(--text-muted); }
.list-sub { font-size: 12px; color: var(--text-muted); }

.detail { display: flex; flex-direction: column; gap: 16px; }
.card { padding: 16px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
.empty-card { color: var(--text-muted); }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
.card-title { font-weight: 600; }
.actions { display: flex; gap: 8px; }
.kv-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.kv-grid .wide { grid-column: 1 / -1; }
.k { display: block; font-size: 12px; color: var(--text-muted); }
.v { font-size: 14px; }
.diagnosis { display: flex; gap: 32px; margin-bottom: 8px; }
.reasons { margin: 0; padding-left: 18px; color: var(--text-muted); font-size: 13px; }
.hint { margin-top: 10px; font-size: 12px; color: var(--text-muted); }
.ic-decay { display: flex; gap: 10px; align-items: baseline; margin-bottom: 10px; font-size: 13px; }
.ic-decay .warn { color: var(--warning); }

.table { width: 100%; border-collapse: collapse; font-size: 13px; }
.table th, .table td { padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--border); }
.table th { color: var(--text-muted); font-weight: 500; }
.mono { font-family: ui-monospace, SFMono-Regular, monospace; font-size: 12px; }

.chip { padding: 2px 8px; border-radius: 999px; font-size: 11px; border: 1px solid var(--border); }
.chip-active { color: var(--success); border-color: var(--success); }
.chip-warn { color: var(--warning); border-color: var(--warning); }
.chip-danger { color: var(--danger); border-color: var(--danger); }
.chip-disabled { color: var(--text-muted); }

.btn-primary, .btn-secondary, .btn-danger {
  padding: 6px 14px; border-radius: var(--radius-sm); border: 1px solid var(--border);
  background: transparent; color: var(--text);
}
.btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn-danger { color: var(--danger); border-color: var(--danger); }
.btn-secondary:disabled, .btn-primary:disabled, .btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }

.modal-overlay {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6);
  display: flex; align-items: center; justify-content: center; z-index: 50;
}
.modal {
  width: 520px; max-width: 92vw; padding: 20px; border-radius: var(--radius);
  background: var(--surface); border: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 12px;
}
.modal-title { font-size: 16px; font-weight: 700; }
.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-label { font-size: 12px; color: var(--text-muted); }
.form-input, .form-select, .form-textarea {
  padding: 8px 10px; border-radius: var(--radius-sm); border: 1px solid var(--border);
  background: var(--bg); color: var(--text); width: 100%;
}
.form-error { color: var(--danger); font-size: 12px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
</style>
