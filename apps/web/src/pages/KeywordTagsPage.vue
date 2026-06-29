<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">关键词标签管理</h1>
      <span class="page-desc">管理新闻分类的关键词→资产标签映射规则</span>
    </div>

    <!-- 新增行 -->
    <div class="card">
      <div class="card-header"><span class="card-title">新增映射</span></div>
      <div class="card-body">
        <div class="add-row">
          <input v-model="newKeyword" class="form-input" placeholder="关键词（如 芯片）" @keyup.enter="handleCreate" />
          <input v-model="newTag" class="form-input" placeholder="资产标签（如 半导体）" @keyup.enter="handleCreate" />
          <input v-model.number="newPriority" class="form-input form-input-sm" type="number" placeholder="优先级" />
          <button class="btn-primary" :disabled="!newKeyword || !newTag" @click="handleCreate">添加</button>
        </div>
      </div>
    </div>

    <!-- 批量导入 -->
    <div class="card">
      <div class="card-header"><span class="card-title">批量导入</span></div>
      <div class="card-body">
        <div class="batch-row">
          <textarea
            v-model="batchText"
            class="form-textarea"
            rows="4"
            placeholder="每行一条：关键词:标签&#10;示例：&#10;芯片:半导体&#10;AI:人工智能"
          />
          <button class="btn-secondary" :disabled="!batchText.trim()" @click="handleBatchImport">导入</button>
        </div>
        <div v-if="batchMsg" class="action-banner" :class="batchOk ? 'banner-ok' : 'banner-err'">{{ batchMsg }}</div>
      </div>
    </div>

    <!-- 列表 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">映射列表</span>
        <label class="checkbox-label">
          <input v-model="showActiveOnly" type="checkbox" @change="loadList" />
          仅显示启用的
        </label>
      </div>
      <div class="card-body">
        <div v-if="loading" class="loading">加载中...</div>
        <div v-else-if="rows.length === 0" class="empty">暂无数据</div>
        <table v-else class="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>关键词</th>
              <th>标签</th>
              <th>启用</th>
              <th>优先级</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.id" :class="{ 'row-inactive': !row.is_active }">
              <td class="text-muted">{{ row.id }}</td>
              <td>
                <input
                  v-if="editingId === row.id"
                  v-model="editForm.keyword"
                  class="form-input form-input-inline"
                />
                <span v-else class="code-mono">{{ row.keyword }}</span>
              </td>
              <td>
                <input
                  v-if="editingId === row.id"
                  v-model="editForm.tag"
                  class="form-input form-input-inline"
                />
                <span v-else>{{ row.tag }}</span>
              </td>
              <td>
                <input
                  v-if="editingId === row.id"
                  v-model="editForm.is_active"
                  type="checkbox"
                />
                <span v-else :class="row.is_active ? 'text-rise' : 'text-muted'">
                  {{ row.is_active ? '是' : '否' }}
                </span>
              </td>
              <td>
                <input
                  v-if="editingId === row.id"
                  v-model.number="editForm.priority"
                  class="form-input form-input-inline form-input-xs"
                  type="number"
                />
                <span v-else>{{ row.priority }}</span>
              </td>
              <td>
                <template v-if="editingId === row.id">
                  <button class="btn-action btn-save" @click="handleSave(row.id)">保存</button>
                  <button class="btn-action btn-cancel" @click="cancelEdit">取消</button>
                </template>
                <template v-else>
                  <button class="btn-action btn-edit" @click="startEdit(row)">编辑</button>
                  <button class="btn-action btn-del" @click="handleDelete(row.id)">删除</button>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/** 关键词标签管理页面。
 *
 * 提供 keyword_tag_config 表的 CRUD 管理界面，
 * 支持新增、编辑、删除、批量导入。
 */
import { onMounted, ref } from 'vue'
import {
  batchImportKeywordTags,
  createKeywordTag,
  deleteKeywordTag,
  fetchKeywordTags,
  updateKeywordTag,
} from '../api/keywordTags'
import type { KeywordTagConfig } from '../types/api'

// ---- 列表 ----
const rows = ref<KeywordTagConfig[]>([])
const loading = ref(false)
const showActiveOnly = ref(false)

async function loadList() {
  loading.value = true
  try {
    rows.value = await fetchKeywordTags(0, 200, showActiveOnly.value)
  } catch {
    rows.value = []
  } finally {
    loading.value = false
  }
}

// ---- 新增 ----
const newKeyword = ref('')
const newTag = ref('')
const newPriority = ref(0)

async function handleCreate() {
  if (!newKeyword.value || !newTag.value) return
  try {
    await createKeywordTag({
      keyword: newKeyword.value.trim(),
      tag: newTag.value.trim(),
      priority: newPriority.value,
    })
    newKeyword.value = ''
    newTag.value = ''
    newPriority.value = 0
    await loadList()
  } catch (e) {
    alert(`添加失败：${e instanceof Error ? e.message : '未知错误'}`)
  }
}

// ---- 编辑 ----
const editingId = ref<number | null>(null)
const editForm = ref({ keyword: '', tag: '', is_active: true, priority: 0 })

function startEdit(row: KeywordTagConfig) {
  editingId.value = row.id
  editForm.value = { keyword: row.keyword, tag: row.tag, is_active: row.is_active, priority: row.priority }
}

function cancelEdit() {
  editingId.value = null
}

async function handleSave(id: number) {
  try {
    await updateKeywordTag(id, editForm.value)
    editingId.value = null
    await loadList()
  } catch (e) {
    alert(`保存失败：${e instanceof Error ? e.message : '未知错误'}`)
  }
}

// ---- 删除 ----
async function handleDelete(id: number) {
  if (!confirm('确定要删除（停用）此映射？')) return
  try {
    await deleteKeywordTag(id)
    await loadList()
  } catch (e) {
    alert(`删除失败：${e instanceof Error ? e.message : '未知错误'}`)
  }
}

// ---- 批量导入 ----
const batchText = ref('')
const batchMsg = ref('')
const batchOk = ref(true)

async function handleBatchImport() {
  const text = batchText.value.trim()
  if (!text) return

  const mappings: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed || trimmed.startsWith('#')) continue
    const colonIdx = trimmed.indexOf(':')
    if (colonIdx === -1) {
      batchMsg.value = `格式错误：${trimmed}`
      batchOk.value = false
      return
    }
    const kw = trimmed.slice(0, colonIdx).trim()
    const tg = trimmed.slice(colonIdx + 1).trim()
    if (kw && tg) mappings[kw] = tg
  }

  if (Object.keys(mappings).length === 0) {
    batchMsg.value = '无有效映射'
    batchOk.value = false
    return
  }

  batchMsg.value = ''
  try {
    const result = await batchImportKeywordTags(mappings)
    batchMsg.value = `导入完成：${result.created} 条`
    batchOk.value = true
    batchText.value = ''
    await loadList()
  } catch (e) {
    batchMsg.value = `导入失败：${e instanceof Error ? e.message : '未知错误'}`
    batchOk.value = false
  }
}

// ---- 生命周期 ----
onMounted(() => {
  loadList()
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 24px; }
.page-header { display: flex; align-items: baseline; gap: 12px; }
.page-title { font-size: 22px; font-weight: 700; }
.page-desc { color: var(--text-muted); font-size: 13px; }

.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
.card-header { display: flex; align-items: center; gap: 12px; padding: 14px 20px; border-bottom: 1px solid var(--border); }
.card-title { font-size: 14px; font-weight: 600; }
.card-body { padding: 16px 20px; }

.add-row, .batch-row { display: flex; gap: 10px; align-items: flex-start; }
.form-input { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 7px 10px; font-size: 13px; outline: none; }
.form-input:focus { border-color: var(--accent); }
.form-input-sm { max-width: 80px; }
.form-input-inline { width: 120px; padding: 4px 6px; font-size: 12px; }
.form-input-xs { width: 60px; padding: 4px 6px; font-size: 12px; }
.form-textarea { background: var(--surface-2); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 10px; font-size: 13px; outline: none; flex: 1; resize: vertical; font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace; }
.form-textarea:focus { border-color: var(--accent); }

.btn-primary { background: var(--accent); color: #fff; border: none; border-radius: var(--radius-sm); padding: 8px 16px; font-size: 13px; font-weight: 500; cursor: pointer; }
.btn-primary:disabled { opacity: .5; cursor: not-allowed; }
.btn-secondary { background: var(--surface); color: var(--text); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 14px; font-size: 13px; font-weight: 500; cursor: pointer; }
.btn-secondary:disabled { opacity: .5; cursor: not-allowed; }

.btn-action { padding: 3px 8px; border-radius: var(--radius-sm); font-size: 12px; border: 1px solid var(--border); cursor: pointer; margin-right: 4px; }
.btn-edit { background: var(--surface); color: var(--accent); }
.btn-save { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn-cancel { background: var(--surface); color: var(--text-muted); }
.btn-del { background: var(--surface); color: var(--danger); }

.checkbox-label { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-muted); cursor: pointer; }

.action-banner { margin-top: 12px; padding: 10px 16px; border-radius: var(--radius-sm); font-size: 13px; }
.banner-ok { background: rgba(34,197,94,.1); border: 1px solid rgba(34,197,94,.3); color: var(--success); }
.banner-err { background: rgba(239,68,68,.1); border: 1px solid rgba(239,68,68,.3); color: var(--danger); }

.data-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.data-table th { text-align: left; padding: 8px 10px; color: var(--text-muted); font-weight: 500; border-bottom: 1px solid var(--border); }
.data-table td { padding: 8px 10px; border-bottom: 1px solid var(--border); }
.data-table tbody tr:hover { background: var(--surface-2); }
.row-inactive { opacity: .5; }

.code-mono { font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace; font-size: 12px; }
.text-rise { color: var(--success); font-weight: 600; }
.text-muted { color: var(--text-muted); }
.loading { padding: 40px; text-align: center; color: var(--text-muted); }
.empty { padding: 40px; text-align: center; color: var(--text-muted); }
</style>
