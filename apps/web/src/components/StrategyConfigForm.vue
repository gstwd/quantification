<template>
  <!--
    策略配置表单组件。

    将 config_json 拆分为 6 个模块卡片进行结构化编辑：
    评分（必填）、择时、过滤、排名、组合、风控。
    支持实时校验，输出标准 config_json 对象。
  -->
  <div class="config-form">
    <!-- 校验错误提示 -->
    <div v-if="errors.length > 0" class="validation-box">
      <div v-for="(err, i) in errors" :key="i" class="validation-item">{{ err }}</div>
    </div>

    <!-- ═══ 资产范围（策略级配置） ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('scope')">
        <span class="module-title">资产范围 <HelpTip :text="scHelp('index_codes')" /></span>
        <span :class="['arrow', expanded.scope ? 'open' : '']">▾</span>
      </div>
      <div v-show="expanded.scope" class="module-body">
        <div class="module-desc">指定策略运行的目标指数。为空时自动使用全部可用指数。</div>
        <div class="sub-field">
          <label class="sub-label">指数代码（逗号分隔）</label>
          <input
            v-model="indexCodesInput"
            class="fp-input"
            placeholder="如 000300,000905，留空=全部"
          />
        </div>
      </div>
    </div>

    <!-- ═══ 评分模块（必填） ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('score')">
        <span class="module-title">评分模块 (Score) <HelpTip :text="scHelp('score')" /></span>
        <span class="module-badge required">必填</span>
        <span :class="['arrow', expanded.score ? 'open' : '']">▾</span>
      </div>
      <div v-show="expanded.score" class="module-body">
        <div class="module-desc">为每个资产计算综合得分，通过因子加权实现。至少选择 1 个因子。</div>

        <!-- 因子列表表头 -->
        <div class="factor-header">
          <span class="fh-factor">因子</span>
          <span class="fh-weight">权重</span>
          <span class="fh-transform">变换函数</span>
          <span class="fh-action"></span>
        </div>

        <FactorPicker
          v-for="(row, i) in scoreFactors"
          :key="i"
          :factors="availableFactors"
          :model-value="row"
          @update:model-value="updateScoreFactor(i, $event)"
          @remove="removeScoreFactor(i)"
        />

        <button class="add-btn" @click="addScoreFactor">+ 添加因子</button>

        <div class="sub-field">
          <label class="sub-label">缺失因子策略</label>
          <div class="radio-row">
            <label class="radio-opt">
              <input type="radio" v-model="scoreMissingStrategy" value="ignore" /> 忽略（重新归一化权重）
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="scoreMissingStrategy" value="zero" /> 按零处理
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="scoreMissingStrategy" value="exclude" /> 排除资产
            </label>
          </div>
        </div>

        <div class="sub-field">
          <label class="sub-label">评分模式 <HelpTip :text="scHelp('scoring_mode')" /></label>
          <div class="radio-row">
            <label class="radio-opt">
              <input type="radio" v-model="scoreScoringMode" value="absolute" /> 绝对评分（每资产独立）
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="scoreScoringMode" value="rank" /> 排名分（横截面排名）
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="scoreScoringMode" value="zscore" /> Z-Score（横截面标准化）
            </label>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 择时模块（可选） ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('timing')">
        <span class="module-title">择时模块 (Timing) <HelpTip :text="scHelp('timing')" /></span>
        <span class="module-badge optional">可选</span>
        <label class="toggle-switch" @click.stop>
          <input type="checkbox" v-model="timingEnabled" />
          <span class="toggle-track"></span>
        </label>
        <span :class="['arrow', expanded.timing ? 'open' : '']">▾</span>
      </div>
      <div v-show="timingEnabled && expanded.timing" class="module-body">
        <div class="module-desc">基于市场估值/趋势/量能判断 regime（进攻/中性/防守），控制组合总仓位。</div>

        <div class="factor-header">
          <span class="fh-factor">因子</span>
          <span class="fh-weight">权重</span>
          <span class="fh-transform">变换函数</span>
          <span class="fh-action"></span>
        </div>

        <FactorPicker
          v-for="(row, i) in timingFactors"
          :key="i"
          :factors="availableFactors"
          :model-value="row"
          @update:model-value="updateTimingFactor(i, $event)"
          @remove="removeTimingFactor(i)"
        />

        <button class="add-btn" @click="addTimingFactor">+ 添加因子</button>

        <div class="threshold-row">
          <div class="threshold-field">
            <label class="sub-label">进攻阈值</label>
            <div class="threshold-input-wrap">
              <input
                v-model.number="timingOffensive"
                type="number"
                min="0"
                max="100"
                class="fp-input"
              />
              <span class="threshold-hint">得分 ≥ 此值时判定为进攻</span>
            </div>
          </div>
          <div class="threshold-field">
            <label class="sub-label">防守阈值</label>
            <div class="threshold-input-wrap">
              <input
                v-model.number="timingDefensive"
                type="number"
                min="0"
                max="100"
                class="fp-input"
              />
              <span class="threshold-hint">得分 ≤ 此值时判定为防守</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 过滤模块（可选） ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('filter')">
        <span class="module-title">过滤模块 (Filter) <HelpTip :text="scHelp('filter')" /></span>
        <span class="module-badge optional">可选</span>
        <label class="toggle-switch" @click.stop>
          <input type="checkbox" v-model="filterEnabled" />
          <span class="toggle-track"></span>
        </label>
        <span :class="['arrow', expanded.filter ? 'open' : '']">▾</span>
      </div>
      <div v-show="filterEnabled && expanded.filter" class="module-body">
        <div class="module-desc">按条件过滤不符合要求的资产。多条规则可选 AND（全部满足）或 OR（任一满足）。</div>

        <div class="sub-field">
          <label class="sub-label">规则逻辑</label>
          <div class="radio-row">
            <label class="radio-opt">
              <input type="radio" v-model="filterLogic" value="AND" /> AND（全部满足）
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="filterLogic" value="OR" /> OR（任一满足）
            </label>
          </div>
        </div>

        <div v-for="(rule, i) in filterRules" :key="i" class="filter-rule-row">
          <select
            :value="rule.factor"
            class="fp-select fr-factor"
            @change="updateFilterRule(i, 'factor', ($event.target as HTMLSelectElement).value)"
          >
            <option value="" disabled>选择因子</option>
            <optgroup v-for="group in groupedFactors" :key="group.category" :label="group.category || '其他'">
              <option v-for="f in group.items" :key="f.factor_id" :value="f.factor_id">
                {{ f.factor_id }}
              </option>
            </optgroup>
          </select>

          <select
            :value="rule.op"
            class="fp-select fr-op"
            @change="updateFilterRule(i, 'op', ($event.target as HTMLSelectElement).value)"
          >
            <option value="gt">大于 (>)</option>
            <option value="lt">小于 (<)</option>
            <option value="gte">大于等于 (≥)</option>
            <option value="lte">小于等于 (≤)</option>
            <option value="eq">等于 (=)</option>
            <option value="neq">不等于 (≠)</option>
            <option value="between">区间 (between)</option>
          </select>

          <!-- between 模式：双值输入 -->
          <template v-if="rule.op === 'between'">
            <input
              :value="Array.isArray(rule.value) ? rule.value[0] : ''"
              type="number"
              step="any"
              class="fp-input fr-value"
              placeholder="最小值"
              @input="updateBetweenValue(i, 0, ($event.target as HTMLInputElement).value)"
            />
            <span class="fr-sep">~</span>
            <input
              :value="Array.isArray(rule.value) ? rule.value[1] : ''"
              type="number"
              step="any"
              class="fp-input fr-value"
              placeholder="最大值"
              @input="updateBetweenValue(i, 1, ($event.target as HTMLInputElement).value)"
            />
          </template>

          <!-- 非 between 模式：支持固定值 / 跨因子比较 -->
          <template v-else>
            <select
              :value="rule.compare_to ? 'compare' : 'value'"
              class="fp-select fr-mode"
              @change="onFilterModeChange(i, ($event.target as HTMLSelectElement).value)"
            >
              <option value="value">固定值</option>
              <option value="compare">因子比较</option>
            </select>

            <template v-if="rule.compare_to">
              <select
                :value="rule.compare_to"
                class="fp-select fr-factor"
                @change="updateFilterRule(i, 'compare_to', ($event.target as HTMLSelectElement).value)"
              >
                <option value="" disabled>选择比较因子</option>
                <optgroup v-for="group in groupedFactors" :key="group.category" :label="group.category || '其他'">
                  <option v-for="f in group.items" :key="f.factor_id" :value="f.factor_id">
                    {{ f.factor_id }}
                  </option>
                </optgroup>
              </select>
            </template>
            <template v-else>
              <input
                :value="typeof rule.value === 'number' ? rule.value : ''"
                type="number"
                step="any"
                class="fp-input fr-value"
                placeholder="值"
                @input="updateFilterRule(i, 'value', parseFloat(($event.target as HTMLInputElement).value) || 0)"
              />
            </template>
          </template>

          <button class="fp-remove" @click="removeFilterRule(i)" title="移除">×</button>
        </div>

        <button class="add-btn" @click="addFilterRule">+ 添加规则</button>
      </div>
    </div>

    <!-- ═══ 排名模块 ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('rank')">
        <span class="module-title">排名模块 (Rank) <HelpTip :text="scHelp('rank')" /></span>
        <span class="module-badge always">始终启用</span>
        <span :class="['arrow', expanded.rank ? 'open' : '']">▾</span>
      </div>
      <div v-show="expanded.rank" class="module-body">
        <div class="module-desc">对资产按得分排序，支持取 Top N 或 Bottom N。</div>

        <div class="rank-grid">
          <div class="sub-field">
            <label class="sub-label">排序字段</label>
            <select v-model="rankSortBy" class="fp-select">
              <option value="score">综合得分</option>
              <option value="momentum_rank">动量排名</option>
              <option value="valuation_rank">估值排名</option>
            </select>
          </div>
          <div class="sub-field">
            <label class="sub-label">排序方向</label>
            <div class="radio-row">
              <label class="radio-opt">
                <input type="radio" v-model="rankOrder" value="desc" /> 降序（高→低）
              </label>
              <label class="radio-opt">
                <input type="radio" v-model="rankOrder" value="asc" /> 升序（低→高）
              </label>
            </div>
          </div>
          <div class="sub-field">
            <label class="sub-label">Top N</label>
            <input
              v-model="rankTopN"
              type="number"
              min="1"
              class="fp-input"
              placeholder="留空=全部"
            />
          </div>
          <div class="sub-field">
            <label class="sub-label">Bottom N</label>
            <input
              v-model="rankBottomN"
              type="number"
              min="1"
              class="fp-input"
              placeholder="留空=不启用"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 组合模块（可选） ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('portfolio')">
        <span class="module-title">组合模块 (Portfolio) <HelpTip :text="scHelp('portfolio')" /></span>
        <span class="module-badge optional">可选</span>
        <label class="toggle-switch" @click.stop>
          <input type="checkbox" v-model="portfolioEnabled" />
          <span class="toggle-track"></span>
        </label>
        <span :class="['arrow', expanded.portfolio ? 'open' : '']">▾</span>
      </div>
      <div v-show="portfolioEnabled && expanded.portfolio" class="module-body">
        <div class="module-desc">配置权重分配方法。未启用时为信号模式（只输出得分和排名，不分配仓位）。</div>

        <div class="sub-field">
          <label class="sub-label">分配方法</label>
          <div class="radio-row">
            <label class="radio-opt">
              <input type="radio" v-model="portfolioMethod" value="equal_weight" /> 等权分配
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="portfolioMethod" value="score_weight" /> 得分加权
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="portfolioMethod" value="winner_take_all" /> 赢家通吃（最高分独占）
            </label>
          </div>
        </div>

        <div class="sub-field">
          <label class="sub-label">默认仓位（无择时信号时）</label>
          <div class="slider-row">
            <input
              v-model.number="portfolioDefaultExposure"
              type="range"
              min="0"
              max="100"
              step="5"
              class="slider"
            />
            <input
              v-model.number="portfolioDefaultExposure"
              type="number"
              min="0"
              max="100"
              class="fp-input slider-value"
            />
            <span class="exposure-unit">%</span>
          </div>
        </div>

        <div class="sub-field">
          <label class="sub-label">择时仓位控制（各 regime 下的总仓位上限）</label>
          <div class="exposure-row">
            <div class="exposure-field">
              <span class="exposure-label">进攻</span>
              <input
                v-model.number="exposureOffensive"
                type="number"
                min="0"
                max="100"
                step="5"
                class="fp-input exposure-input"
              />
              <span class="exposure-unit">%</span>
            </div>
            <div class="exposure-field">
              <span class="exposure-label">中性</span>
              <input
                v-model.number="exposureNeutral"
                type="number"
                min="0"
                max="100"
                step="5"
                class="fp-input exposure-input"
              />
              <span class="exposure-unit">%</span>
            </div>
            <div class="exposure-field">
              <span class="exposure-label">防守</span>
              <input
                v-model.number="exposureDefensive"
                type="number"
                min="0"
                max="100"
                step="5"
                class="fp-input exposure-input"
              />
              <span class="exposure-unit">%</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 风控模块（可选） ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('risk')">
        <span class="module-title">风控模块 (Risk) <HelpTip :text="scHelp('risk')" /></span>
        <span class="module-badge optional">可选</span>
        <label class="toggle-switch" @click.stop>
          <input type="checkbox" v-model="riskEnabled" />
          <span class="toggle-track"></span>
        </label>
        <span :class="['arrow', expanded.risk ? 'open' : '']">▾</span>
      </div>
      <div v-show="riskEnabled && expanded.risk" class="module-body">
        <div class="module-desc">设置仓位约束：单资产上限、组合总仓位上限、最低现金比例。</div>

        <div class="risk-grid">
          <div class="sub-field">
            <label class="sub-label">单资产仓位上限</label>
            <div class="slider-row">
              <input
                v-model.number="riskMaxAssetWeight"
                type="range"
                min="5"
                max="100"
                step="5"
                class="slider"
              />
              <input
                v-model.number="riskMaxAssetWeight"
                type="number"
                min="5"
                max="100"
                class="fp-input slider-value"
              />
              <span class="exposure-unit">%</span>
            </div>
          </div>
          <div class="sub-field">
            <label class="sub-label">组合总仓位上限</label>
            <div class="slider-row">
              <input
                v-model.number="riskMaxPortfolioExposure"
                type="range"
                min="10"
                max="100"
                step="5"
                class="slider"
              />
              <input
                v-model.number="riskMaxPortfolioExposure"
                type="number"
                min="10"
                max="100"
                class="fp-input slider-value"
              />
              <span class="exposure-unit">%</span>
            </div>
          </div>
          <div class="sub-field">
            <label class="sub-label">最低现金比例</label>
            <div class="slider-row">
              <input
                v-model.number="riskMinCashRatio"
                type="range"
                min="0"
                max="50"
                step="5"
                class="slider"
              />
              <input
                v-model.number="riskMinCashRatio"
                type="number"
                min="0"
                max="50"
                class="fp-input slider-value"
              />
              <span class="exposure-unit">%</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 调仓模块（可选） ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('rebalance')">
        <span class="module-title">调仓模块 (Rebalance) <HelpTip :text="scHelp('rebalance')" /></span>
        <span class="module-badge optional">可选</span>
        <label class="toggle-switch" @click.stop>
          <input type="checkbox" v-model="rebalanceEnabled" />
          <span class="toggle-track"></span>
        </label>
        <span :class="['arrow', expanded.rebalance ? 'open' : '']">▾</span>
      </div>
      <div v-show="rebalanceEnabled && expanded.rebalance" class="module-body">
        <div class="module-desc">控制策略调仓频率。周度/月度可指定具体调仓日。回测中非调仓日沿用上次持仓。</div>

        <div class="sub-field">
          <label class="sub-label">调仓频率</label>
          <select v-model="rebalanceFrequency" class="fp-select">
            <option value="daily">每日</option>
            <option value="weekly">每周</option>
            <option value="monthly">每月</option>
          </select>
        </div>

        <div v-if="rebalanceFrequency === 'weekly'" class="sub-field">
          <label class="sub-label">周调仓日</label>
          <select v-model.number="rebalanceDayOfWeek" class="fp-select">
            <option :value="0">周一</option>
            <option :value="1">周二</option>
            <option :value="2">周三</option>
            <option :value="3">周四</option>
            <option :value="4">周五</option>
          </select>
        </div>

        <div v-if="rebalanceFrequency === 'monthly'" class="sub-field">
          <label class="sub-label">月调仓日</label>
          <input
            v-model.number="rebalanceDayOfMonth"
            type="number"
            min="1"
            max="28"
            class="fp-input"
            placeholder="1-28"
          />
          <span class="threshold-hint">每月第几个交易日进行调仓（1-28）</span>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
/**
 * 策略配置表单组件。
 *
 * 将 config_json 拆分为 6 个模块进行结构化编辑，
 * 实时输出标准 config_json 对象。
 */

import { computed, onMounted, reactive, ref, watch } from 'vue'

import { fetchFactorSpecs } from '../api/factors'
import type { FactorSpec } from '../types/api'
import FactorPicker from './FactorPicker.vue'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'

/** 获取策略配置描述的快捷方法 */
function scHelp(key: string): string {
  return getIndicator('strategy_config', key)?.description ?? ''
}

/** 因子行数据 */
interface FactorRowValue {
  factor_id: string
  weight: number
  transform?: string
}

/** 过滤规则 */
interface FilterRuleValue {
  factor: string
  op: string
  value: number | number[]
  compare_to?: string
}

const props = defineProps<{
  /** config_json 对象 */
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, unknown>): void
}>()

// ── 可用因子列表 ──────────────────────────────────────────────────
const availableFactors = ref<FactorSpec[]>([])

onMounted(async () => {
  try {
    availableFactors.value = await fetchFactorSpecs()
  } catch {
    availableFactors.value = []
  }
})

/** 按 category 分组（供过滤模块使用） */
const groupedFactors = computed(() => {
  const groups = new Map<string, FactorSpec[]>()
  for (const f of availableFactors.value) {
    if (!f.is_active) continue
    const cat = f.category || '其他'
    if (!groups.has(cat)) groups.set(cat, [])
    groups.get(cat)!.push(f)
  }
  return Array.from(groups.entries()).map(([category, items]) => ({ category, items }))
})

// ── 模块展开状态 ──────────────────────────────────────────────────
const expanded = reactive({
  scope: false,
  score: true,
  timing: false,
  filter: false,
  rank: false,
  portfolio: false,
  risk: false,
  rebalance: false,
})

function toggleModule(key: keyof typeof expanded): void {
  expanded[key] = !expanded[key]
}

// ── 资产范围 ──────────────────────────────────────────────────────
const indexCodesInput = ref('')

function initScope(): void {
  const codes = props.modelValue.index_codes as string[] | undefined
  indexCodesInput.value = codes && codes.length > 0 ? codes.join(', ') : ''
}

// ── 评分模块 ──────────────────────────────────────────────────────
const scoreFactors = ref<FactorRowValue[]>([])
const scoreMissingStrategy = ref('ignore')
const scoreScoringMode = ref('absolute')

function initScore(): void {
  const score = props.modelValue.score as Record<string, unknown> | undefined
  if (!score) return
  const factors = (score.factors ?? {}) as Record<string, number>
  const transforms = (score.transforms ?? {}) as Record<string, string>
  scoreFactors.value = Object.entries(factors).map(([fid, w]) => ({
    factor_id: fid,
    weight: w,
    transform: transforms[fid] || undefined,
  }))
  scoreMissingStrategy.value = (score.missing_factor_strategy as string) || 'ignore'
  scoreScoringMode.value = (score.scoring_mode as string) || 'absolute'
}

function addScoreFactor(): void {
  scoreFactors.value.push({ factor_id: '', weight: 0.5 })
}

function updateScoreFactor(i: number, val: FactorRowValue): void {
  scoreFactors.value[i] = val
  emitConfig()
}

function removeScoreFactor(i: number): void {
  scoreFactors.value.splice(i, 1)
  emitConfig()
}

// ── 择时模块 ──────────────────────────────────────────────────────
const timingEnabled = ref(false)
const timingFactors = ref<FactorRowValue[]>([])
const timingOffensive = ref(65)
const timingDefensive = ref(35)

function initTiming(): void {
  const timing = props.modelValue.timing as Record<string, unknown> | undefined
  if (!timing) { timingEnabled.value = false; return }
  timingEnabled.value = true
  const factors = (timing.factors ?? {}) as Record<string, number>
  const transforms = (timing.transforms ?? {}) as Record<string, string>
  timingFactors.value = Object.entries(factors).map(([fid, w]) => ({
    factor_id: fid,
    weight: w,
    transform: transforms[fid] || undefined,
  }))
  const thresholds = (timing.thresholds ?? {}) as Record<string, number>
  timingOffensive.value = thresholds.offensive ?? 65
  timingDefensive.value = thresholds.defensive ?? 35
}

function addTimingFactor(): void {
  timingFactors.value.push({ factor_id: '', weight: 0.5 })
}

function updateTimingFactor(i: number, val: FactorRowValue): void {
  timingFactors.value[i] = val
  emitConfig()
}

function removeTimingFactor(i: number): void {
  timingFactors.value.splice(i, 1)
  emitConfig()
}

// ── 过滤模块 ──────────────────────────────────────────────────────
const filterEnabled = ref(false)
const filterLogic = ref('AND')
const filterRules = ref<FilterRuleValue[]>([])

function initFilter(): void {
  const filters = props.modelValue.filters as Record<string, unknown> | undefined
  if (!filters) { filterEnabled.value = false; return }
  filterEnabled.value = true
  filterLogic.value = (filters.logic as string) || 'AND'
  filterRules.value = ((filters.rules ?? []) as Array<Record<string, unknown>>).map(r => ({
    factor: r.factor as string || '',
    op: r.op as string || 'gt',
    value: r.value as number | number[],
    compare_to: r.compare_to as string | undefined,
  }))
}

function addFilterRule(): void {
  filterRules.value.push({ factor: '', op: 'gt', value: 0, compare_to: undefined })
}

function updateFilterRule(i: number, key: string, value: unknown): void {
  ;(filterRules.value[i] as Record<string, unknown>)[key] = value
  emitConfig()
}

function updateBetweenValue(i: number, index: number, raw: string): void {
  const rule = filterRules.value[i]
  const arr = Array.isArray(rule.value) ? [...rule.value] : [0, 0]
  arr[index] = raw === '' ? 0 : parseFloat(raw)
  rule.value = arr
  emitConfig()
}

function onFilterModeChange(i: number, mode: string): void {
  const rule = filterRules.value[i]
  if (mode === 'compare') {
    rule.compare_to = ''
    rule.value = 0  // 清空固定值
  } else {
    rule.compare_to = undefined
  }
  emitConfig()
}

function removeFilterRule(i: number): void {
  filterRules.value.splice(i, 1)
  emitConfig()
}

// ── 排名模块 ──────────────────────────────────────────────────────
const rankSortBy = ref('score')
const rankOrder = ref('desc')
const rankTopN = ref<string>('')
const rankBottomN = ref<string>('')

function initRank(): void {
  const rank = props.modelValue.rank as Record<string, unknown> | undefined
  if (!rank) return
  rankSortBy.value = (rank.sort_by as string) || 'score'
  rankOrder.value = (rank.order as string) || 'desc'
  rankTopN.value = rank.top_n != null ? String(rank.top_n) : ''
  rankBottomN.value = rank.bottom_n != null ? String(rank.bottom_n) : ''
}

// ── 组合模块 ──────────────────────────────────────────────────────
const portfolioEnabled = ref(false)
const portfolioMethod = ref('equal_weight')
const portfolioDefaultExposure = ref(50)
const exposureOffensive = ref(80)
const exposureNeutral = ref(50)
const exposureDefensive = ref(20)

function initPortfolio(): void {
  const portfolio = props.modelValue.portfolio as Record<string, unknown> | undefined
  if (!portfolio) { portfolioEnabled.value = false; return }
  portfolioEnabled.value = true
  portfolioMethod.value = (portfolio.method as string) || 'equal_weight'
  portfolioDefaultExposure.value = Math.round(((portfolio.default_exposure as number) ?? 0.5) * 100)
  const te = (portfolio.timing_exposure ?? {}) as Record<string, number>
  exposureOffensive.value = Math.round((te.offensive ?? 0.8) * 100)
  exposureNeutral.value = Math.round((te.neutral ?? 0.5) * 100)
  exposureDefensive.value = Math.round((te.defensive ?? 0.2) * 100)
}

// ── 风控模块 ──────────────────────────────────────────────────────
const riskEnabled = ref(false)
const riskMaxAssetWeight = ref(30)
const riskMaxPortfolioExposure = ref(100)
const riskMinCashRatio = ref(0)

function initRisk(): void {
  const risk = props.modelValue.risk as Record<string, unknown> | undefined
  if (!risk) { riskEnabled.value = false; return }
  riskEnabled.value = true
  riskMaxAssetWeight.value = Math.round(((risk.max_asset_weight as number) ?? 0.3) * 100)
  riskMaxPortfolioExposure.value = Math.round(((risk.max_portfolio_exposure as number) ?? 1.0) * 100)
  riskMinCashRatio.value = Math.round(((risk.min_cash_ratio as number) ?? 0) * 100)
}

// ── 调仓模块 ──────────────────────────────────────────────────────
const rebalanceEnabled = ref(false)
const rebalanceFrequency = ref('daily')
const rebalanceDayOfWeek = ref<number | null>(null)
const rebalanceDayOfMonth = ref<number | null>(null)

function initRebalance(): void {
  const rebalance = props.modelValue.rebalance as Record<string, unknown> | undefined
  if (!rebalance) { rebalanceEnabled.value = false; return }
  rebalanceEnabled.value = true
  rebalanceFrequency.value = (rebalance.frequency as string) || 'daily'
  rebalanceDayOfWeek.value = (rebalance.day_of_week as number) ?? null
  rebalanceDayOfMonth.value = (rebalance.day_of_month as number) ?? null
}

// ── 校验 ──────────────────────────────────────────────────────────
const errors = computed((): string[] => {
  const errs: string[] = []
  const validFactors = scoreFactors.value.filter(f => f.factor_id)
  if (validFactors.length === 0) {
    errs.push('评分模块：至少需要 1 个因子')
  }
  for (const f of validFactors) {
    if (f.weight === 0) {
      errs.push(`评分模块：因子 ${f.factor_id} 权重为 0，将不参与评分`)
    }
  }
  if (timingEnabled.value) {
    const validTiming = timingFactors.value.filter(f => f.factor_id)
    if (validTiming.length === 0) {
      errs.push('择时模块：已启用但未配置因子')
    }
    if (timingOffensive.value <= timingDefensive.value) {
      errs.push('择时模块：进攻阈值必须大于防守阈值')
    }
  }
  if (filterEnabled.value) {
    for (let i = 0; i < filterRules.value.length; i++) {
      const rule = filterRules.value[i]
      if (!rule.factor) {
        errs.push(`过滤模块：规则 ${i + 1} 未选择因子`)
      }
      if (rule.compare_to !== undefined && rule.compare_to === '') {
        errs.push(`过滤模块：规则 ${i + 1} 已切换为因子比较模式，但未选择比较因子`)
      }
    }
  }
  return errs
})

// ── 构建并输出 config_json ────────────────────────────────────────
function buildConfig(): Record<string, unknown> {
  const config: Record<string, unknown> = {}

  // 资产范围
  const codes = indexCodesInput.value
    .split(',')
    .map(s => s.trim())
    .filter(Boolean)
  if (codes.length > 0) {
    config.index_codes = codes
  }

  // 评分
  const factors: Record<string, number> = {}
  const transforms: Record<string, string> = {}
  for (const f of scoreFactors.value) {
    if (!f.factor_id) continue
    factors[f.factor_id] = f.weight
    if (f.transform) transforms[f.factor_id] = f.transform
  }
  const scoreConfig: Record<string, unknown> = { factors }
  if (Object.keys(transforms).length > 0) scoreConfig.transforms = transforms
  if (scoreMissingStrategy.value !== 'ignore') scoreConfig.missing_factor_strategy = scoreMissingStrategy.value
  if (scoreScoringMode.value !== 'absolute') scoreConfig.scoring_mode = scoreScoringMode.value
  config.score = scoreConfig

  // 择时
  if (timingEnabled.value) {
    const tFactors: Record<string, number> = {}
    const tTransforms: Record<string, string> = {}
    for (const f of timingFactors.value) {
      if (!f.factor_id) continue
      tFactors[f.factor_id] = f.weight
      if (f.transform) tTransforms[f.factor_id] = f.transform
    }
    const timingConfig: Record<string, unknown> = { factors: tFactors }
    if (Object.keys(tTransforms).length > 0) timingConfig.transforms = tTransforms
    timingConfig.thresholds = {
      offensive: timingOffensive.value,
      defensive: timingDefensive.value,
    }
    config.timing = timingConfig
  }

  // 过滤
  if (filterEnabled.value && filterRules.value.length > 0) {
    config.filters = {
      logic: filterLogic.value,
      rules: filterRules.value.filter(r => r.factor).map(r => {
        const rule: Record<string, unknown> = { factor: r.factor, op: r.op }
        if (r.compare_to) {
          rule.compare_to = r.compare_to
        } else {
          rule.value = r.value
        }
        return rule
      }),
    }
  }

  // 排名
  const rank: Record<string, unknown> = {
    sort_by: rankSortBy.value,
    order: rankOrder.value,
  }
  if (rankTopN.value !== '') rank.top_n = parseInt(rankTopN.value, 10)
  if (rankBottomN.value !== '') rank.bottom_n = parseInt(rankBottomN.value, 10)
  config.rank = rank

  // 组合
  if (portfolioEnabled.value) {
    const portfolio: Record<string, unknown> = { method: portfolioMethod.value }
    portfolio.timing_exposure = {
      offensive: exposureOffensive.value / 100,
      neutral: exposureNeutral.value / 100,
      defensive: exposureDefensive.value / 100,
    }
    if (portfolioDefaultExposure.value !== 50) portfolio.default_exposure = portfolioDefaultExposure.value / 100
    config.portfolio = portfolio
  }

  // 风控
  if (riskEnabled.value) {
    config.risk = {
      max_asset_weight: riskMaxAssetWeight.value / 100,
      max_portfolio_exposure: riskMaxPortfolioExposure.value / 100,
      min_cash_ratio: riskMinCashRatio.value / 100,
    }
  }

  // 调仓
  if (rebalanceEnabled.value) {
    const rebalance: Record<string, unknown> = { frequency: rebalanceFrequency.value }
    if (rebalanceFrequency.value === 'weekly' && rebalanceDayOfWeek.value != null) {
      rebalance.day_of_week = rebalanceDayOfWeek.value
    }
    if (rebalanceFrequency.value === 'monthly' && rebalanceDayOfMonth.value != null) {
      rebalance.day_of_month = rebalanceDayOfMonth.value
    }
    config.rebalance = rebalance
  }

  return config
}

function emitConfig(): void {
  selfUpdating = true
  emit('update:modelValue', buildConfig())
  // 下一个 tick 重置标志，允许外部更新触发重新初始化
  Promise.resolve().then(() => { selfUpdating = false })
}

// ── 监听所有表单变化，实时 emit ────────────────────────────────────
watch(
  [
    indexCodesInput,
    scoreFactors, scoreMissingStrategy, scoreScoringMode,
    timingEnabled, timingFactors, timingOffensive, timingDefensive,
    filterEnabled, filterLogic, filterRules,
    rankSortBy, rankOrder, rankTopN, rankBottomN,
    portfolioEnabled, portfolioMethod, portfolioDefaultExposure, exposureOffensive, exposureNeutral, exposureDefensive,
    riskEnabled, riskMaxAssetWeight, riskMaxPortfolioExposure, riskMinCashRatio,
    rebalanceEnabled, rebalanceFrequency, rebalanceDayOfWeek, rebalanceDayOfMonth,
  ],
  () => emitConfig(),
  { deep: true },
)

// ── 初始化：从 modelValue 解析各模块 ──────────────────────────────
onMounted(() => {
  initScope()
  initScore()
  initTiming()
  initFilter()
  initRank()
  initPortfolio()
  initRisk()
  initRebalance()
})

// 外部 modelValue 变化时重新初始化（排除自身 emit 导致的循环更新）
let selfUpdating = false
watch(() => props.modelValue, () => {
  if (selfUpdating) return
  initScope()
  initScore()
  initTiming()
  initFilter()
  initRank()
  initPortfolio()
  initRisk()
  initRebalance()
}, { deep: true })
</script>

<style scoped>
.config-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 校验提示 */
.validation-box {
  background: rgba(239,68,68,0.08);
  border: 1px solid rgba(239,68,68,0.25);
  border-radius: var(--radius-sm);
  padding: 10px 14px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.validation-item { font-size: 12px; color: #f87171; }

/* 模块卡片 */
.module-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.module-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
  transition: background 0.1s;
}
.module-header:hover { background: var(--surface-2); }

.module-title { font-size: 13px; font-weight: 600; flex: 1; }

.module-badge {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}
.module-badge.required { background: rgba(59,130,246,0.15); color: #60a5fa; }
.module-badge.optional { background: var(--surface-2); color: var(--text-muted); }
.module-badge.always { background: rgba(34,197,94,0.12); color: #4ade80; }

.arrow {
  font-size: 12px;
  color: var(--text-muted);
  transition: transform 0.2s;
}
.arrow.open { transform: rotate(180deg); }

/* 开关 */
.toggle-switch { position: relative; display: inline-flex; cursor: pointer; }
.toggle-switch input { position: absolute; opacity: 0; width: 0; height: 0; }
.toggle-track {
  width: 36px;
  height: 20px;
  background: var(--surface-2);
  border-radius: 10px;
  position: relative;
  transition: background 0.2s;
}
.toggle-track::after {
  content: '';
  position: absolute;
  width: 16px;
  height: 16px;
  background: var(--text-muted);
  border-radius: 50%;
  top: 2px;
  left: 2px;
  transition: all 0.2s;
}
.toggle-switch input:checked + .toggle-track { background: var(--accent); }
.toggle-switch input:checked + .toggle-track::after { left: 18px; background: #fff; }

/* 模块内容 */
.module-body {
  padding: 0 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.module-desc {
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.6;
  padding-bottom: 4px;
}

/* 因子列表表头 */
.factor-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 0 4px;
  font-size: 11px;
  color: var(--text-muted);
  font-weight: 500;
}
.fh-factor { flex: 2; }
.fh-weight { width: 72px; text-align: center; }
.fh-transform { flex: 1.2; }
.fh-action { width: 28px; }

/* 添加按钮 */
.add-btn {
  align-self: flex-start;
  padding: 4px 12px;
  background: transparent;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.add-btn:hover { border-color: var(--accent); color: var(--accent); }

/* 子字段 */
.sub-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 4px;
}
.sub-label { font-size: 12px; font-weight: 500; color: var(--text-muted); }

.radio-row { display: flex; gap: 16px; flex-wrap: wrap; }
.radio-opt {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-muted);
  cursor: pointer;
}
.radio-opt input { accent-color: var(--accent); }

/* 输入框 */
.fp-select, .fp-input {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 12px;
  color: var(--text);
  outline: none;
  transition: border-color 0.15s;
}
.fp-select:focus, .fp-input:focus { border-color: var(--accent); }

.fp-remove {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 16px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}
.fp-remove:hover { background: rgba(239,68,68,0.15); color: #f87171; border-color: rgba(239,68,68,0.3); }

/* 阈值行 */
.threshold-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.threshold-field { display: flex; flex-direction: column; gap: 6px; }
.threshold-input-wrap { display: flex; flex-direction: column; gap: 4px; }
.threshold-hint { font-size: 11px; color: var(--text-muted); }

/* 过滤规则行 */
.filter-rule-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.fr-factor { flex: 1.5; }
.fr-op { flex: 1; }
.fr-mode { flex: 0.8; }
.fr-value { width: 90px; }
.fr-sep { color: var(--text-muted); font-size: 12px; }

/* 排名网格 */
.rank-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

/* 择时仓位 */
.exposure-row { display: flex; gap: 12px; }
.exposure-field {
  display: flex;
  align-items: center;
  gap: 6px;
}
.exposure-label { font-size: 12px; color: var(--text-muted); min-width: 32px; }
.exposure-input { width: 64px; text-align: center; }
.exposure-unit { font-size: 12px; color: var(--text-muted); }

/* 风控网格 */
.risk-grid { display: flex; flex-direction: column; gap: 16px; }
.slider-row { display: flex; align-items: center; gap: 10px; }
.slider {
  flex: 1;
  height: 4px;
  appearance: none;
  background: var(--surface-2);
  border-radius: 2px;
  outline: none;
}
.slider::-webkit-slider-thumb {
  appearance: none;
  width: 16px;
  height: 16px;
  background: var(--accent);
  border-radius: 50%;
  cursor: pointer;
}
.slider-value { width: 56px; text-align: center; }

/* 复选框标签 */
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-muted);
  cursor: pointer;
}
.checkbox-label input { accent-color: var(--accent); }

</style>
