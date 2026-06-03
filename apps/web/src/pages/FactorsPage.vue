<template>
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">因子中心</h1>
    </div>

    <div v-if="loading" class="empty">加载中...</div>
    <div v-else-if="specs.length === 0" class="empty">暂无因子数据</div>
    <div v-else class="grid">
      <div
        v-for="spec in specs"
        :key="spec.factor_id"
        class="factor-card"
        :class="{ inactive: !spec.is_active }"
      >
        <div class="card-top">
          <span class="factor-id">{{ spec.factor_id }}</span>
          <div class="card-actions">
            <span class="chip" :class="`chip-${spec.category ?? 'default'}`">
              {{ CATEGORY_LABELS[spec.category ?? ''] ?? spec.category ?? '未分类' }}
            </span>
            <button class="edit-btn" @click="startEdit(spec)">编辑</button>
          </div>
        </div>
        <h3 class="factor-name">{{ spec.name }}</h3>
        <p class="factor-desc">{{ spec.description }}</p>
        <div class="card-footer">
          <span class="version">v{{ spec.version }}</span>
          <span class="required">{{ spec.required_data.join(', ') }}</span>
          <span class="status-tag" :class="spec.is_active ? 'active' : 'disabled'">
            {{ spec.is_active ? '启用' : '禁用' }}
          </span>
        </div>
        <RouterLink :to="`/factors/${spec.factor_id}`" class="detail-link">查看详情</RouterLink>
      </div>
    </div>

    <!-- 编辑弹窗 -->
    <div v-if="editing" class="modal-overlay" @click.self="cancelEdit">
      <div class="modal">
        <h2 class="modal-title">编辑因子</h2>
        <div class="form-group">
          <label class="form-label">因子名称</label>
          <input v-model="editForm.name" class="form-input" />
        </div>
        <div class="form-group">
          <label class="form-label">描述</label>
          <textarea v-model="editForm.description" class="form-textarea" rows="3"></textarea>
        </div>
        <div class="form-group">
          <label class="form-label">类别</label>
          <select v-model="editForm.category" class="form-input">
            <option value="">未分类</option>
            <option v-for="(label, key) in CATEGORY_LABELS" :key="key" :value="key">{{ label }}</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">
            <input type="checkbox" v-model="editForm.is_active" />
            启用
          </label>
        </div>
        <div class="modal-actions">
          <button class="cancel-btn" @click="cancelEdit">取消</button>
          <button class="save-btn" :disabled="saving" @click="saveEdit">
            {{ saving ? '保存中...' : '保存' }}
          </button>
        </div>
        <div v-if="editError" class="edit-error">{{ editError }}</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 因子中心页面。
 *
 * 展示所有因子元数据列表，支持编辑因子名称、描述、类别和启用状态。
 * 因子定义持久化在数据库中，代码仅实现计算逻辑。
 */

import { onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { fetchFactorSpecs, updateFactor } from '../api/factors'
import type { FactorSpec } from '../types/api'

const specs = ref<FactorSpec[]>([])
const loading = ref(false)

/** 编辑状态 */
const editing = ref(false)
const editingId = ref('')
const saving = ref(false)
const editError = ref('')
const editForm = reactive({
  name: '',
  description: '',
  category: '',
  is_active: true,
})

const CATEGORY_LABELS: Record<string, string> = {
  volume: '量能',
  momentum: '动量',
  volatility: '波动率',
  flow: '份额流',
  valuation: '估值',
}

/** 进入编辑模式 */
function startEdit(spec: FactorSpec) {
  editingId.value = spec.factor_id
  editForm.name = spec.name
  editForm.description = spec.description
  editForm.category = spec.category ?? ''
  editForm.is_active = spec.is_active
  editError.value = ''
  editing.value = true
}

/** 取消编辑 */
function cancelEdit() {
  editing.value = false
  editingId.value = ''
  editError.value = ''
}

/** 保存编辑 */
async function saveEdit() {
  saving.value = true
  editError.value = ''
  try {
    const updated = await updateFactor(editingId.value, {
      name: editForm.name,
      description: editForm.description,
      category: editForm.category || undefined,
      is_active: editForm.is_active,
    })
    // 更新列表中的对应项
    const idx = specs.value.findIndex(s => s.factor_id === editingId.value)
    if (idx !== -1) {
      specs.value[idx] = updated
    }
    cancelEdit()
  } catch {
    editError.value = '保存失败，请稍后重试'
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  loading.value = true
  try {
    specs.value = await fetchFactorSpecs()
  } catch {
    specs.value = []
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }

.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }

.empty { padding: 60px; text-align: center; color: var(--text-muted); }

.grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }

.factor-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color 0.15s;
}
.factor-card:hover { border-color: var(--accent); }
.factor-card.inactive { opacity: 0.55; }

.card-top { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.factor-id {
  font-family: monospace;
  font-size: 11px;
  color: var(--accent);
  background: rgba(59, 130, 246, 0.1);
  padding: 2px 8px;
  border-radius: 20px;
}

.card-actions { display: flex; align-items: center; gap: 8px; }

.chip { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; white-space: nowrap; }
.chip-volume    { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }
.chip-momentum  { background: rgba(34, 197, 94, 0.12);  color: #4ade80; }
.chip-volatility{ background: rgba(239, 68, 68, 0.12);  color: #f87171; }
.chip-flow      { background: rgba(139, 92, 246, 0.15); color: #a78bfa; }
.chip-valuation { background: rgba(59, 130, 246, 0.15); color: #60a5fa; }
.chip-default   { background: var(--surface-2, rgba(255,255,255,0.05)); color: var(--text-muted); }

.edit-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 2px 10px;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.edit-btn:hover { color: var(--accent); border-color: var(--accent); }

.factor-name { font-size: 16px; font-weight: 600; }
.factor-desc { font-size: 13px; color: var(--text-muted); line-height: 1.6; flex: 1; }

.card-footer { display: flex; align-items: center; gap: 12px; margin-top: auto; }
.version  { font-size: 11px; color: var(--text-muted); }
.required { font-size: 11px; color: var(--text-muted); font-family: monospace; }

.status-tag {
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 20px;
  font-weight: 600;
}
.status-tag.active  { background: rgba(34, 197, 94, 0.12); color: #4ade80; }
.status-tag.disabled { background: rgba(239, 68, 68, 0.12); color: #f87171; }

.detail-link {
  display: block;
  text-align: center;
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
  padding: 6px;
  border: 1px solid rgba(59, 130, 246, 0.2);
  border-radius: var(--radius-sm);
  transition: background 0.15s;
}
.detail-link:hover { background: rgba(59, 130, 246, 0.08); }

/* 编辑弹窗 */
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
  padding: 24px;
  width: 420px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.modal-title { font-size: 16px; font-weight: 600; }

.form-group { display: flex; flex-direction: column; gap: 4px; }
.form-label { font-size: 12px; color: var(--text-muted); display: flex; align-items: center; gap: 6px; }
.form-input {
  background: var(--surface-2, rgba(255,255,255,0.05));
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 6px 10px;
  font-size: 13px;
  outline: none;
}
.form-input:focus { border-color: var(--accent); }
.form-textarea {
  background: var(--surface-2, rgba(255,255,255,0.05));
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  padding: 6px 10px;
  font-size: 13px;
  outline: none;
  resize: vertical;
}
.form-textarea:focus { border-color: var(--accent); }

.modal-actions { display: flex; justify-content: flex-end; gap: 8px; }
.cancel-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
}
.save-btn {
  background: rgba(59, 130, 246, 0.15);
  border: 1px solid rgba(59, 130, 246, 0.3);
  color: var(--accent);
  border-radius: var(--radius-sm);
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
  transition: background 0.15s;
}
.save-btn:hover:not(:disabled) { background: rgba(59, 130, 246, 0.25); }
.save-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.edit-error { font-size: 12px; color: #f87171; text-align: right; }
</style>
