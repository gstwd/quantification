<template>
  <!--
    策略详情页面。

    展示策略配置的完整内容，包括各模块配置（评分、过滤、排名、组合、风控）。
    支持编辑和删除策略。
  -->
  <div class="page">
    <div v-if="store.error" class="error-tip">{{ store.error }}</div>
    <div v-else-if="store.loading" class="loading">加载中...</div>
    <template v-else-if="store.current">
      <div class="page-header">
        <div class="header-left">
          <h1 class="page-title">{{ store.current.display_name }}</h1>
          <div class="chips">
            <span class="chip chip-version">v{{ store.current.version }}</span>
            <span class="chip chip-freq">{{ store.current.frequency }}</span>
            <span :class="['chip', store.current.status === 'active' ? 'chip-active' : 'chip-disabled']">
              {{ store.current.status === 'active' ? '启用' : '禁用' }}
            </span>
          </div>
        </div>
        <div class="header-actions">
          <button class="btn-accent" @click="handleRunSignal" :disabled="running">
            {{ running ? '执行中...' : '运行信号' }}
          </button>
          <button class="btn-secondary" @click="handleLoadSignals" :disabled="loadingSignals">
            {{ loadingSignals ? '加载中...' : '查看信号' }}
          </button>
          <button class="btn-accent" @click="handleRunAllocation" :disabled="allocating">
            {{ allocating ? '计算中...' : '查看决策' }}
          </button>
          <button class="btn-secondary" @click="showEdit = true">编辑</button>
          <button class="btn-danger" @click="handleDelete">删除</button>
        </div>
      </div>

      <p class="description">{{ store.current.description || '暂无描述' }}</p>

      <!-- 策略配置模块 -->
      <div class="config-grid">
        <!-- 资产范围 -->
        <div v-if="indexCodesList.length > 0" class="config-card">
          <div class="config-header">资产范围</div>
          <div class="config-body">
            <div class="config-row">
              <span class="config-key">指定指数</span>
              <div class="factor-weights">
                <span v-for="code in indexCodesList" :key="code" class="factor-tag">
                  <span class="factor-name">{{ code }}</span>
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- 评分模块 -->
        <div class="config-card">
          <div class="config-header">评分模块 (Score)</div>
          <div class="config-body">
            <div v-if="scoreConfig" class="config-section">
              <div class="config-row">
                <span class="config-key">因子权重</span>
                <div class="factor-weights">
                  <span v-for="(weight, fid) in scoreConfig.factors" :key="fid" class="factor-tag">
                    <span class="factor-name">{{ fid }}</span>
                    <span class="factor-weight">{{ weight }}</span>
                  </span>
                </div>
              </div>
              <div v-if="scoreConfig.transforms && Object.keys(scoreConfig.transforms).length > 0" class="config-row">
                <span class="config-key">变换函数</span>
                <div class="factor-weights">
                  <span v-for="(fn, fid) in scoreConfig.transforms" :key="fid" class="factor-tag transform">
                    <span class="factor-name">{{ fid }}</span>
                    <span class="factor-weight">{{ fn }}</span>
                  </span>
                </div>
              </div>
              <div class="config-row">
                <span class="config-key">缺失策略</span>
                <span class="config-val">{{ scoreConfig.missing_factor_strategy || 'ignore' }}</span>
              </div>
              <div class="config-row">
                <span class="config-key">评分模式</span>
                <span class="config-val">{{ scoreConfig.scoring_mode || 'absolute' }}</span>
              </div>
            </div>
            <div v-else class="config-empty">未配置</div>
          </div>
        </div>

        <!-- 择时模块 -->
        <div class="config-card">
          <div class="config-header">择时模块 (Timing)</div>
          <div class="config-body">
            <div v-if="timingConfig" class="config-section">
              <div class="config-row">
                <span class="config-key">因子权重</span>
                <div class="factor-weights">
                  <span v-for="(weight, fid) in timingConfig.factors" :key="fid" class="factor-tag">
                    <span class="factor-name">{{ fid }}</span>
                    <span class="factor-weight">{{ weight }}</span>
                  </span>
                </div>
              </div>
              <div class="config-row">
                <span class="config-key">阈值</span>
                <span class="config-val">
                  进攻 ≥ {{ timingConfig.thresholds?.offensive ?? 65 }}，防守 ≤ {{ timingConfig.thresholds?.defensive ?? 35 }}
                </span>
              </div>
              <div v-if="timingConfig.proxy_index_codes && timingConfig.proxy_index_codes.length > 0" class="config-row">
                <span class="config-key">代理指数</span>
                <span class="config-val">{{ timingConfig.proxy_index_codes.join(', ') }}</span>
              </div>
            </div>
            <div v-else class="config-empty">未配置（无择时）</div>
          </div>
        </div>

        <!-- 过滤模块 -->
        <div class="config-card">
          <div class="config-header">过滤模块 (Filter)</div>
          <div class="config-body">
            <div v-if="filterConfig && filterConfig.rules && filterConfig.rules.length > 0" class="config-section">
              <div class="config-row">
                <span class="config-key">逻辑</span>
                <span class="config-val">{{ filterConfig.logic || 'AND' }}</span>
              </div>
              <div v-for="(rule, i) in filterConfig.rules" :key="i" class="config-row">
                <span class="config-key">规则 {{ i + 1 }}</span>
                <span class="config-val mono">
                  {{ rule.factor }} {{ rule.op }}
                  {{ rule.compare_to ? rule.compare_to : rule.value }}
                </span>
              </div>
            </div>
            <div v-else class="config-empty">未配置（无过滤）</div>
          </div>
        </div>

        <!-- 排名模块 -->
        <div class="config-card">
          <div class="config-header">排名模块 (Rank)</div>
          <div class="config-body">
            <div v-if="rankConfig" class="config-section">
              <div class="config-row">
                <span class="config-key">排序</span>
                <span class="config-val">{{ rankConfig.sort_by || 'score' }} {{ rankConfig.order || 'desc' }}</span>
              </div>
              <div v-if="rankConfig.top_n" class="config-row">
                <span class="config-key">Top N</span>
                <span class="config-val">{{ rankConfig.top_n }}</span>
              </div>
              <div v-if="rankConfig.bottom_n" class="config-row">
                <span class="config-key">Bottom N</span>
                <span class="config-val">{{ rankConfig.bottom_n }}</span>
              </div>
            </div>
            <div v-else class="config-empty">默认排名</div>
          </div>
        </div>

        <!-- 组合模块 -->
        <div class="config-card">
          <div class="config-header">组合模块 (Portfolio)</div>
          <div class="config-body">
            <div v-if="portfolioConfig" class="config-section">
              <div class="config-row">
                <span class="config-key">分配方法</span>
                <span class="config-val">{{ portfolioConfig.method }}</span>
              </div>
              <div v-if="portfolioConfig.timing_exposure" class="config-row">
                <span class="config-key">择时仓位</span>
                <div class="factor-weights">
                  <span v-for="(exp, regime) in portfolioConfig.timing_exposure" :key="regime" class="factor-tag">
                    <span class="factor-name">{{ regime }}</span>
                    <span class="factor-weight">{{ (exp * 100).toFixed(0) }}%</span>
                  </span>
                </div>
              </div>
              <div v-if="portfolioConfig.default_exposure != null" class="config-row">
                <span class="config-key">默认仓位</span>
                <span class="config-val">{{ (portfolioConfig.default_exposure * 100).toFixed(0) }}%（无择时信号时）</span>
              </div>
            </div>
            <div v-else class="config-empty">未配置（信号模式）</div>
          </div>
        </div>

        <!-- 风控模块 -->
        <div class="config-card">
          <div class="config-header">风控模块 (Risk)</div>
          <div class="config-body">
            <div v-if="riskConfig" class="config-section">
              <div v-if="riskConfig.max_asset_weight != null" class="config-row">
                <span class="config-key">单资产上限</span>
                <span class="config-val">{{ (riskConfig.max_asset_weight * 100).toFixed(0) }}%</span>
              </div>
              <div v-if="riskConfig.max_portfolio_exposure != null && riskConfig.max_portfolio_exposure < 1" class="config-row">
                <span class="config-key">组合上限</span>
                <span class="config-val">{{ (riskConfig.max_portfolio_exposure * 100).toFixed(0) }}%</span>
              </div>
              <div v-if="riskConfig.min_cash_ratio != null && riskConfig.min_cash_ratio > 0" class="config-row">
                <span class="config-key">最低现金</span>
                <span class="config-val">{{ (riskConfig.min_cash_ratio * 100).toFixed(0) }}%</span>
              </div>
            </div>
            <div v-else class="config-empty">未配置（无风控）</div>
          </div>
        </div>

        <!-- 调仓模块 -->
        <div class="config-card">
          <div class="config-header">调仓模块 (Rebalance)</div>
          <div class="config-body">
            <div v-if="rebalanceConfig" class="config-section">
              <div class="config-row">
                <span class="config-key">频率</span>
                <span class="config-val">{{ rebalanceConfig.frequency || 'daily' }}</span>
              </div>
              <div v-if="rebalanceConfig.day_of_week != null" class="config-row">
                <span class="config-key">周调仓日</span>
                <span class="config-val">周{{ ['一','二','三','四','五'][rebalanceConfig.day_of_week] }}</span>
              </div>
              <div v-if="rebalanceConfig.day_of_month != null" class="config-row">
                <span class="config-key">月调仓日</span>
                <span class="config-val">每月第 {{ rebalanceConfig.day_of_month }} 个交易日</span>
              </div>
            </div>
            <div v-else class="config-empty">未配置（默认每日调仓）</div>
          </div>
        </div>

      </div>

      <!-- 执行结果提示 -->
      <div v-if="runMessage" :class="['run-tip', runSuccess ? 'run-success' : 'run-error']">
        {{ runMessage }}
      </div>

      <!-- 最新信号表格 -->
      <div v-if="signals.length > 0" class="signals-section">
        <div class="signals-header">
          <h2 class="section-title">最新信号（{{ signalTradeDate }}）</h2>
          <span class="signals-count">共 {{ signals.length }} 条</span>
        </div>
        <div class="signals-table-wrap">
          <table class="signals-table">
            <thead>
              <tr>
                <th>排名</th>
                <th>指数代码</th>
                <th>得分</th>
                <th>信号等级</th>
                <th>信号标签</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(sig, i) in signals" :key="sig.index_code">
                <td class="text-muted">{{ i + 1 }}</td>
                <td class="mono">{{ sig.index_code }}</td>
                <td class="mono">{{ sig.signal_score.toFixed(1) }}</td>
                <td>
                  <span :class="['level-tag', `level-${sig.signal_level.toLowerCase()}`]">
                    {{ sig.signal_level }}
                  </span>
                </td>
                <td>{{ sig.signal_label }}</td>
                <td>
                  <button class="toggle-detail-btn" @click="toggleSignalDetail(sig.index_code)">
                    {{ expandedSignal === sig.index_code ? '收起' : '详情' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <!-- 展开的信号详情 -->
        <div v-if="expandedSignal && expandedSignalPayload" class="signal-detail">
          <div class="signal-detail-header">信号详情：{{ expandedSignal }}</div>
          <pre class="signal-detail-json mono">{{ JSON.stringify(expandedSignalPayload, null, 2) }}</pre>
        </div>
      </div>

      <!-- 决策管线结果 -->
      <div v-if="allocation" class="allocation-section">
        <h2 class="section-title">决策管线结果</h2>

        <!-- 择时信号 -->
        <div v-if="allocation.timing && allocation.timing.regime" class="alloc-card">
          <div class="alloc-header">择时信号</div>
          <div class="alloc-body">
            <div class="alloc-row">
              <span class="alloc-key">Regime</span>
              <span :class="['regime-tag', `regime-${allocation.timing.regime}`]">
                {{ allocation.timing.regime === 'offensive' ? '进攻' : allocation.timing.regime === 'defensive' ? '防守' : '中性' }}
              </span>
            </div>
            <div class="alloc-row">
              <span class="alloc-key">置信度</span>
              <span class="alloc-val">{{ ((allocation.timing.confidence ?? 0) * 100).toFixed(0) }}%</span>
            </div>
            <div v-if="allocation.timing.label" class="alloc-row">
              <span class="alloc-key">标签</span>
              <span class="alloc-val">{{ allocation.timing.label }}</span>
            </div>
          </div>
        </div>

        <!-- 排名 -->
        <div v-if="allocation.rankings && allocation.rankings.length > 0" class="alloc-card">
          <div class="alloc-header">资产排名 (Top {{ allocation.rankings.length }})</div>
          <div class="alloc-body">
            <table class="rank-table">
              <thead>
                <tr>
                  <th>排名</th>
                  <th>代码</th>
                  <th>名称</th>
                  <th>得分</th>
                  <th>类别</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in allocation.rankings" :key="r.etf_code">
                  <td>{{ i + 1 }}</td>
                  <td class="mono">{{ r.etf_code }}</td>
                  <td>{{ r.name_cn }}</td>
                  <td class="mono">{{ r.score?.toFixed(1) }}</td>
                  <td>{{ r.category }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- 仓位分配 -->
        <div v-if="allocation.plan && Object.keys(allocation.plan.positions ?? {}).length > 0" class="alloc-card">
          <div class="alloc-header">仓位分配</div>
          <div class="alloc-body">
            <div class="alloc-row">
              <span class="alloc-key">方法</span>
              <span class="alloc-val">{{ allocation.plan.method }}</span>
            </div>
            <div class="alloc-row">
              <span class="alloc-key">总仓位</span>
              <span class="alloc-val">{{ (allocation.plan.total_exposure * 100).toFixed(1) }}%</span>
            </div>
            <div class="alloc-row">
              <span class="alloc-key">现金</span>
              <span class="alloc-val">{{ (allocation.plan.cash_ratio * 100).toFixed(1) }}%</span>
            </div>
            <div class="position-tags">
              <span v-for="(w, code) in allocation.plan.positions" :key="code" class="position-tag">
                <span class="pos-code">{{ code }}</span>
                <span class="pos-weight">{{ (w * 100).toFixed(1) }}%</span>
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 原始配置 JSON -->
      <details class="json-section">
        <summary class="json-toggle">原始配置 JSON</summary>
        <pre class="json-block mono">{{ formattedJson }}</pre>
      </details>

      <!-- 编辑弹窗 -->
      <div v-if="showEdit" class="modal-overlay" @click.self="showEdit = false">
        <div class="modal">
          <h2 class="modal-title">编辑策略</h2>
          <div class="form-group">
            <label class="form-label">策略名称</label>
            <input v-model="editForm.display_name" class="form-input" />
          </div>
          <div class="form-group">
            <label class="form-label">描述</label>
            <textarea v-model="editForm.description" class="form-textarea" rows="2"></textarea>
          </div>
          <div class="form-group">
            <label class="form-label">频率</label>
            <select v-model="editForm.frequency" class="form-select">
              <option value="daily">每日</option>
              <option value="weekly">每周</option>
              <option value="monthly">每月</option>
            </select>
          </div>
          <div class="form-group">
            <div class="config-header-row">
              <label class="form-label">策略配置</label>
              <button class="toggle-json-btn" @click="editAdvancedMode = !editAdvancedMode">
                {{ editAdvancedMode ? '表单模式' : '高级模式 (JSON)' }}
              </button>
            </div>

            <StrategyConfigForm v-if="!editAdvancedMode" v-model="editConfigJson" />

            <template v-else>
              <textarea v-model="editConfigText" class="form-textarea mono" rows="14"></textarea>
              <div v-if="editJsonError" class="form-error">{{ editJsonError }}</div>
            </template>
          </div>
          <div class="modal-actions">
            <button class="btn-secondary" @click="showEdit = false">取消</button>
            <button class="btn-primary" @click="handleUpdate" :disabled="store.loading">
              {{ store.loading ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </template>
    <div v-else class="loading">策略不存在</div>
  </div>
</template>

<script setup lang="ts">
/**
 * 策略详情页面。
 *
 * 展示策略配置的各模块详情，支持编辑和删除操作。
 */

import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { runAllocation } from '../api/strategies'
import { fetchLatestSignals } from '../api/signals'
import { triggerStrategyRun } from '../api/runs'
import StrategyConfigForm from '../components/StrategyConfigForm.vue'
import { useStrategyStore } from '../stores/strategies'
import type { AllocationResponse, SignalRow } from '../types/api'

const props = defineProps<{ strategyId: string }>()
const store = useStrategyStore()
const router = useRouter()

const running = ref(false)
const allocating = ref(false)
const runMessage = ref('')
const runSuccess = ref(true)
const allocation = ref<AllocationResponse | null>(null)

/** 信号查看 */
const signals = ref<SignalRow[]>([])
const signalTradeDate = ref('')
const loadingSignals = ref(false)
const expandedSignal = ref<string | null>(null)

const showEdit = ref(false)
const editJsonError = ref('')
const editAdvancedMode = ref(false)
const editForm = ref({ display_name: '', description: '', frequency: 'daily' })
const editConfigJson = ref<Record<string, unknown>>({})
const editConfigText = ref('')

/** 从 config_json 中提取各模块配置 */
const configJson = computed(() => store.current?.config_json ?? {})
const indexCodesList = computed(() => (configJson.value.index_codes as string[]) || [])
const scoreConfig = computed(() => configJson.value.score as { factors?: Record<string, number>; transforms?: Record<string, string>; missing_factor_strategy?: string; scoring_mode?: string } | undefined)
const timingConfig = computed(() => configJson.value.timing as { factors?: Record<string, number>; transforms?: Record<string, string>; thresholds?: { offensive?: number; defensive?: number }; proxy_index_codes?: string[] } | undefined)
const filterConfig = computed(() => configJson.value.filters as { logic?: string; rules?: Array<{ factor: string; op: string; value?: number | number[]; compare_to?: string }> } | undefined)
const rankConfig = computed(() => configJson.value.rank as { sort_by?: string; order?: string; top_n?: number; bottom_n?: number } | undefined)
const portfolioConfig = computed(() => configJson.value.portfolio as { method?: string; timing_exposure?: Record<string, number>; default_exposure?: number } | undefined)
const riskConfig = computed(() => configJson.value.risk as { max_asset_weight?: number; max_portfolio_exposure?: number; min_cash_ratio?: number } | undefined)
const rebalanceConfig = computed(() => configJson.value.rebalance as { frequency?: string; day_of_week?: number; day_of_month?: number } | undefined)

/** 格式化 JSON 用于展示 */
const formattedJson = computed(() => {
  try {
    return JSON.stringify(configJson.value, null, 2)
  } catch {
    return '{}'
  }
})

/** 监听编辑弹窗打开，初始化表单 */
watch(showEdit, (val) => {
  if (val && store.current) {
    editForm.value = {
      display_name: store.current.display_name,
      description: store.current.description,
      frequency: store.current.frequency || 'daily',
    }
    editConfigJson.value = JSON.parse(JSON.stringify(store.current.config_json))
    editConfigText.value = JSON.stringify(store.current.config_json, null, 2)
    editJsonError.value = ''
    editAdvancedMode.value = false
  }
})

/** 保存编辑 */
async function handleUpdate(): Promise<void> {
  editJsonError.value = ''
  let finalConfig: Record<string, unknown>
  if (editAdvancedMode.value) {
    try {
      finalConfig = JSON.parse(editConfigText.value)
    } catch {
      editJsonError.value = 'JSON 格式错误'
      return
    }
  } else {
    finalConfig = editConfigJson.value
  }

  const success = await store.update(props.strategyId, {
    ...editForm.value,
    config_json: finalConfig,
  })
  if (success) {
    showEdit.value = false
  }
}

/** 展开的信号详情 payload */
const expandedSignalPayload = computed(() => {
  if (!expandedSignal.value) return null
  const sig = signals.value.find((s) => s.index_code === expandedSignal.value)
  return sig?.signal_payload ?? null
})

/** 加载最新信号 */
async function handleLoadSignals(): Promise<void> {
  loadingSignals.value = true
  try {
    const res = await fetchLatestSignals(props.strategyId, 0, 100)
    signals.value = res.items
    signalTradeDate.value = res.items.length > 0 ? String(res.items[0].trade_date) : ''
    expandedSignal.value = null
    if (res.items.length === 0) {
      runMessage.value = '暂无信号数据，请先运行信号计算'
      runSuccess.value = true
    }
  } catch (e) {
    runMessage.value = e instanceof Error ? e.message : '加载信号失败'
    runSuccess.value = false
  } finally {
    loadingSignals.value = false
  }
}

/** 切换信号详情展开 */
function toggleSignalDetail(indexCode: string): void {
  expandedSignal.value = expandedSignal.value === indexCode ? null : indexCode
}

/** 运行信号计算 */
async function handleRunSignal(): Promise<void> {
  running.value = true
  runMessage.value = ''
  try {
    await triggerStrategyRun(props.strategyId)
    runMessage.value = '信号计算任务已提交，后台执行中。可前往"运行记录"页面查看进度。'
    runSuccess.value = true
  } catch (e) {
    runMessage.value = e instanceof Error ? e.message : '信号计算触发失败'
    runSuccess.value = false
  } finally {
    running.value = false
  }
}

/** 运行决策管线 */
async function handleRunAllocation(): Promise<void> {
  allocating.value = true
  runMessage.value = ''
  try {
    allocation.value = await runAllocation(props.strategyId)
    runMessage.value = '决策管线执行完成'
    runSuccess.value = true
  } catch (e) {
    runMessage.value = e instanceof Error ? e.message : '决策管线执行失败'
    runSuccess.value = false
    allocation.value = null
  } finally {
    allocating.value = false
  }
}

/** 删除策略 */
async function handleDelete(): Promise<void> {
  if (!confirm('确定删除此策略？此操作不可撤销。')) return
  const success = await store.remove(props.strategyId)
  if (success) {
    router.push('/strategies')
  }
}

onMounted(() => store.loadOne(props.strategyId))
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.loading { padding: 60px; text-align: center; color: var(--text-muted); }
.error-tip { padding: 12px 16px; background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); border-radius: var(--radius); color: #f87171; font-size: 13px; }

.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.header-left { display: flex; flex-direction: column; gap: 8px; }
.page-title { font-size: 22px; font-weight: 700; }
.header-actions { display: flex; gap: 8px; }

.chips { display: flex; gap: 6px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; }
.chip-version { background: rgba(59,130,246,0.15); color: #60a5fa; }
.chip-freq { background: rgba(34,197,94,0.12); color: #4ade80; }
.chip-active { background: rgba(34,197,94,0.12); color: #4ade80; }
.chip-disabled { background: rgba(239,68,68,0.12); color: #f87171; }

.description { color: var(--text-muted); font-size: 14px; line-height: 1.7; max-width: 700px; }

/* 配置网格 */
.config-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.config-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.config-header {
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.config-body { padding: 8px 0; }
.config-section { display: flex; flex-direction: column; }
.config-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(51,65,85,0.3);
}
.config-row:last-child { border-bottom: none; }
.config-key { font-size: 12px; color: var(--text-muted); min-width: 70px; flex-shrink: 0; }
.config-val { font-size: 12px; }
.config-empty { padding: 20px 16px; text-align: center; color: var(--text-muted); font-size: 12px; }

.factor-weights { display: flex; gap: 4px; flex-wrap: wrap; }
.factor-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: var(--surface-2);
  border-radius: 12px;
  font-size: 11px;
}
.factor-tag.transform { background: rgba(168,85,247,0.12); }
.factor-name { color: var(--text-muted); }
.factor-weight { color: var(--accent); font-weight: 600; }

/* JSON 区域 */
.json-section { margin-top: 4px; }
.json-toggle {
  cursor: pointer;
  font-size: 13px;
  color: var(--text-muted);
  padding: 8px 0;
}
.json-block {
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  font-size: 12px;
  overflow-x: auto;
  max-height: 400px;
  white-space: pre-wrap;
  word-break: break-all;
}

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
}
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

.btn-danger {
  padding: 8px 16px;
  background: rgba(239,68,68,0.1);
  color: #f87171;
  border: 1px solid rgba(239,68,68,0.3);
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

.form-group { display: flex; flex-direction: column; gap: 6px; }
.form-label { font-size: 12px; color: var(--text-muted); font-weight: 500; }
.form-input, .form-textarea {
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

.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 8px; }

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

/* 执行按钮 */
.btn-accent {
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
.btn-accent:hover:not(:disabled) { opacity: 0.85; }
.btn-accent:disabled { opacity: 0.5; cursor: not-allowed; }

/* 执行结果提示 */
.run-tip {
  padding: 10px 16px;
  border-radius: var(--radius);
  font-size: 13px;
}
.run-success { background: rgba(34,197,94,0.1); border: 1px solid rgba(34,197,94,0.25); color: #4ade80; }
.run-error { background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.25); color: #f87171; }

/* 决策结果区域 */
.allocation-section { display: flex; flex-direction: column; gap: 12px; }
.section-title { font-size: 16px; font-weight: 600; }

.alloc-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.alloc-header {
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
}
.alloc-body { padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.alloc-row { display: flex; align-items: center; gap: 8px; }
.alloc-key { font-size: 12px; color: var(--text-muted); min-width: 60px; }
.alloc-val { font-size: 12px; }

.regime-tag {
  font-size: 12px;
  padding: 2px 10px;
  border-radius: 12px;
  font-weight: 600;
}
.regime-offensive { background: rgba(34,197,94,0.15); color: #4ade80; }
.regime-neutral { background: rgba(245,158,11,0.15); color: #f59e0b; }
.regime-defensive { background: rgba(239,68,68,0.15); color: #f87171; }

/* 排名表格 */
.rank-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.rank-table th {
  text-align: left;
  padding: 6px 8px;
  color: var(--text-muted);
  font-weight: 500;
  border-bottom: 1px solid var(--border);
}
.rank-table td {
  padding: 6px 8px;
  border-bottom: 1px solid rgba(51,65,85,0.3);
}

/* 仓位标签 */
.position-tags { display: flex; gap: 6px; flex-wrap: wrap; padding-top: 4px; }
.position-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  background: var(--surface-2);
  border-radius: 12px;
  font-size: 11px;
}
.pos-code { color: var(--text-muted); }
.pos-weight { color: var(--accent); font-weight: 600; }

/* 信号表格 */
.signals-section {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.signals-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.signals-header .section-title { margin: 0; }
.signals-count { font-size: 12px; color: var(--text-muted); }
.signals-table-wrap { overflow-x: auto; }
.signals-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.signals-table th {
  text-align: left;
  padding: 8px 14px;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  border-bottom: 1px solid var(--border);
}
.signals-table td { padding: 10px 14px; border-bottom: 1px solid rgba(51,65,85,0.3); }
.signals-table tr:last-child td { border-bottom: none; }

.level-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  font-weight: 600;
}
.level-high { background: rgba(34,197,94,0.15); color: #4ade80; }
.level-mid { background: rgba(245,158,11,0.15); color: #f59e0b; }
.level-low { background: rgba(239,68,68,0.12); color: #f87171; }

.toggle-detail-btn {
  padding: 2px 8px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.toggle-detail-btn:hover { border-color: var(--accent); color: var(--accent); }

.signal-detail {
  padding: 12px 16px;
  border-top: 1px solid var(--border);
  background: var(--surface-2);
}
.signal-detail-header { font-size: 12px; font-weight: 600; color: var(--text-muted); margin-bottom: 8px; }
.signal-detail-json {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px;
  font-size: 11px;
  overflow-x: auto;
  max-height: 300px;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
