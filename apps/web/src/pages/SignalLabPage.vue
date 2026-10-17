<template>
  <div class="page">
    <div class="page-header">
      <div class="header-main">
        <h1 class="page-title">量价 / 择时研报研究台</h1>
        <span class="code-badge">研究</span>
      </div>
      <p class="page-desc">
        QuantsPlaybook 第一批研报因子的交互研究页：按请求即时计算，展示公式、中间分量、数据覆盖与检验统计，
        用于观察数据和理解原理。本页不创建策略、不触发回测、不写入任何因子表。
      </p>
      <div class="notice-bar">
        <div class="notice-item">
          <span class="notice-label">第一批晋升（已登记到因子中心）</span>
          <RouterLink to="/factors/amount_volatility">成交额波动（标准化）</RouterLink> ·
          <RouterLink to="/factors/price_volume_corr">量价相关</RouterLink> ·
          <RouterLink to="/factors/pv_resonance">价量共振</RouterLink>（择时配合 resonance_score 变换）。
          不晋升：二阶动量（与 return_5d 相关 0.64）、动量期限差、LLT、GSISI、NH−NL%；
          振幅剪刀差暂缓（主题指数缺 OHL）。
        </div>
        <div class="notice-item">
          <span class="notice-label">研究期边界</span>
          研究期 {{ catalog?.research_start ?? '2016-01-01' }} ~ {{ catalog?.research_end ?? '2025-12-31' }}；
          区间触及验证期时结果会标注，仅供观察，不得据此调参。
        </div>
        <div v-for="note in catalog?.offline_notes ?? []" :key="note" class="notice-item">
          <span class="notice-label">说明</span>{{ note }}
        </div>
      </div>
    </div>

    <div v-if="catalogError" class="error-box">{{ catalogError }}</div>

    <div class="card">
      <div class="tabs">
        <button
          v-for="tab in TABS"
          :key="tab.key"
          class="tab"
          :class="{ active: activeTab === tab.key }"
          type="button"
          @click="activeTab = tab.key"
        >
          {{ tab.label }}
        </button>
      </div>

      <div class="form-row wrap">
        <label>日期范围</label>
        <button
          v-for="preset in presets"
          :key="preset.label"
          class="range-btn"
          :class="{ active: activePreset === preset.label }"
          type="button"
          @click="applyPreset(preset)"
        >
          {{ preset.label }}
        </button>
        <input v-model="startDate" type="date" class="date-input" @change="activePreset = ''" />
        <span class="sep">~</span>
        <input v-model="endDate" type="date" class="date-input" @change="activePreset = ''" />
        <span v-if="inValidation" class="validation-tag">含验证期数据</span>
      </div>

      <!-- 单指数择时 -->
      <template v-if="activeTab === 'timing' && catalog">
        <div class="form-row wrap">
          <label>指数</label>
          <select v-model="timingIndex" class="select-input wide">
            <option v-for="item in catalog.indexes" :key="item.index_code" :value="item.index_code">
              {{ item.index_name }}（{{ item.index_code }}）
            </option>
          </select>
          <label>信号</label>
          <select v-model="timingSignal" class="select-input wide">
            <option v-for="item in catalog.timing_signals" :key="item.key" :value="item.key">
              {{ item.label }}
            </option>
          </select>
          <template v-if="isEngineTiming">
            <label>择时口径</label>
            <select v-model="timingStrategy" class="select-input wide">
              <option value="">默认示例（r60v90 口径，参数可调）</option>
              <option
                v-for="item in catalog.timing_strategies"
                :key="item.strategy_id"
                :value="item.strategy_id"
              >
                策略：{{ item.display_name }}（{{ item.strategy_id }}）
              </option>
            </select>
          </template>
          <button class="query-btn" type="button" :disabled="timingLoading" @click="queryTiming">
            {{ timingLoading ? '计算中...' : '计算' }}
          </button>
        </div>
        <div v-if="timingDef && !timingStrategy" class="form-row wrap">
          <label>参数</label>
          <ParamEditor v-model="timingParams" :definition="timingDef" />
        </div>
        <div v-else-if="timingStrategy" class="form-row wrap">
          <label>参数</label>
          <span class="hint">取自策略 timing 块（因子、权重、变换、阈值与代理指数），不可覆盖</span>
        </div>
        <DefinitionCard v-if="shownTimingDef" :definition="shownTimingDef" />
        <div v-if="timingError" class="error-box">{{ timingError }}</div>
        <TimingResultView v-if="timingResult" :result="timingResult" />
      </template>

      <!-- 市场级 -->
      <template v-if="activeTab === 'market' && catalog">
        <div class="form-row wrap">
          <label>信号</label>
          <select v-model="marketSignal" class="select-input wide">
            <option v-for="item in catalog.market_signals" :key="item.key" :value="item.key">
              {{ item.label }}
            </option>
          </select>
          <label>代理指数</label>
          <select v-model="marketProxy" class="select-input wide">
            <option v-for="item in catalog.indexes" :key="item.index_code" :value="item.index_code">
              {{ item.index_name }}（{{ item.index_code }}）
            </option>
          </select>
          <button class="query-btn" type="button" :disabled="marketLoading" @click="queryMarket">
            {{ marketLoading ? '计算中...' : '计算' }}
          </button>
        </div>
        <div v-if="marketDef" class="form-row wrap">
          <label>参数</label>
          <ParamEditor v-model="marketParams" :definition="marketDef" />
        </div>
        <DefinitionCard v-if="marketDef" :definition="marketDef" />
        <div v-if="marketError" class="error-box">{{ marketError }}</div>
        <TimingResultView v-if="marketResult" :result="marketResult" />
      </template>

      <!-- 截面因子 -->
      <template v-if="activeTab === 'cross' && catalog">
        <div class="form-row wrap">
          <label>因子</label>
          <select v-model="crossFactor" class="select-input wide">
            <option v-for="item in catalog.cross_section_factors" :key="item.key" :value="item.key">
              {{ item.label }}
            </option>
          </select>
          <label>未来收益</label>
          <select v-model.number="crossHorizon" class="select-input">
            <option :value="5">5 日</option>
            <option :value="10">10 日</option>
            <option :value="20">20 日</option>
          </select>
          <button class="query-btn" type="button" :disabled="crossLoading" @click="queryCross">
            {{ crossLoading ? '计算中...' : '计算 IC' }}
          </button>
        </div>
        <div v-if="crossDef" class="form-row wrap">
          <label>参数</label>
          <ParamEditor v-model="crossParams" :definition="crossDef" />
        </div>
        <details class="pool">
          <summary>
            指数池：{{ crossCodes.length ? `已选 ${crossCodes.length} 个` : `全部活跃指数（${catalog.indexes.length} 个）` }}
          </summary>
          <div class="pool-actions">
            <button class="link-btn" type="button" @click="crossCodes = catalog.indexes.map((i) => i.index_code)">
              全选
            </button>
            <button class="link-btn" type="button" @click="crossCodes = []">清空（= 全部）</button>
          </div>
          <div class="pool-list">
            <label v-for="item in catalog.indexes" :key="item.index_code" class="pool-item">
              <input v-model="crossCodes" type="checkbox" :value="item.index_code" />
              {{ item.index_name }}
            </label>
          </div>
        </details>
        <DefinitionCard v-if="crossDef" :definition="crossDef" />
        <div v-if="crossError" class="error-box">{{ crossError }}</div>
        <CrossSectionResultView v-if="crossResult" :result="crossResult" />
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 量价 / 择时研报研究台（因子中心下级研究页）。
 *
 * 三个模块：单指数择时信号、市场级情绪（GSISI）、截面因子 IC。
 * 全部为只读即时计算，参数可调仅用于理解原理；晋升判定以研报默认参数为准。
 * 择时综合分走引擎实现，可切换为任一策略的 timing 块（此时参数取自策略配置）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { AxiosError } from 'axios'

import {
  fetchSignalLabCatalog,
  fetchSignalLabCrossSection,
  fetchSignalLabMarket,
  fetchSignalLabTiming,
} from '../api/signalLab'
import CrossSectionResultView from '../components/signalLab/CrossSectionResultView.vue'
import DefinitionCard from '../components/signalLab/DefinitionCard.vue'
import ParamEditor from '../components/signalLab/ParamEditor.vue'
import TimingResultView from '../components/signalLab/TimingResultView.vue'
import type {
  SignalLabCatalog,
  SignalLabCrossSectionResponse,
  SignalLabDefinition,
  SignalLabTimingResponse,
} from '../types/api'
import { RESEARCH_END, RESEARCH_START, VALIDATION_START } from '../utils/datePresets'
import { todayCn } from '../utils/date'

type TabKey = 'timing' | 'market' | 'cross'

/** 走引擎实现的择时综合分信号键 */
const ENGINE_TIMING_KEY = 'existing_composite'

interface RangePreset {
  label: string
  start: string
  end: string
}

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'timing', label: '单指数择时信号' },
  { key: 'market', label: '市场情绪（GSISI）' },
  { key: 'cross', label: '截面因子 IC' },
]

const presets: RangePreset[] = [
  { label: '研究期全段', start: RESEARCH_START, end: RESEARCH_END },
  { label: '近 3 年', start: '2023-01-01', end: RESEARCH_END },
  { label: '2018 下跌', start: '2018-01-01', end: '2018-12-31' },
  { label: '2022 下跌', start: '2022-01-01', end: '2022-12-31' },
  { label: '含验证期', start: '2024-01-01', end: todayCn() },
]

const catalog = ref<SignalLabCatalog | null>(null)
const catalogError = ref('')
const activeTab = ref<TabKey>('timing')
const activePreset = ref('研究期全段')
const startDate = ref(RESEARCH_START)
const endDate = ref(RESEARCH_END)

const timingIndex = ref('000300')
const timingSignal = ref('pv_resonance')
// 择时综合分口径：空字符串 = 默认示例，否则为策略 ID（读取该策略的 timing 块）
const timingStrategy = ref('')
const timingParams = ref<Record<string, number>>({})
const timingResult = ref<SignalLabTimingResponse | null>(null)
const timingLoading = ref(false)
const timingError = ref('')

const marketSignal = ref('gsisi')
const marketProxy = ref('000300')
const marketParams = ref<Record<string, number>>({})
const marketResult = ref<SignalLabTimingResponse | null>(null)
const marketLoading = ref(false)
const marketError = ref('')

const crossFactor = ref('amount_volatility')
const crossHorizon = ref(5)
const crossCodes = ref<string[]>([])
const crossParams = ref<Record<string, number>>({})
const crossResult = ref<SignalLabCrossSectionResponse | null>(null)
const crossLoading = ref(false)
const crossError = ref('')

const inValidation = computed(() => endDate.value >= VALIDATION_START)

/** 按键查找定义 */
function findDef(list: SignalLabDefinition[] | undefined, key: string): SignalLabDefinition | null {
  return list?.find((d) => d.key === key) ?? null
}

const timingDef = computed(() => findDef(catalog.value?.timing_signals, timingSignal.value))
// 择时综合分走引擎实现，可选择任一策略的 timing 块
const isEngineTiming = computed(() => timingSignal.value === ENGINE_TIMING_KEY)
// 策略口径的公式由后端按 timing 块生成：结果与当前选择一致时展示结果中的定义
const shownTimingDef = computed<SignalLabDefinition | null>(() => {
  const result = timingResult.value
  if (timingStrategy.value && result && result.strategy_id === timingStrategy.value) return result.signal
  return timingDef.value
})
const marketDef = computed(() => findDef(catalog.value?.market_signals, marketSignal.value))
const crossDef = computed(() => findDef(catalog.value?.cross_section_factors, crossFactor.value))

/** 取定义的默认参数 */
function defaults(def: SignalLabDefinition | null): Record<string, number> {
  const out: Record<string, number> = {}
  for (const spec of def?.params ?? []) out[spec.name] = spec.default
  return out
}

watch(timingDef, (def) => (timingParams.value = defaults(def)))
watch(marketDef, (def) => (marketParams.value = defaults(def)))
watch(crossDef, (def) => (crossParams.value = defaults(def)))

/** 应用日期预设 */
function applyPreset(preset: RangePreset): void {
  activePreset.value = preset.label
  startDate.value = preset.start
  endDate.value = preset.end
}

/** 提取接口错误信息（优先使用后端 detail） */
function errorText(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
    if (typeof detail === 'string') return detail
    if (err.code === 'ECONNABORTED') return '请求超时，请缩短区间后重试'
    return err.message
  }
  return err instanceof Error ? err.message : String(err)
}

/** 计算单指数择时信号 */
async function queryTiming(): Promise<void> {
  timingLoading.value = true
  timingError.value = ''
  try {
    timingResult.value = await fetchSignalLabTiming(
      timingIndex.value,
      timingSignal.value,
      startDate.value,
      endDate.value,
      timingParams.value,
      isEngineTiming.value && timingStrategy.value ? timingStrategy.value : undefined,
    )
  } catch (err) {
    timingError.value = errorText(err)
  } finally {
    timingLoading.value = false
  }
}

/** 计算市场级信号 */
async function queryMarket(): Promise<void> {
  marketLoading.value = true
  marketError.value = ''
  try {
    marketResult.value = await fetchSignalLabMarket(
      marketSignal.value,
      marketProxy.value,
      startDate.value,
      endDate.value,
      marketParams.value,
    )
  } catch (err) {
    marketError.value = errorText(err)
  } finally {
    marketLoading.value = false
  }
}

/** 计算截面因子 IC */
async function queryCross(): Promise<void> {
  crossLoading.value = true
  crossError.value = ''
  try {
    crossResult.value = await fetchSignalLabCrossSection(
      crossFactor.value,
      startDate.value,
      endDate.value,
      crossHorizon.value,
      crossCodes.value,
      crossParams.value,
    )
  } catch (err) {
    crossError.value = errorText(err)
  } finally {
    crossLoading.value = false
  }
}

watch(activeTab, (tab) => {
  if (tab === 'market' && !marketResult.value && !marketLoading.value) void queryMarket()
  if (tab === 'cross' && !crossResult.value && !crossLoading.value) void queryCross()
})

onMounted(async () => {
  try {
    catalog.value = await fetchSignalLabCatalog()
    marketProxy.value = catalog.value.default_proxy
    timingIndex.value = catalog.value.default_proxy
    timingParams.value = defaults(timingDef.value)
    marketParams.value = defaults(marketDef.value)
    crossParams.value = defaults(crossDef.value)
    await queryTiming()
  } catch (err) {
    catalogError.value = `研究台目录加载失败：${errorText(err)}`
  }
})
</script>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.page-header {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.header-main {
  display: flex;
  align-items: center;
  gap: 10px;
}
.page-title {
  font-size: 22px;
  font-weight: 700;
}
.code-badge {
  font-size: 12px;
  background: rgba(16, 185, 129, 0.15);
  color: #10b981;
  padding: 3px 10px;
  border-radius: 20px;
}
.page-desc {
  color: var(--text-muted);
  font-size: 13px;
  max-width: 980px;
  line-height: 1.7;
  margin: 0;
}
.notice-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.notice-item {
  flex: 1 1 340px;
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.22);
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  font-size: 12px;
  color: #93c5fd;
  line-height: 1.6;
}
.notice-label {
  color: var(--accent);
  font-weight: 600;
  margin-right: 8px;
}
.notice-item a {
  color: #bfdbfe;
  text-decoration: underline;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
}
.tab {
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  padding: 8px 14px;
  font-size: 13px;
  cursor: pointer;
}
.tab.active {
  color: var(--text);
  border-bottom-color: var(--accent);
  font-weight: 600;
}
.form-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.form-row.wrap {
  flex-wrap: wrap;
}
.form-row > label {
  min-width: 56px;
  color: #94a3b8;
  font-size: 13px;
}
.select-input,
.date-input {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 4px;
  padding: 5px 6px;
  font-size: 12px;
}
.select-input {
  width: 100px;
}
.select-input.wide {
  width: 240px;
}
.select-input option {
  background: var(--surface);
}
.range-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  border-radius: 4px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.range-btn.active {
  border-color: var(--accent);
  color: var(--accent);
}
.sep {
  color: #64748b;
}
.validation-tag {
  font-size: 11px;
  color: var(--warning);
  background: rgba(245, 158, 11, 0.12);
  border-radius: 10px;
  padding: 2px 8px;
}
.query-btn {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 6px 16px;
  font-size: 13px;
  cursor: pointer;
}
.query-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.error-box {
  background: rgba(239, 68, 68, 0.12);
  color: #fca5a5;
  border-radius: var(--radius-sm);
  padding: 8px 12px;
  font-size: 13px;
}
.pool {
  font-size: 12px;
  color: var(--text-muted);
}
.pool summary {
  cursor: pointer;
}
.pool-actions {
  display: flex;
  gap: 8px;
  margin: 6px 0;
}
.pool-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}
.pool-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #cbd5e1;
}
.hint {
  color: var(--text-muted);
  font-size: 12px;
}
.link-btn {
  background: transparent;
  border: none;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
</style>
