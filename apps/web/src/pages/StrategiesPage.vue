<template>
  <!--
    策略中心页面。

    展示所有已启用的策略配置卡片，支持新建策略。
    每张卡片显示策略名称、版本、频率和描述，点击进入详情页。
  -->
  <div class="page">
    <div class="page-header">
      <h1 class="page-title">策略中心</h1>
      <button class="btn-primary" @click="showCreate = true">新建策略</button>
    </div>

    <div v-if="store.error" class="error-tip">{{ store.error }}</div>
    <div v-else-if="store.loading" class="loading">加载中...</div>
    <div v-else-if="store.items.length === 0" class="empty">暂无策略配置，请点击"新建策略"创建</div>
    <div v-else class="grid">
      <div v-for="item in store.items" :key="item.strategy_id" class="strategy-card">
        <button
          class="star-btn"
          :class="{ starred: item.is_starred }"
          :title="item.is_starred ? '取消星标' : '星标关注'"
          @click.stop="handleToggleStar(item)"
        >
          {{ item.is_starred ? '★' : '☆' }}
        </button>
        <button class="copy-btn" title="复制策略" @click="handleCopyClick(item)">📋</button>
        <div class="card-top">
          <h3 class="strategy-name">{{ item.display_name }}</h3>
          <div class="chips">
            <span class="chip chip-version">v{{ item.version }}</span>
            <span class="chip chip-freq">{{ item.frequency }}</span>
            <span :class="['chip', item.status === 'active' ? 'chip-active' : 'chip-disabled']">
              {{ item.status === 'active' ? '启用' : '禁用' }}
            </span>
          </div>
        </div>
        <p class="strategy-desc">{{ item.description || '暂无描述' }}</p>
        <div class="card-footer">
          <span class="strategy-id mono">{{ item.strategy_id }}</span>
          <RouterLink :to="`/strategies/${item.strategy_id}`" class="detail-btn">查看详情 →</RouterLink>
        </div>
      </div>
    </div>

    <!-- 新建策略弹窗 -->
    <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
      <div class="modal">
        <h2 class="modal-title">新建策略</h2>
        <div class="form-group">
          <label class="form-label">策略 ID</label>
          <input v-model="form.strategy_id" class="form-input" placeholder="如 momentum_rotation" />
        </div>
        <div class="form-group">
          <label class="form-label">策略名称</label>
          <input v-model="form.display_name" class="form-input" placeholder="如 动量轮动" />
        </div>
        <div class="form-group">
          <label class="form-label">描述</label>
          <textarea v-model="form.description" class="form-textarea" rows="2" placeholder="策略描述"></textarea>
        </div>
        <div class="form-group">
          <label class="form-label">频率</label>
          <select v-model="form.frequency" class="form-select">
            <option value="daily">每日</option>
            <option value="weekly">每周</option>
            <option value="monthly">每月</option>
          </select>
        </div>

        <!-- 配置区域 -->
        <div class="form-group">
          <div class="config-header-row">
            <label class="form-label">策略配置</label>
            <button class="toggle-json-btn" @click="advancedMode = !advancedMode">
              {{ advancedMode ? '表单模式' : '高级模式 (JSON)' }}
            </button>
          </div>

          <StrategyConfigForm v-if="!advancedMode" v-model="configJson" />

          <template v-else>
            <textarea v-model="configJsonText" class="form-textarea mono" rows="12" placeholder='{"score": {"factors": {...}}}'></textarea>
            <div v-if="jsonError" class="form-error">{{ jsonError }}</div>
          </template>
        </div>

        <div v-if="store.validationResult && !store.validationResult.valid" class="validation-errors">
          <div v-for="(err, i) in store.validationResult.errors" :key="i" class="validation-error">{{ err }}</div>
        </div>
        <div class="modal-actions">
          <button class="btn-secondary" @click="showCreate = false">取消</button>
          <button class="btn-primary" @click="handleCreate" :disabled="store.loading">
            {{ store.loading ? '创建中...' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 复制策略弹窗 -->
    <div v-if="showCopy" class="modal-overlay" @click.self="showCopy = false">
      <div class="modal">
        <h2 class="modal-title">复制策略</h2>
        <p class="copy-hint">将复制源策略的全部配置，请为副本指定新的 ID 和名称。</p>
        <div class="form-group">
          <label class="form-label">策略 ID（不可与已有策略重复）</label>
          <input v-model="copyForm.strategy_id" class="form-input" placeholder="如 momentum_rotation_v2" />
        </div>
        <div class="form-group">
          <label class="form-label">策略名称</label>
          <input v-model="copyForm.display_name" class="form-input" placeholder="如 动量轮动 V2" />
        </div>
        <div v-if="copyError" class="form-error">{{ copyError }}</div>
        <div class="modal-actions">
          <button class="btn-secondary" @click="showCopy = false">取消</button>
          <button class="btn-primary" @click="handleCopyConfirm" :disabled="copyLoading">
            {{ copyLoading ? '复制中...' : '确认复制' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 策略中心页面。
 *
 * 展示所有策略配置卡片，支持新建策略。
 * 新建时需要提供策略 ID、名称和 JSON 配置。
 */

import { onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import StrategyConfigForm from '../components/StrategyConfigForm.vue'
import { useStrategyStore } from '../stores/strategies'

const store = useStrategyStore()
const showCreate = ref(false)
const showCopy = ref(false)
const copyLoading = ref(false)
const copyError = ref('')
const jsonError = ref('')
const advancedMode = ref(false)

const form = ref({
  strategy_id: '',
  display_name: '',
  description: '',
  frequency: 'daily',
})

/** 复制表单 */
const copyForm = ref({
  strategy_id: '',
  display_name: '',
})

/** 表单模式下的 config_json 对象 */
const configJson = ref<Record<string, unknown>>({ score: { factors: {} } })

/** 高级模式下的 JSON 文本 */
const configJsonText = ref('{\n  "score": {\n    "factors": {}\n  }\n}')

/** 监听新建弹窗关闭，重置表单 */
watch(showCreate, (val) => {
  if (!val) {
    form.value = { strategy_id: '', display_name: '', description: '', frequency: 'daily' }
    configJson.value = { score: { factors: {} } }
    configJsonText.value = '{\n  "score": {\n    "factors": {}\n  }\n}'
    jsonError.value = ''
    advancedMode.value = false
    store.validationResult = null
  }
})

/** 监听复制弹窗关闭，重置表单 */
watch(showCopy, (val) => {
  if (!val) {
    copyForm.value = { strategy_id: '', display_name: '' }
    copyError.value = ''
  }
})

/** 切换星标状态 */
async function handleToggleStar(item: { strategy_id: string; is_starred: boolean }): Promise<void> {
  if (item.is_starred) {
    await store.unstar(item.strategy_id)
  } else {
    await store.star(item.strategy_id)
  }
}

/** 创建策略 */
async function handleCreate(): Promise<void> {
  jsonError.value = ''
  let finalConfig: Record<string, unknown>
  if (advancedMode.value) {
    try {
      finalConfig = JSON.parse(configJsonText.value)
    } catch {
      jsonError.value = 'JSON 格式错误'
      return
    }
  } else {
    finalConfig = configJson.value
  }

  const success = await store.create({
    ...form.value,
    config_json: finalConfig,
  })
  if (success) {
    showCreate.value = false
  }
}

/** 点击复制按钮，获取源策略详情并打开复制弹窗 */
async function handleCopyClick(item: { strategy_id: string; display_name: string }): Promise<void> {
  copyError.value = ''
  copyLoading.value = true
  try {
    await store.loadOne(item.strategy_id)
    copyForm.value = {
      strategy_id: item.strategy_id,
      display_name: item.display_name,
    }
    showCopy.value = true
  } catch (e) {
    copyError.value = e instanceof Error ? e.message : '获取策略详情失败'
  } finally {
    copyLoading.value = false
  }
}

/** 确认复制策略 */
async function handleCopyConfirm(): Promise<void> {
  copyError.value = ''
  if (!copyForm.value.strategy_id.trim() || !copyForm.value.display_name.trim()) {
    copyError.value = '策略 ID 和名称不能为空'
    return
  }
  if (!store.current?.config_json) {
    copyError.value = '未获取到源策略配置，请重新打开弹窗'
    return
  }

  copyLoading.value = true
  try {
    const success = await store.create({
      strategy_id: copyForm.value.strategy_id.trim(),
      display_name: copyForm.value.display_name.trim(),
      version: store.current.version,
      description: store.current.description || '',
      frequency: store.current.frequency,
      config_json: store.current.config_json,
    })
    if (success) {
      showCopy.value = false
    } else {
      copyError.value = store.error || '复制失败'
    }
  } catch {
    copyError.value = '复制策略失败'
  } finally {
    copyLoading.value = false
  }
}

onMounted(() => store.loadAll())
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.page-header { display: flex; align-items: center; justify-content: space-between; }
.page-title { font-size: 22px; font-weight: 700; }
.loading { padding: 60px; text-align: center; color: var(--text-muted); }
.empty { padding: 60px; text-align: center; color: var(--text-muted); }
.error-tip { padding: 12px 16px; background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); border-radius: var(--radius); color: #f87171; font-size: 13px; }

.grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }

.strategy-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color 0.15s;
  position: relative;
}
.strategy-card:hover { border-color: var(--accent); }
.strategy-card:hover .copy-btn { opacity: 1; }
.strategy-card:hover .star-btn:not(.starred) { opacity: 1; }

.star-btn {
  position: absolute;
  top: 12px;
  right: 44px;
  padding: 4px 8px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 16px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, border-color 0.15s, color 0.15s;
  line-height: 1;
}
.star-btn.starred {
  opacity: 1;
  color: #f59e0b;
  border-color: rgba(245, 158, 11, 0.3);
}
.star-btn:hover {
  border-color: #f59e0b;
  color: #f59e0b;
}

.copy-btn {
  position: absolute;
  top: 12px;
  right: 12px;
  padding: 4px 8px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, border-color 0.15s, color 0.15s;
  line-height: 1;
}
.copy-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.card-top { display: flex; flex-direction: column; gap: 8px; }
.strategy-name { font-size: 16px; font-weight: 600; }

.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; }
.chip-version { background: rgba(59,130,246,0.15); color: #60a5fa; }
.chip-freq { background: rgba(34,197,94,0.12); color: #4ade80; }
.chip-scope { background: var(--surface-2); color: var(--text-muted); }
.chip-active { background: rgba(34,197,94,0.12); color: #4ade80; }
.chip-disabled { background: rgba(239,68,68,0.12); color: #f87171; }

.strategy-desc { font-size: 13px; color: var(--text-muted); line-height: 1.6; flex: 1; }

.card-footer { display: flex; align-items: center; justify-content: space-between; margin-top: auto; }
.strategy-id { font-size: 12px; color: var(--text-muted); }

.detail-btn {
  font-size: 13px;
  color: var(--accent);
  font-weight: 500;
  transition: color 0.15s;
}
.detail-btn:hover { color: var(--accent-hover); }

.mono { font-family: monospace; }

/* 按钮 */
.btn-primary {
  padding: 8px 16px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: opacity 0.15s;
}
.btn-primary:hover { opacity: 0.9; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-secondary {
  padding: 8px 16px;
  background: var(--surface-2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  cursor: pointer;
}

/* 弹窗 */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.6);
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
  width: 720px;
  max-height: 90vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.modal-title { font-size: 18px; font-weight: 600; }

.copy-hint { font-size: 13px; color: var(--text-muted); line-height: 1.6; }

.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-label { font-size: 12px; color: var(--text-muted); font-weight: 500; }
.form-input, .form-textarea, .form-select {
  padding: 8px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
}
.form-textarea { resize: vertical; }
.form-error { font-size: 12px; color: #f87171; }

.validation-errors { display: flex; flex-direction: column; gap: 4px; }
.validation-error { font-size: 12px; color: #f87171; padding: 4px 8px; background: rgba(239,68,68,0.08); border-radius: 4px; }

.config-header-row { display: flex; align-items: center; justify-content: space-between; }
.toggle-json-btn {
  padding: 4px 10px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.toggle-json-btn:hover { border-color: var(--accent); color: var(--accent); }

.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }
</style>
