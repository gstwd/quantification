<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">指数数据</h1>
        <span class="count-badge">{{ loading && summaries.length === 0 ? '—' : summaries.length }} 个</span>
      </div>
      <div class="header-actions">
        <RouterLink to="/data-management?dataset=index_daily_bar" class="btn-secondary">维护数据</RouterLink>
        <button class="btn-add" @click="openAddModal">+ 添加指数</button>
      </div>
    </div>

    <!-- 刷新提示 -->
    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="summaries.length === 0" class="empty">暂无指数数据</div>
    <div v-else class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>指数代码</th>
            <th>名称</th>
            <th>最新收盘</th>
            <th>涨跌幅</th>
            <th title="最新收盘与涨跌幅对应的交易日；早于全市场最新交易日的标黄">行情日</th>
            <th>PE(TTM) <HelpTip :text="marketHelp('pe')" /></th>
            <th>PE分位 <HelpTip :text="factorHelp('pe_percentile')" /></th>
            <th>PB <HelpTip :text="marketHelp('pb')" /></th>
            <th>PB分位 <HelpTip :text="factorHelp('pb_percentile')" /></th>
            <th title="PE/PB 及分位对应的估值日期；比行情日落后超过 5 个自然日的标黄">估值日</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in summaries"
            :key="item.index_code"
            class="clickable-row"
            @click="$router.push(`/indexes/${item.index_code}`)"
          >
            <td><span class="code-mono">{{ item.index_code }}</span></td>
            <td>{{ item.index_name }}</td>
            <td class="num-cell">{{ getLatestClose(item.index_code) }}</td>
            <td class="num-cell" :class="getChangePctClass(item.index_code)">
              {{ getChangePct(item.index_code) }}
            </td>
            <td class="num-cell date-cell" :class="{ stale: isBarStale(item) }" :title="item.bar_date ?? ''">
              {{ shortDate(item.bar_date) }}
            </td>
            <td class="num-cell">{{ getPE(item.index_code) }}</td>
            <td class="num-cell">{{ getPEPercentile(item.index_code) }}</td>
            <td class="num-cell">{{ getPB(item.index_code) }}</td>
            <td class="num-cell">{{ getPBPercentile(item.index_code) }}</td>
            <td class="num-cell date-cell" :class="{ stale: isValuationStale(item) }" :title="item.valuation_date ?? ''">
              {{ shortDate(item.valuation_date) }}
            </td>
            <td>
              <button class="btn-delete" @click.stop="handleDelete(item.index_code, item.index_name)">停用</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 添加指数弹窗 -->
    <div v-if="showAddModal" class="modal-overlay" @click.self="closeAddModal">
      <div class="modal">
        <div class="modal-header">
          <h2 class="modal-title">添加指数</h2>
          <button class="modal-close" @click="closeAddModal">&times;</button>
        </div>
        <form class="modal-body" @submit.prevent="submitAdd">
          <div class="form-group">
            <label class="form-label">指数代码 <span class="required">*</span></label>
            <input
              v-model="addCode"
              class="form-input"
              placeholder="如 000300"
              maxlength="6"
              autofocus
            />
          </div>
          <div class="form-group">
            <label class="form-label">指数名称</label>
            <input
              v-model="addName"
              class="form-input"
              placeholder="留空将自动获取"
            />
            <span class="form-hint">名称将优先自动从数据源获取，获取失败时使用此处输入值</span>
          </div>
          <div v-if="addError" class="form-error">{{ addError }}</div>
          <div class="modal-footer">
            <button type="button" class="btn-cancel" @click="closeAddModal">取消</button>
            <button type="submit" class="btn-submit" :disabled="addLoading">
              {{ addLoading ? '获取信息中...' : '添加' }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'

import {
  createIndex,
  deleteIndex,
  fetchIndexSummaries,
} from '../api/market_data'
import type { IndexSummary } from '../types/api'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'
import { parseDateCn } from '../utils/date'

/** 获取指标描述的快捷方法 */
function marketHelp(key: string): string {
  return getIndicator('market_data', key)?.description ?? ''
}
function factorHelp(key: string): string {
  return getIndicator('factors', key)?.description ?? ''
}

const summaries = ref<IndexSummary[]>([])
const loading = ref(false)
/** 加载指数汇总数据（单次请求替代原有的 1+2N 次请求） */
async function loadIndexes() {
  loading.value = true
  try {
    summaries.value = await fetchIndexSummaries()
  } finally {
    loading.value = false
  }
}

/** index_code → IndexSummary 快速查找映射 */
const summaryMap = computed<Record<string, IndexSummary>>(() => {
  const map: Record<string, IndexSummary> = {}
  for (const s of summaries.value) {
    map[s.index_code] = s
  }
  return map
})

/** 全列表最新行情日（作为「最新交易日」参照） */
const latestBarDate = computed<string | null>(() => {
  let latest: string | null = null
  for (const s of summaries.value) {
    if (s.bar_date && (!latest || s.bar_date > latest)) latest = s.bar_date
  }
  return latest
})

/** YYYY-MM-DD → MM-DD（空值显示 —） */
function shortDate(value: string | null | undefined): string {
  return value ? value.slice(5) : '—'
}

/** 行情日是否落后于全列表最新行情日 */
function isBarStale(item: IndexSummary): boolean {
  return !!item.bar_date && !!latestBarDate.value && item.bar_date < latestBarDate.value
}

/** 估值日是否比该指数行情日落后超过 5 个自然日 */
function isValuationStale(item: IndexSummary): boolean {
  if (!item.valuation_date || !item.bar_date) return false
  const lagDays = (parseDateCn(item.bar_date).getTime() - parseDateCn(item.valuation_date).getTime()) / 86_400_000
  return lagDays > 5
}

/** 获取最新收盘价 */
function getLatestClose(code: string): string {
  return summaryMap.value[code]?.close_price?.toFixed(2) ?? '—'
}

/** 获取涨跌幅文本 */
function getChangePct(code: string): string {
  const s = summaryMap.value[code]
  if (!s || s.change_pct === null || s.change_pct === undefined) return '—'
  return (s.change_pct >= 0 ? '+' : '') + s.change_pct.toFixed(2) + '%'
}

/** 获取涨跌幅样式类 */
function getChangePctClass(code: string): string {
  const s = summaryMap.value[code]
  if (!s || s.change_pct === null || s.change_pct === undefined) return ''
  return s.change_pct >= 0 ? 'text-rise' : 'text-fall'
}

/** 获取 PE 值 */
function getPE(code: string): string {
  return summaryMap.value[code]?.pe?.toFixed(1) ?? '—'
}

/** 获取 PE 分位 */
function getPEPercentile(code: string): string {
  const p = summaryMap.value[code]?.pe_percentile
  return p !== null && p !== undefined ? p.toFixed(0) + '%' : '—'
}

/** 获取 PB 值 */
function getPB(code: string): string {
  return summaryMap.value[code]?.pb?.toFixed(2) ?? '—'
}

/** 获取 PB 分位 */
function getPBPercentile(code: string): string {
  const p = summaryMap.value[code]?.pb_percentile
  return p !== null && p !== undefined ? p.toFixed(0) + '%' : '—'
}

onMounted(loadIndexes)

// --- 添加指数 ---
const showAddModal = ref(false)
const addCode = ref('')
const addName = ref('')
const addError = ref('')
const addLoading = ref(false)

/** 打开添加弹窗 */
function openAddModal() {
  addCode.value = ''
  addName.value = ''
  addError.value = ''
  showAddModal.value = true
}

/** 关闭添加弹窗 */
function closeAddModal() {
  showAddModal.value = false
  addCode.value = ''
  addName.value = ''
  addError.value = ''
}

/** 提交添加请求 */
async function submitAdd() {
  addError.value = ''
  const code = addCode.value.trim()
  if (!code) {
    addError.value = '请输入指数代码'
    return
  }
  addLoading.value = true
  try {
    const newIndex = await createIndex({
      index_code: code,
      name_cn: addName.value.trim() || undefined,
    })
    summaries.value.push({
      index_code: newIndex.index_code,
      index_name: newIndex.index_name,
      close_price: null,
      change_pct: null,
      bar_date: null,
      pe: null,
      pe_percentile: null,
      pb: null,
      pb_percentile: null,
      dividend_yield: null,
      valuation_date: null,
    })
    summaries.value.sort((a, b) => a.index_code.localeCompare(b.index_code))
    closeAddModal()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    addError.value = err?.response?.data?.detail ?? '添加失败，请重试'
  } finally {
    addLoading.value = false
  }
}

// --- 停用指数 ---
/** 停用指定指数（软删除，保留历史数据） */
async function handleDelete(indexCode: string, indexName: string) {
  if (!window.confirm(`确认停用指数 ${indexCode}（${indexName}）？停用后历史数据保留，可重新添加恢复。`)) return
  try {
    await deleteIndex(indexCode)
    summaries.value = summaries.value.filter(i => i.index_code !== indexCode)
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    alert(err?.response?.data?.detail ?? '操作失败，请重试')
  }
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 24px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.header-left { display: flex; align-items: center; gap: 10px; }
.header-actions { display: flex; gap: 8px; }
.page-title { font-size: 22px; font-weight: 700; }
.count-badge {
  font-size: 11px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: var(--font-mono);
}
.loading, .empty { padding: 60px; text-align: center; color: var(--text-muted); }

.btn-add {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 8px 16px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}
.btn-add:hover { opacity: 0.85; }
.btn-secondary {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 8px 14px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}
.btn-secondary:hover { background: var(--surface-2); border-color: var(--accent); }
.btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }
.refresh-banner {
  padding: 10px 16px;
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.banner-ok { background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.3); color: var(--success); }
.banner-err { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); color: var(--danger); }

.table-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow-x: auto;
}
.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  text-align: left; padding: 10px 16px;
  font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: rgba(0,0,0,0.1);
  white-space: nowrap;
}
.data-table th:nth-child(n+3) { text-align: right; }
.data-table th:last-child { text-align: left; }
.date-cell { color: var(--text-muted); font-size: 12px; }
.date-cell.stale { color: var(--warning); }
.data-table td { padding: 12px 16px; border-bottom: 1px solid rgba(51,65,85,0.5); }
.data-table tr:last-child td { border-bottom: none; }
.clickable-row { cursor: pointer; transition: background 0.1s; }
.clickable-row:hover td { background: rgba(59,130,246,0.06); }
.code-mono { font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--accent); }
.num-cell { font-family: var(--font-mono); font-size: 13px; text-align: right; }
.text-rise { color: var(--rise); }
.text-fall { color: var(--fall); }

.btn-delete {
  background: transparent;
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: #f87171;
  border-radius: var(--radius-sm);
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.btn-delete:hover { background: rgba(239, 68, 68, 0.1); }

/* Modal */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.modal {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  width: 420px;
  max-width: 90vw;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 18px 20px 14px;
  border-bottom: 1px solid var(--border);
}
.modal-title { font-size: 16px; font-weight: 700; }
.modal-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 16px;
  cursor: pointer;
  padding: 2px 6px;
}
.modal-close:hover { color: var(--text); }
.modal-body { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-label { font-size: 12px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }
.required { color: #f87171; }
.form-input {
  background: var(--surface-2, rgba(255,255,255,0.05));
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 8px 12px;
  font-size: 14px;
  outline: none;
  transition: border-color 0.15s;
}
.form-input:focus { border-color: var(--accent); }
.form-error { font-size: 13px; color: #f87171; padding: 6px 10px; background: rgba(239, 68, 68, 0.1); border-radius: var(--radius-sm); }
.form-hint { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
.modal-footer { display: flex; justify-content: flex-end; gap: 10px; padding-top: 4px; }
.btn-cancel {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 8px 16px;
  font-size: 13px;
  cursor: pointer;
}
.btn-cancel:hover { color: var(--text); }
.btn-submit {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 8px 20px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}
.btn-submit:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-submit:not(:disabled):hover { opacity: 0.85; }
</style>
