<template>
  <div class="page">
    <div class="page-header">
      <div class="header-left">
        <h1 class="page-title">指数数据</h1>
        <span class="count-badge">{{ indexes.length }} 个</span>
      </div>
      <button class="btn-add" @click="openAddModal">+ 添加指数</button>
    </div>

    <div v-if="loading" class="loading">加载中...</div>
    <div v-else-if="indexes.length === 0" class="empty">暂无指数数据</div>
    <div v-else class="table-wrap">
      <table class="data-table">
        <thead>
          <tr>
            <th>指数代码</th>
            <th>名称</th>
            <th>最新收盘</th>
            <th>涨跌幅</th>
            <th>PE(TTM)</th>
            <th>PE分位</th>
            <th>PB</th>
            <th>PB分位</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="item in indexes"
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
            <td class="num-cell">{{ getPE(item.index_code) }}</td>
            <td class="num-cell">{{ getPEPercentile(item.index_code) }}</td>
            <td class="num-cell">{{ getPB(item.index_code) }}</td>
            <td class="num-cell">{{ getPBPercentile(item.index_code) }}</td>
            <td>
              <button class="btn-delete" @click.stop="handleDelete(item.index_code, item.index_name)">删除</button>
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
import { onMounted, ref } from 'vue'

import {
  createIndex,
  deleteIndex,
  fetchBenchmarkIndexes,
  fetchIndexDailyBars,
  fetchIndexValuation,
} from '../api/market_data'
import type { BenchmarkIndex, DailyBar, IndexValuation } from '../types/api'

const indexes = ref<BenchmarkIndex[]>([])
const loading = ref(false)
const latestBars = ref<Record<string, DailyBar>>({})
const latestValuations = ref<Record<string, IndexValuation>>({})

/** 加载指数列表 */
async function loadIndexes() {
  loading.value = true
  try {
    indexes.value = await fetchBenchmarkIndexes()
    await loadLatestData()
  } finally {
    loading.value = false
  }
}

/** 加载所有指数的最新行情和估值数据 */
async function loadLatestData() {
  const promises = indexes.value.map(async (idx) => {
    const code = idx.index_code
    try {
      const [bars, valuations] = await Promise.all([
        fetchIndexDailyBars(code, { limit: 1 }),
        fetchIndexValuation(code, { limit: 1 }),
      ])
      if (bars.length > 0) {
        latestBars.value[code] = bars[0]
      }
      if (valuations.length > 0) {
        latestValuations.value[code] = valuations[0]
      }
    } catch {
      // 单个指数数据获取失败不影响其他指数
    }
  })
  await Promise.all(promises)
}

/** 获取最新收盘价 */
function getLatestClose(code: string): string {
  const bar = latestBars.value[code]
  return bar?.close_price?.toFixed(2) ?? '—'
}

/** 获取涨跌幅文本 */
function getChangePct(code: string): string {
  const bar = latestBars.value[code]
  if (!bar || bar.change_pct === null || bar.change_pct === undefined) return '—'
  return (bar.change_pct >= 0 ? '+' : '') + bar.change_pct.toFixed(2) + '%'
}

/** 获取涨跌幅样式类 */
function getChangePctClass(code: string): string {
  const bar = latestBars.value[code]
  if (!bar || bar.change_pct === null || bar.change_pct === undefined) return ''
  return bar.change_pct >= 0 ? 'text-rise' : 'text-fall'
}

/** 获取 PE 值 */
function getPE(code: string): string {
  return latestValuations.value[code]?.pe?.toFixed(1) ?? '—'
}

/** 获取 PE 分位 */
function getPEPercentile(code: string): string {
  const p = latestValuations.value[code]?.pe_percentile
  return p !== null && p !== undefined ? p.toFixed(0) + '%' : '—'
}

/** 获取 PB 值 */
function getPB(code: string): string {
  return latestValuations.value[code]?.pb?.toFixed(2) ?? '—'
}

/** 获取 PB 分位 */
function getPBPercentile(code: string): string {
  const p = latestValuations.value[code]?.pb_percentile
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
    indexes.value.push(newIndex)
    indexes.value.sort((a, b) => a.index_code.localeCompare(b.index_code))
    closeAddModal()
  } catch (e: unknown) {
    const err = e as { response?: { data?: { detail?: string } } }
    addError.value = err?.response?.data?.detail ?? '添加失败，请重试'
  } finally {
    addLoading.value = false
  }
}

// --- 删除指数 ---
/** 删除指定指数 */
async function handleDelete(indexCode: string, indexName: string) {
  if (!window.confirm(`确认删除指数 ${indexCode}（${indexName}）？`)) return
  try {
    await deleteIndex(indexCode)
    indexes.value = indexes.value.filter(i => i.index_code !== indexCode)
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
.page-title { font-size: 22px; font-weight: 700; }
.count-badge {
  font-size: 11px;
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
  padding: 2px 8px;
  border-radius: 20px;
  font-family: monospace;
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

.table-wrap {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
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
.data-table td { padding: 12px 16px; border-bottom: 1px solid rgba(51,65,85,0.5); }
.data-table tr:last-child td { border-bottom: none; }
.clickable-row { cursor: pointer; transition: background 0.1s; }
.clickable-row:hover td { background: rgba(59,130,246,0.06); }
.code-mono { font-family: monospace; font-size: 13px; font-weight: 600; color: var(--accent); }
.num-cell { font-family: monospace; font-size: 13px; text-align: right; }
.text-rise { color: #22c55e; }
.text-fall { color: #ef4444; }

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
