<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">ETF 池</h1>
      <span class="count-badge">{{ store.total }} 只</span>
    </div>

    <div class="toolbar">
      <div class="search-wrap">
        <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input v-model="query" class="search-input" placeholder="搜索代码或名称..." />
      </div>
      <div class="toolbar-actions">
        <button class="btn-secondary" :disabled="refreshing" @click="handleRefreshData">{{ refreshing ? '刷新中...' : '刷新数据' }}</button>
        <button class="btn-add" @click="openAddModal">+ 添加 ETF</button>
      </div>
    </div>

    <!-- 刷新提示 -->
    <div v-if="refreshMsg" class="refresh-banner" :class="refreshOk ? 'banner-ok' : 'banner-err'">{{ refreshMsg }}</div>

    <div class="table-wrap">
      <div v-if="store.loading" class="loading">加载中...</div>
      <table v-else class="data-table">
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>交易所</th>
            <th>跟踪指数</th>
            <th>类别</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="etf in filtered" :key="etf.etf_code" class="clickable-row" @click="$router.push(`/etfs/${etf.etf_code}`)">
            <td><span class="code-mono">{{ etf.etf_code }}</span></td>
            <td class="name-cell">{{ etf.name_cn }}</td>
            <td>
              <span class="badge" :class="etf.exchange === 'SSE' ? 'badge-sse' : 'badge-szse'">{{ etf.exchange }}</span>
            </td>
            <td class="text-muted">{{ etf.tracking_index_name }}</td>
            <td>
              <span class="badge badge-category">{{ etf.category }}</span>
            </td>
            <td>
              <span class="status-dot" :class="etf.is_active ? 'dot-active' : 'dot-inactive'"></span>
              <span class="text-muted">{{ etf.is_active ? '活跃' : '下架' }}</span>
            </td>
            <td>
              <button class="btn-delete" @click.stop="handleDelete(etf.etf_code)">下架</button>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!store.loading && filtered.length === 0" class="empty">未找到匹配的 ETF</div>
    </div>

    <div v-if="store.total > pageSize" class="pagination">
      <button class="page-btn" :disabled="offset === 0" @click="goPage(offset - pageSize)">上一页</button>
      <span class="page-info">{{ offset + 1 }}–{{ Math.min(offset + pageSize, store.total) }} / 共 {{ store.total }} 条</span>
      <button class="page-btn" :disabled="offset + pageSize >= store.total" @click="goPage(offset + pageSize)">下一页</button>
    </div>

    <!-- 添加 ETF 弹窗 -->
    <div v-if="showAddModal" class="modal-overlay" @click.self="closeAddModal">
      <div class="modal">
        <div class="modal-header">
          <h2 class="modal-title">添加 ETF</h2>
          <button class="modal-close" @click="closeAddModal">✕</button>
        </div>
        <form class="modal-body" @submit.prevent="submitAdd">
          <div class="form-group">
            <label class="form-label">ETF 代码 <span class="required">*</span></label>
            <input
              v-model="addCode"
              class="form-input"
              placeholder="6 位数字，如 510300"
              maxlength="6"
              autofocus
            />
            <span class="form-hint">基金名称、跟踪指数等信息将自动从数据源获取</span>
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

import { triggerEtfRefresh } from '../api/runs'
import { useEtfStore } from '../stores/etfs'

const store = useEtfStore()
const query = ref('')
const refreshing = ref(false)
const refreshMsg = ref('')
const refreshOk = ref(true)
const offset = ref(0)
const pageSize = 200

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return store.items
  return store.items.filter(e => e.etf_code.includes(q) || e.name_cn.toLowerCase().includes(q))
})

/** 触发 ETF 数据刷新 */
async function handleRefreshData() {
  refreshing.value = true
  refreshMsg.value = ''
  try {
    const res = await triggerEtfRefresh()
    refreshMsg.value = `ETF 数据刷新已触发，可在运行记录中查看进度 (${res.run_id.slice(0, 8)}…)`
    refreshOk.value = true
  } catch {
    refreshMsg.value = '触发失败，请重试'
    refreshOk.value = false
  } finally {
    refreshing.value = false
    setTimeout(() => { refreshMsg.value = '' }, 5000)
  }
}

async function goPage(newOffset: number) {
  offset.value = newOffset
  await store.loadAll(newOffset, pageSize)
}

onMounted(() => store.loadAll(0, pageSize))

// --- 添加 ETF ---
const showAddModal = ref(false)
const addCode = ref('')
const addError = ref('')
const addLoading = ref(false)

function openAddModal() {
  addError.value = ''
  showAddModal.value = true
}

function closeAddModal() {
  showAddModal.value = false
  addCode.value = ''
  addError.value = ''
}

async function submitAdd() {
  addError.value = ''
  if (!/^\d{6}$/.test(addCode.value)) {
    addError.value = 'ETF 代码必须为 6 位数字'
    return
  }
  addLoading.value = true
  try {
    await store.addEtf({ etf_code: addCode.value })
    closeAddModal()
  } catch (e: any) {
    addError.value = e?.response?.data?.detail ?? '添加失败，请重试'
  } finally {
    addLoading.value = false
  }
}

// --- 下架 ETF ---
async function handleDelete(etfCode: string) {
  if (!window.confirm(`确认下架 ETF ${etfCode}？此操作可通过重新添加撤销。`)) return
  try {
    await store.removeEtf(etfCode)
  } catch (e: any) {
    alert(e?.response?.data?.detail ?? '操作失败，请重试')
  }
}
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }

.page-header { display: flex; align-items: center; gap: 12px; }
.page-title { font-size: 22px; font-weight: 700; }
.count-badge {
  font-size: 12px;
  background: var(--surface-2);
  color: var(--text-muted);
  padding: 2px 10px;
  border-radius: 20px;
}

.toolbar { display: flex; justify-content: space-between; align-items: center; }
.toolbar-actions { display: flex; gap: 8px; }
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
.search-wrap { position: relative; display: flex; align-items: center; }
.search-icon { position: absolute; left: 10px; color: var(--text-muted); pointer-events: none; }
.search-input {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 8px 12px 8px 32px;
  width: 260px;
  outline: none;
  transition: border-color 0.15s;
}
.search-input:focus { border-color: var(--accent); }
.search-input::placeholder { color: var(--text-muted); }

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

.loading, .empty { padding: 40px; text-align: center; color: var(--text-muted); }

.data-table { width: 100%; border-collapse: collapse; }
.data-table th {
  text-align: left;
  padding: 10px 20px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
  background: rgba(0,0,0,0.1);
}
.data-table td { padding: 13px 20px; border-bottom: 1px solid rgba(51, 65, 85, 0.5); }
.data-table tr:last-child td { border-bottom: none; }

.clickable-row { cursor: pointer; }
.clickable-row:hover td { background: rgba(255,255,255,0.03); }

.code-mono { font-family: monospace; font-size: 13px; color: var(--accent); font-weight: 600; }
.name-cell { font-weight: 500; }
.text-muted { color: var(--text-muted); font-size: 13px; }

.badge { display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; }
.badge-sse { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
.badge-szse { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.badge-category { background: var(--surface-2); color: var(--text-muted); }

.status-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.dot-active { background: var(--success); }
.dot-inactive { background: var(--text-muted); }

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

.pagination { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 8px 0; }
.page-btn {
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s;
}
.page-btn:hover:not(:disabled) { border-color: var(--accent); }
.page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.page-info { font-size: 13px; color: var(--text-muted); }
</style>
