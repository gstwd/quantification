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
        <div class="module-desc">
          指定策略运行的目标指数（只选择可在市场上找到实际 ETF 对应的指数）。
          不选则自动使用全部可用指数。
        </div>
        <div class="sub-field">
          <label class="sub-label">选择指数</label>
          <div class="index-checkboxes">
            <label
              v-for="idx in scopedAssets"
              :key="idx.code"
              class="checkbox-label"
            >
              <input
                type="checkbox"
                :checked="selectedIndexCodes.includes(idx.code)"
                @change="toggleIndexCode(idx.code)"
              />
              <span class="index-code">{{ idx.code }}</span>
              <span class="index-name">{{ idx.name }}</span>
            </label>
          </div>
          <div v-if="selectedIndexCodes.length > 0" class="index-summary">
            已选 <strong>{{ selectedIndexCodes.length }}</strong> 个：
            <span class="index-codes">{{ selectedIndexCodes.join(', ') }}</span>
          </div>
          <div v-else class="index-summary empty">
            未选择（使用全部可用指数）
          </div>
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
          <span class="fh-factor">模板 / 别名 / 参数</span>
          <span class="fh-weight">权重</span>
          <span class="fh-transform">变换函数</span>
          <span class="fh-action"></span>
        </div>

        <FactorPicker
          v-for="(row, i) in scoreFactors"
          :key="i"
          :factors="scoreModuleFactors"
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
              <input type="radio" v-model="scoreMissingStrategy" value="zero" /> 按最差处理（该因子记最低分）
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

        <div class="sub-field">
          <label class="sub-label">综合方式</label>
          <div class="radio-row">
            <label class="radio-opt">
              <input type="radio" v-model="timingCombine" value="weighted" /> 加权综合分（过阈值）
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="timingCombine" value="rules" /> 规则模式（进攻 / 防守规则组）
            </label>
          </div>
        </div>
        <div v-if="timingCombine === 'weighted'" class="threshold-hint">
          综合分在 0-100 空间加权：除 RSI、市场宽度、估值百分位等原值即 0-100 的因子外，
          收益率、RSRS、量比等因子必须选择变换函数，否则校验不通过。
        </div>
        <div v-else class="threshold-hint">
          规则模式：防守组满足 → 防守；否则进攻组满足 → 进攻；都不满足 → 中性（无信号，可配合“无信号维持前值”）。
          启用多档仓位时改为按档位顺序匹配。规则比较因子原值；下方因子列表只用于声明择时因子（别名 / 模板 / 参数），
          权重与变换不生效。
        </div>

        <div class="factor-header">
          <span class="fh-factor">模板 / 别名 / 参数</span>
          <span class="fh-weight">权重</span>
          <span class="fh-transform">变换函数</span>
          <span class="fh-action"></span>
        </div>

        <FactorPicker
          v-for="(row, i) in timingFactors"
          :key="i"
          :factors="timingModuleFactors"
          :model-value="row"
          @update:model-value="updateTimingFactor(i, $event)"
          @remove="removeTimingFactor(i)"
        />

        <button class="add-btn" @click="addTimingFactor">+ 添加因子</button>

        <div class="sub-field">
          <label class="radio-opt">
            <input type="checkbox" v-model="timingTiersEnabled" @change="onTimingTiersToggle" />
            多档仓位（按顺序首个命中的档位决定总仓位，如 0 / 25 / 50 / 75 / 100%）
          </label>
          <span class="threshold-hint">
            <template v-if="timingCombine === 'weighted'">
              加权模式：综合分 ≥ 档位下限即命中（下限需从高到低排列），regime 仍按下方阈值判定；
              低于全部下限时按组合模块的 regime 仓位。
            </template>
            <template v-else>
              规则模式：每个档位一组规则，按顺序首个满足的档位决定 regime 与总仓位（取代进攻 / 防守规则组）；
              都不满足为中性，按组合模块的中性仓位。
            </template>
            状态层沿用前一 regime 时同时沿用上一档位；命中档位时组合模块的 regime 仓位不生效。
          </span>
        </div>

        <div v-if="timingCombine === 'weighted' && timingTiersEnabled" class="sub-field">
          <div v-for="(tier, i) in timingTiers" :key="i" class="filter-rule-row">
            <span class="exposure-label">档位 {{ i + 1 }}</span>
            <span class="threshold-hint">综合分 ≥</span>
            <input
              v-model.number="tier.min_score"
              type="number"
              min="0"
              max="100"
              step="any"
              class="fp-input fr-value"
              placeholder="下限"
            />
            <span class="threshold-hint">仓位</span>
            <select v-model="tier.mode" class="fp-select fr-mode" title="仓位方式">
              <option value="fixed">固定仓位</option>
              <option value="hold">维持上一仓位</option>
              <option value="delta">相对增减</option>
            </select>
            <template v-if="tier.mode === 'fixed'">
              <input
                v-model.number="tier.exposure"
                type="number"
                min="0"
                max="100"
                step="5"
                class="fp-input exposure-input"
              />
              <span class="exposure-unit">%</span>
            </template>
            <template v-else-if="tier.mode === 'delta'">
              <input
                v-model.number="tier.delta"
                type="number"
                min="-100"
                max="100"
                step="5"
                class="fp-input exposure-input"
                title="在上一仓位上增减（如 +25 表示加一档）"
              />
              <span class="exposure-unit">%，夹在</span>
              <input v-model.number="tier.min_exposure" type="number" min="0" max="100" step="5" class="fp-input exposure-input" />
              <span class="fr-sep">~</span>
              <input v-model.number="tier.max_exposure" type="number" min="0" max="100" step="5" class="fp-input exposure-input" />
              <span class="exposure-unit">%</span>
            </template>
            <input v-model="tier.label" class="fp-input fr-factor" maxlength="40" placeholder="档位名称（可选）" />
            <button class="fp-remove" @click="removeTimingTier(i)" title="移除">×</button>
          </div>
          <button class="add-btn" @click="addTimingTier">+ 添加档位</button>
        </div>

        <template v-if="timingCombine === 'rules'">
          <div v-for="block in timingRuleBlocks" :key="block.key" class="sub-field">
            <label class="sub-label">{{ block.label }}</label>
            <div v-if="block.tierIndex !== null" class="filter-rule-row">
              <input
                v-model="timingTiers[block.tierIndex].label"
                class="fp-input fr-factor"
                maxlength="40"
                placeholder="档位名称（可选）"
              />
              <select v-model="timingTiers[block.tierIndex].regime" class="fp-select fr-op" title="命中该档时的 regime">
                <option value="offensive">进攻</option>
                <option value="neutral">中性</option>
                <option value="defensive">防守</option>
              </select>
              <span class="threshold-hint">仓位</span>
              <select v-model="timingTiers[block.tierIndex].mode" class="fp-select fr-mode" title="仓位方式">
              <option value="fixed">固定仓位</option>
              <option value="hold">维持上一仓位</option>
              <option value="delta">相对增减</option>
            </select>
            <template v-if="timingTiers[block.tierIndex].mode === 'fixed'">
              <input
                v-model.number="timingTiers[block.tierIndex].exposure"
                type="number"
                min="0"
                max="100"
                step="5"
                class="fp-input exposure-input"
              />
              <span class="exposure-unit">%</span>
            </template>
            <template v-else-if="timingTiers[block.tierIndex].mode === 'delta'">
              <input
                v-model.number="timingTiers[block.tierIndex].delta"
                type="number"
                min="-100"
                max="100"
                step="5"
                class="fp-input exposure-input"
                title="在上一仓位上增减（如 +25 表示加一档）"
              />
              <span class="exposure-unit">%，夹在</span>
              <input v-model.number="timingTiers[block.tierIndex].min_exposure" type="number" min="0" max="100" step="5" class="fp-input exposure-input" />
              <span class="fr-sep">~</span>
              <input v-model.number="timingTiers[block.tierIndex].max_exposure" type="number" min="0" max="100" step="5" class="fp-input exposure-input" />
              <span class="exposure-unit">%</span>
            </template>
              <button
                class="fp-remove"
                :disabled="block.tierIndex === 0"
                @click="moveTimingTier(block.tierIndex, -1)"
                title="上移（顺序即优先级）"
              >↑</button>
              <button class="fp-remove" @click="removeTimingTier(block.tierIndex)" title="移除档位">×</button>
            </div>
            <div class="radio-row">
              <label class="radio-opt">
                <input type="radio" v-model="block.group.logic" value="AND" @change="emitConfig" /> AND（全部满足）
              </label>
              <label class="radio-opt">
                <input type="radio" v-model="block.group.logic" value="OR" @change="emitConfig" /> OR（任一满足）
              </label>
            </div>
            <div v-for="(rule, i) in block.group.rules" :key="i" class="filter-rule-row">
              <select
                :value="rule.factor"
                class="fp-select fr-factor"
                @change="updateTimingRule(block.group, i, 'factor', ($event.target as HTMLSelectElement).value)"
              >
                <option value="" disabled>选择因子</option>
                <option v-for="opt in timingRuleFactorOptions" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
              </select>
              <select
                :value="rule.op"
                class="fp-select fr-op"
                @change="updateTimingRule(block.group, i, 'op', ($event.target as HTMLSelectElement).value)"
              >
                <option value="gt">大于 (>)</option>
                <option value="lt">小于 (<)</option>
                <option value="gte">大于等于 (≥)</option>
                <option value="lte">小于等于 (≤)</option>
                <option value="eq">等于 (=)</option>
                <option value="neq">不等于 (≠)</option>
                <option value="between">区间 (between)</option>
              </select>
              <select
                :value="rule.missing_strategy ?? 'fail'"
                class="fp-select fr-missing"
                title="因子值缺失时的处理方式"
                @change="updateTimingRule(block.group, i, 'missing_strategy', ($event.target as HTMLSelectElement).value)"
              >
                <option value="fail">缺失:不满足</option>
                <option value="pass">缺失:满足</option>
              </select>
              <template v-if="rule.op === 'between'">
                <input
                  :value="Array.isArray(rule.value) ? rule.value[0] : ''"
                  type="number"
                  step="any"
                  class="fp-input fr-value"
                  placeholder="最小值"
                  @input="updateTimingRuleBetween(block.group, i, 0, ($event.target as HTMLInputElement).value)"
                />
                <span class="fr-sep">~</span>
                <input
                  :value="Array.isArray(rule.value) ? rule.value[1] : ''"
                  type="number"
                  step="any"
                  class="fp-input fr-value"
                  placeholder="最大值"
                  @input="updateTimingRuleBetween(block.group, i, 1, ($event.target as HTMLInputElement).value)"
                />
              </template>
              <template v-else>
                <select
                  :value="rule.compare_to !== undefined ? 'compare' : 'value'"
                  class="fp-select fr-mode"
                  @change="onTimingRuleModeChange(block.group, i, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="value">固定值</option>
                  <option value="compare">因子比较</option>
                </select>
                <select
                  v-if="rule.compare_to !== undefined"
                  :value="rule.compare_to"
                  class="fp-select fr-factor"
                  @change="updateTimingRule(block.group, i, 'compare_to', ($event.target as HTMLSelectElement).value)"
                >
                  <option value="" disabled>选择比较因子</option>
                  <option v-for="opt in timingRuleFactorOptions" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                  </option>
                </select>
                <input
                  v-else
                  :value="typeof rule.value === 'number' ? rule.value : ''"
                  type="number"
                  step="any"
                  class="fp-input fr-value"
                  placeholder="值"
                  @input="updateTimingRule(block.group, i, 'value', parseFloat(($event.target as HTMLInputElement).value) || 0)"
                />
              </template>
              <button class="fp-remove" @click="removeTimingRule(block.group, i)" title="移除">×</button>
            </div>
            <span v-if="block.group.groups.length > 0" class="threshold-hint">
              另含 {{ block.group.groups.length }} 个嵌套子规则组：表单不支持编辑，保存时原样保留（用 CLI 或配置 JSON 修改）
            </span>
            <button class="add-btn" @click="addTimingRule(block.group)">+ 添加{{ block.short }}规则</button>
          </div>
          <button v-if="timingTiersEnabled" class="add-btn" @click="addTimingTier">+ 添加档位</button>
        </template>

        <div v-if="timingTiersEnabled && hasDynamicTier" class="sub-field">
          <label class="sub-label">动态档位起点仓位</label>
          <div class="threshold-input-wrap">
            <input v-model.number="timingTierBase" type="number" min="0" max="100" step="5" class="fp-input exposure-input" />
            <span class="exposure-unit">%</span>
            <span class="threshold-hint">“维持 / 相对增减”档位在没有上一仓位（首日、上一日未命中档位）时以此为起点</span>
          </div>
        </div>

        <div v-if="timingCombine === 'weighted'" class="threshold-row">
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
        <div class="sub-field">
          <label class="sub-label">代理指数代码（逗号分隔）</label>
          <input
            v-model="timingProxyIndexCodes"
            class="fp-input"
            placeholder="如 000300,000016，留空=默认沪深300"
          />
          <span class="threshold-hint">
            用于加载市场级择时因子的代表性指数：按顺序选第一个让全部择时因子都有值的代理；
            都不能全覆盖时逐因子取第一个有值的代理（择时明细会标出实际来源）
          </span>
        </div>
        <div class="sub-field">
          <label class="sub-label">按因子指定代理（可选）</label>
          <input
            v-model="timingFactorProxiesText"
            class="fp-input"
            placeholder="如 pv_res:000852; trend:000300,000905（别名:代码,代码；多条用分号分隔）"
          />
          <span class="threshold-hint">
            未列出的因子使用上面的代理指数；列出的因子按自己的代理列表顺序取值，例如价量共振看中证1000、动量看沪深300
          </span>
        </div>
        <div class="sub-field">
          <label class="sub-label">状态层（抑制来回翻转，缺省不启用）</label>
          <div class="threshold-row">
            <div v-if="timingCombine === 'weighted'" class="threshold-field">
              <label class="sub-label">滞回带（分）</label>
              <div class="threshold-input-wrap">
                <input v-model.number="timingHysteresis" type="number" min="0" max="50" step="any" class="fp-input" />
                <span class="threshold-hint">已在进攻时综合分不低于“进攻阈值 − 滞回带”就保持进攻，防守对称</span>
              </div>
            </div>
            <div class="threshold-field">
              <label class="sub-label">确认天数</label>
              <div class="threshold-input-wrap">
                <input v-model.number="timingConfirmDays" type="number" min="1" max="60" class="fp-input" />
                <span class="threshold-hint">新 regime 需连续出现的交易日数，1=当日切换</span>
              </div>
            </div>
            <div class="threshold-field">
              <label class="sub-label">最短持有天数</label>
              <div class="threshold-input-wrap">
                <input v-model.number="timingMinHoldDays" type="number" min="0" max="250" class="fp-input" />
                <span class="threshold-hint">切换后至少持有的交易日数，0=不限制</span>
              </div>
            </div>
            <div class="threshold-field">
              <label class="sub-label">单次仓位变动上限（%）</label>
              <div class="threshold-input-wrap">
                <input v-model.number="timingMaxStep" type="number" min="0" max="100" step="5" class="fp-input" />
                <span class="threshold-hint">档位决定的仓位每个决策日最多变动多少（如 25=每次最多一档），0=不限制</span>
              </div>
            </div>
          </div>
          <label class="radio-opt">
            <input type="checkbox" v-model="timingHoldOnNeutral" /> 无信号（中性）时维持前一 regime
          </label>
          <span class="threshold-hint">
            实时分配会回放最近 250 个交易日重建状态，与回测同一实现
          </span>
        </div>
        <div class="sub-field">
          <label class="sub-label">条件仓位上限（可选）</label>
          <span class="threshold-hint">
            规则组满足时封顶总仓位（如“估值分位 ≥ 80 时不超过 50%”），多条命中取最小值；规则比较择时代理上的因子原值，
            因子需先在上方因子列表中声明（加权模式下权重填 0 即只声明不参与综合分）
          </span>
          <div v-for="(cap, i) in timingCaps" :key="i" class="cap-block">
            <div class="filter-rule-row">
              <input v-model="cap.label" class="fp-input fr-factor" maxlength="40" placeholder="上限名称（可选）" />
              <span class="threshold-hint">命中时总仓位不超过</span>
              <input v-model.number="cap.max_exposure" type="number" min="0" max="100" step="5" class="fp-input exposure-input" />
              <span class="exposure-unit">%</span>
              <button class="fp-remove" title="移除上限" @click="timingCaps.splice(i, 1)">×</button>
            </div>
            <RuleGroupEditor
              :model-value="cap.group"
              :factor-options="timingRuleFactorOptions"
              @update:model-value="cap.group = $event"
            />
          </div>
          <button class="add-btn" @click="addTimingCap">+ 添加仓位上限</button>
        </div>
        <div class="sub-field">
          <label class="sub-label">择时因子全部缺失时</label>
          <select v-model="timingOnDegraded" class="fp-select">
            <option value="neutral">按中性（默认）</option>
            <option value="defensive">按防守</option>
            <option value="hold_previous">沿用上一决策日</option>
          </select>
          <span class="threshold-hint">
            中性档仓位较高时（如 90%），缺数据会接近满仓运行，可改为按防守。“沿用上一决策日”在实时分配中
            由回放最近交易日得到；回放不到历史时退化为中性并给出告警
          </span>
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
        <span v-if="filterGroups.length > 0" class="threshold-hint">
          另含 {{ filterGroups.length }} 个嵌套子规则组：表单不支持编辑，保存时原样保留（用 CLI 或配置 JSON 修改）
        </span>

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
            <option v-for="opt in filterFactorOptions" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
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

          <select
            :value="rule.missing_strategy ?? 'fail'"
            class="fp-select fr-missing"
            title="因子值缺失时的处理方式"
            @change="updateFilterRule(i, 'missing_strategy', ($event.target as HTMLSelectElement).value)"
          >
            <option value="fail">缺失:不满足</option>
            <option value="pass">缺失:通过</option>
            <option value="exclude">缺失:排除</option>
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
                <option v-for="opt in filterFactorOptions" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
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
            <span class="threshold-hint">固定按综合得分排序；如需按某个因子排序，请把该因子加入评分模块。</span>
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
          <div class="sub-field">
            <label class="sub-label">排名缓冲带（可选，需 Top N）</label>
            <input
              v-model="rankBufferN"
              type="number"
              min="1"
              max="50"
              class="fp-input"
              placeholder="留空=不启用"
              :disabled="rankTopN === ''"
            />
            <span class="threshold-hint">
              已持有资产排名仍在 Top N + 缓冲名次内时继续保留，减少名次边缘的来回换仓；实时决策会回放历史重建持仓。
            </span>
          </div>
          <div class="sub-field">
            <label class="sub-label">动量子排名因子（仅展示，可选）</label>
            <select v-model="rankMomentumFactor" class="fp-select">
              <option value="">不展示</option>
              <option v-for="alias in declaredAliases" :key="alias" :value="alias">
                {{ alias }}
              </option>
            </select>
          </div>
          <div class="sub-field">
            <label class="sub-label">估值子排名因子（仅展示，可选，数值越低越靠前）</label>
            <select v-model="rankValuationFactor" class="fp-select">
              <option value="">不展示</option>
              <option v-for="alias in declaredAliases" :key="alias" :value="alias">
                {{ alias }}
              </option>
            </select>
          </div>
        </div>
      </div>
    </div>

    <!-- ═══ 组合模块 ═══ -->
    <div class="module-card">
      <div class="module-header" @click="toggleModule('portfolio')">
        <span class="module-title">组合模块 (Portfolio) <HelpTip :text="scHelp('portfolio')" /></span>
        <span :class="['arrow', expanded.portfolio ? 'open' : '']">▾</span>
      </div>
      <div v-show="expanded.portfolio" class="module-body">
        <div class="module-desc">配置权重分配方法，决定各资产的目标仓位比例。</div>

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
            <label class="radio-opt">
              <input type="radio" v-model="portfolioMethod" value="inverse_vol" /> 波动率倒数加权
            </label>
            <label class="radio-opt">
              <input type="radio" v-model="portfolioMethod" value="risk_parity" /> 风险平价（等风险贡献）
            </label>
          </div>
        </div>

        <div v-if="portfolioMethod === 'inverse_vol'" class="sub-field">
          <label class="sub-label">波动率因子（权重 ∝ 1 / 因子值）</label>
          <select v-model="portfolioVolFactor" class="fp-select">
            <option value="">请选择已声明的因子别名</option>
            <option v-for="alias in declaredAliases" :key="alias" :value="alias">{{ alias }}</option>
          </select>
          <span class="threshold-hint">
            一般选 volatility / return_std 的别名；该因子缺失的资产在评分前剔除。
          </span>
        </div>
        <div v-if="portfolioMethod === 'risk_parity'" class="sub-field">
          <label class="sub-label">协方差窗口（交易日）</label>
          <input v-model.number="portfolioCovWindow" type="number" min="20" max="250" class="fp-input" />
          <span class="threshold-hint">
            按近 N 个交易日日收益的协方差求等风险贡献权重；入选资产共同有效天数不足一半时退回等权。
          </span>
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
          <span v-if="timingExposureMode === 'absent'" class="threshold-hint">
            原配置未声明 regime 仓位，当前为引擎默认值（进攻 80% / 中性 50% / 防守 20%）；不修改则保存时保持省略。
          </span>
          <span v-if="timingEnabled && timingTiersEnabled" class="threshold-hint">
            已启用择时多档仓位：命中档位时取档位仓位，以下 regime 仓位只在未命中档位（或择时降级）时生效。
          </span>
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
                @input="markExposureEdited('offensive')"
              />
              <span class="exposure-unit">%</span>
              <span v-if="timingExposureMode === 'explicit' && exposureInherited.offensive" class="inherit-tag" title="原配置未声明该 regime，引擎使用默认仓位；修改后才会写入配置">继承默认</span>
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
                @input="markExposureEdited('neutral')"
              />
              <span class="exposure-unit">%</span>
              <span v-if="timingExposureMode === 'explicit' && exposureInherited.neutral" class="inherit-tag" title="原配置未声明该 regime，引擎使用默认仓位；修改后才会写入配置">继承默认</span>
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
                @input="markExposureEdited('defensive')"
              />
              <span class="exposure-unit">%</span>
              <span v-if="timingExposureMode === 'explicit' && exposureInherited.defensive" class="inherit-tag" title="原配置未声明该 regime，引擎使用默认仓位；修改后才会写入配置">继承默认</span>
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

        <div class="module-desc">
          路径依赖风控（可选）：在每日合成目标后施加，只降仓不加仓；止损触发后次日即成交，不等调仓日。
          实时决策会回放历史重建净值与持仓状态。
        </div>
        <div class="sub-field">
          <label class="sub-label">
            <input type="checkbox" v-model="volTargetEnabled" />
            波动率目标
          </label>
          <div v-if="volTargetEnabled" class="exposure-row">
            <div class="exposure-field">
              <span class="exposure-label">目标年化波动</span>
              <input v-model.number="volTarget" type="number" min="1" max="100" class="fp-input exposure-input" />
              <span class="exposure-unit">%</span>
            </div>
            <div class="exposure-field">
              <span class="exposure-label">窗口</span>
              <input v-model.number="volTargetWindow" type="number" min="20" max="250" class="fp-input exposure-input" />
              <span class="exposure-unit">日</span>
            </div>
            <div class="exposure-field">
              <span class="exposure-label">仓位下限</span>
              <input v-model.number="volTargetFloor" type="number" min="0" max="100" class="fp-input exposure-input" />
              <span class="exposure-unit">%</span>
            </div>
          </div>
        </div>
        <div class="sub-field">
          <label class="sub-label">
            <input type="checkbox" v-model="drawdownStopEnabled" />
            组合回撤止损
          </label>
          <div v-if="drawdownStopEnabled" class="exposure-row">
            <div class="exposure-field">
              <span class="exposure-label">触发回撤</span>
              <input v-model.number="drawdownThreshold" type="number" min="1" max="90" class="fp-input exposure-input" />
              <span class="exposure-unit">%</span>
            </div>
            <div class="exposure-field">
              <span class="exposure-label">止损仓位</span>
              <input v-model.number="drawdownExposure" type="number" min="0" max="100" class="fp-input exposure-input" />
              <span class="exposure-unit">%</span>
            </div>
            <div class="exposure-field">
              <span class="exposure-label">冷却</span>
              <input v-model.number="drawdownCooldown" type="number" min="1" max="250" class="fp-input exposure-input" />
              <span class="exposure-unit">日</span>
            </div>
          </div>
        </div>
        <div class="sub-field">
          <label class="sub-label">
            <input type="checkbox" v-model="trailingStopEnabled" />
            单资产移动止损
          </label>
          <div v-if="trailingStopEnabled" class="exposure-row">
            <div class="exposure-field">
              <span class="exposure-label">自高点回撤</span>
              <input v-model.number="trailingThreshold" type="number" min="1" max="90" class="fp-input exposure-input" />
              <span class="exposure-unit">%</span>
            </div>
            <div class="exposure-field">
              <span class="exposure-label">冷却</span>
              <input v-model.number="trailingCooldown" type="number" min="1" max="250" class="fp-input exposure-input" />
              <span class="exposure-unit">日</span>
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
        <div class="module-desc">
          启用调仓模块时两条腿同时生效：<b>选股腿</b>重建成分，<b>风险腿</b>只把现有成分等比缩放到择时目标仓位。
          两腿日程一致时等价于"每个调仓日重建成分 + 应用择时仓位"（与旧配置行为一致）。
        </div>

        <div class="sub-field">
          <label class="sub-label">选股腿频率</label>
          <select v-model="selectionFrequency" class="fp-select">
            <option value="daily">每日</option>
            <option value="weekly">每周</option>
            <option value="biweekly">双周</option>
            <option value="monthly">每月</option>
          </select>
        </div>

        <div v-if="selectionFrequency === 'weekly' || selectionFrequency === 'biweekly'" class="sub-field">
          <label class="sub-label">选股腿周调仓日</label>
          <select v-model.number="selectionDayOfWeek" class="fp-select">
            <option :value="0">周一</option>
            <option :value="1">周二</option>
            <option :value="2">周三</option>
            <option :value="3">周四</option>
            <option :value="4">周五</option>
          </select>
        </div>

        <div v-if="selectionFrequency === 'biweekly'" class="sub-field">
          <label class="sub-label">选股腿周次</label>
          <select v-model="selectionWeekParity" class="fp-select">
            <option value="odd">奇数周（以 2016-01-04 所在周为第 1 周）</option>
            <option value="even">偶数周（以 2016-01-04 所在周为第 1 周）</option>
          </select>
          <span class="threshold-hint">双周必须指定奇/偶周：以 2016-01-04 所在周为第 1 周连续计数，奇偶决定哪一周调仓（不受跨年第 53 周影响）。</span>
        </div>

        <div v-if="selectionFrequency === 'monthly'" class="sub-field">
          <label class="sub-label">选股腿月调仓日</label>
          <input
            v-model.number="selectionDayOfMonth"
            type="number"
            min="1"
            max="28"
            class="fp-input"
            placeholder="1-28"
          />
          <span class="threshold-hint">每月第几日调仓（1-28；遇非交易日顺延至其后第一个交易日，日期超出当月天数时按当月最后一日处理）</span>
        </div>

        <div class="sub-field">
          <label class="sub-label">
            <input type="checkbox" v-model="riskSameAsSelection" />
            风险腿与选股腿同频
          </label>
          <span class="threshold-hint">勾选时两腿日程一致（推荐，等价改造前的单腿口径）；取消勾选可让仓位与成分按不同频率调整。</span>
        </div>

        <template v-if="!riskSameAsSelection">
          <div class="sub-field">
            <label class="sub-label">风险腿频率</label>
            <select v-model="riskFrequency" class="fp-select">
              <option value="daily">每日</option>
              <option value="weekly">每周</option>
              <option value="biweekly">双周</option>
              <option value="monthly">每月</option>
            </select>
          </div>
          <div v-if="riskFrequency === 'weekly' || riskFrequency === 'biweekly'" class="sub-field">
            <label class="sub-label">风险腿周调仓日</label>
            <select v-model.number="riskDayOfWeek" class="fp-select">
              <option :value="0">周一</option>
              <option :value="1">周二</option>
              <option :value="2">周三</option>
              <option :value="3">周四</option>
              <option :value="4">周五</option>
            </select>
          </div>
          <div v-if="riskFrequency === 'biweekly'" class="sub-field">
            <label class="sub-label">风险腿周次</label>
            <select v-model="riskWeekParity" class="fp-select">
              <option value="odd">奇数周（以 2016-01-04 所在周为第 1 周）</option>
              <option value="even">偶数周（以 2016-01-04 所在周为第 1 周）</option>
            </select>
          </div>
          <div v-if="riskFrequency === 'monthly'" class="sub-field">
            <label class="sub-label">风险腿月调仓日</label>
            <input v-model.number="riskDayOfMonth" type="number" min="1" max="28" class="fp-input" placeholder="1-28" />
          </div>
          <div class="threshold-hint">
            提示：两腿日程不同时，选股腿日只替换成分、维持当前总仓位；总仓位仅在风险腿日按择时目标调整。两种情况都会按风控单资产上限裁剪。
          </div>
        </template>

        <div v-if="!timingEnabled" class="threshold-hint">
          提示：未启用择时模块时风险腿只会把总仓位缩放到组合模块的默认仓位（default_exposure），不会随市场状态变化。
        </div>

        <div class="sub-field">
          <label class="sub-label">偏离阈值调仓（可选）</label>
          <div class="exposure-row">
            <div class="exposure-field">
              <span class="exposure-label">最小换手</span>
              <input v-model="bandMinTurnover" type="number" min="0.5" max="100" step="0.5"
                class="fp-input exposure-input" placeholder="不启用" />
              <span class="exposure-unit">%</span>
            </div>
            <div class="exposure-field">
              <span class="exposure-label">最大偏离</span>
              <input v-model="bandMaxDrift" type="number" min="0.5" max="100" step="0.5"
                class="fp-input exposure-input" placeholder="不启用" />
              <span class="exposure-unit">%</span>
            </div>
          </div>
          <span class="threshold-hint">
            最小换手：调仓日所需单边换手低于该值则跳过本次调仓；最大偏离：非调仓日任一资产实际权重偏离最近目标超过该值时，按最近目标再平衡（不重新选股）。
          </span>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup lang="ts">
/**
 * 策略配置表单组件。
 *
 * 将 config_json 拆分为通用模块卡片进行结构化编辑（评分/择时/过滤/
 * 排名/组合/风控/调仓），资产范围统一为 benchmark_index 指数；
 * 行业与个股只作为因子输入，策略资产统一为 benchmark_index 指数。
 * 实时输出标准 config_json 对象。
 */

import { computed, onMounted, reactive, ref, watch } from 'vue'

import { fetchFactorSpecs } from '../api/factors'
import { fetchBenchmarkIndexes } from '../api/market_data'
import type {
  BenchmarkIndex,
  FactorParameterSpec,
  FactorParameterValue,
  FactorSpec,
  TimingCombineMode,
  TimingOnDegraded,
} from '../types/api'
import FactorPicker from './FactorPicker.vue'
import RuleGroupEditor from './RuleGroupEditor.vue'
import type { RuleGroupValue } from './RuleGroupEditor.vue'
import HelpTip from '../components/HelpTip.vue'
import { getIndicator } from '../utils/indicatorDescriptions'

/** 获取策略配置描述的快捷方法 */
function scHelp(key: string): string {
  return getIndicator('strategy_config', key)?.description ?? ''
}

/** 因子行数据：别名 + 模板 + 参数 + 权重 + 变换 */
interface FactorRowValue {
  alias: string
  template_id: string
  params: Record<string, FactorParameterValue>
  weight: number
  transform?: string
}

/** 过滤规则 */
interface FilterRuleValue {
  factor: string
  op: string
  value: number | number[]
  compare_to?: string
  missing_strategy?: string
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
/** 可用指数列表（用于资产范围多选） */
const availableIndexes = ref<BenchmarkIndex[]>([])

/** 资产范围选项（code/name），策略资产统一为指数目录 */
const scopedAssets = computed(() =>
  availableIndexes.value.map(i => ({ code: i.index_code, name: i.index_name })),
)

onMounted(async () => {
  try {
    availableFactors.value = await fetchFactorSpecs()
  } catch {
    availableFactors.value = []
  }
  try {
    availableIndexes.value = await fetchBenchmarkIndexes()
  } catch {
    availableIndexes.value = []
  }
  // 模板参数默认值来自 parameter_schema，必须等模板列表就绪后再解析配置
  reinitialize()
})

/** 因子是否可用于指定消费模块（usage 元数据过滤，未声明 usage 时按可用处理） */
function usableFor(f: FactorSpec, moduleKey: string): boolean {
  if (!f.is_active) return false
  return !f.usage || f.usage.length === 0 || f.usage.includes(moduleKey)
}

/** 按模块过滤后的因子列表（评分/择时下拉） */
const scoreModuleFactors = computed(() =>
  availableFactors.value.filter(f => usableFor(f, 'score')),
)
const timingModuleFactors = computed(() =>
  availableFactors.value.filter(f => usableFor(f, 'timing')),
)

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

/** 当前已选的指数代码列表（从输入解析） */
const selectedIndexCodes = computed(() =>
  indexCodesInput.value
    .split(',')
    .map(s => s.trim())
    .filter(Boolean),
)

function toggleIndexCode(code: string): void {
  const current = new Set(selectedIndexCodes.value)
  if (current.has(code)) {
    current.delete(code)
  } else {
    current.add(code)
  }
  indexCodesInput.value = Array.from(current).join(', ')
}

function initScope(): void {
  const codes = props.modelValue.index_codes as string[] | undefined
  indexCodesInput.value = codes && codes.length > 0 ? codes.join(', ') : ''
}

// ── 评分模块 ──────────────────────────────────────────────────────
const scoreFactors = ref<FactorRowValue[]>([])
const scoreMissingStrategy = ref('ignore')
/** 原配置是否显式声明了 missing_factor_strategy（显式写缺省值 ignore 时保存也保留，避免配置哈希无谓变化） */
const scoreMissingDeclared = ref(false)
const scoreScoringMode = ref('absolute')

/** 从 config_json 读取因子别名声明 */
function readFactorAliases(): Record<
  string,
  { template_id: string; params: Record<string, FactorParameterValue> }
> {
  const raw = props.modelValue.factor_aliases as
    | Record<string, { template_id?: string; params?: Record<string, FactorParameterValue> }>
    | undefined
  const result: Record<string, { template_id: string; params: Record<string, FactorParameterValue> }> = {}
  for (const [alias, declaration] of Object.entries(raw ?? {})) {
    result[alias] = {
      template_id: declaration?.template_id ?? '',
      params: { ...(declaration?.params ?? {}) },
    }
  }
  return result
}

/** 模板声明的参数默认值 */
function templateDefaults(templateId: string): Record<string, FactorParameterValue> {
  const template = availableFactors.value.find(f => f.factor_id === templateId)
  const defaults: Record<string, FactorParameterValue> = {}
  for (const [key, spec] of Object.entries(template?.parameter_schema ?? {})) {
    defaults[key] = spec.default
  }
  return defaults
}

/**
 * 把配置中的因子引用还原成一行。
 * 引用名在 factor_aliases 中声明时按声明的模板与参数解析；
 * 否则引用名本身就是模板 ID，参数取模板默认值。
 */
function resolveFactorRow(
  ref: string,
  aliases: Record<string, { template_id: string; params: Record<string, FactorParameterValue> }>,
): FactorRowValue {
  const declaration = aliases[ref]
  if (declaration) {
    return {
      alias: ref,
      template_id: declaration.template_id,
      params: declaration.params,
      weight: 0,
    }
  }
  return { alias: ref, template_id: ref, params: templateDefaults(ref), weight: 0 }
}

function initScore(): void {
  const score = props.modelValue.score as Record<string, unknown> | undefined
  if (!score) return
  const factors = (score.factors ?? {}) as Record<string, number>
  const transforms = (score.transforms ?? {}) as Record<string, string>
  const aliases = readFactorAliases()
  scoreFactors.value = Object.entries(factors).map(([ref, weight]) => ({
    ...resolveFactorRow(ref, aliases),
    weight,
    transform: transforms[ref] || undefined,
  }))
  scoreMissingDeclared.value = typeof score.missing_factor_strategy === 'string'
  scoreMissingStrategy.value = (score.missing_factor_strategy as string) || 'ignore'
  scoreScoringMode.value = (score.scoring_mode as string) || 'absolute'
}

function addScoreFactor(): void {
  scoreFactors.value.push({ alias: '', template_id: '', params: {}, weight: 0.5 })
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
const timingProxyIndexCodes = ref('')
// 择时因子全部缺失时的回退策略，默认 neutral（与后端默认一致，不写入 config_json）
const timingOnDegraded = ref<TimingOnDegraded>('neutral')
// 综合方式：weighted=加权综合分（默认，不写入 config_json）/ rules=规则组
const timingCombine = ref<TimingCombineMode>('weighted')

/** 择时规则组的键 */
type TimingRuleGroupKey = 'offensive' | 'defensive'

/** 单个择时规则组的表单状态（groups 为表单不编辑的嵌套子组，原样保留） */
interface TimingRuleGroupValue {
  logic: string
  rules: FilterRuleValue[]
  groups: unknown[]
}

/** 规则组展示顺序：防守优先判定，因此排在前面 */
const TIMING_RULE_GROUPS: Array<{ key: TimingRuleGroupKey; label: string; short: string }> = [
  { key: 'defensive', label: '防守规则组（满足即防守，优先判定）', short: '防守' },
  { key: 'offensive', label: '进攻规则组（防守组不满足时，满足即进攻）', short: '进攻' },
]

const timingRules = ref<Record<TimingRuleGroupKey, TimingRuleGroupValue>>({
  defensive: { logic: 'OR', rules: [], groups: [] },
  offensive: { logic: 'AND', rules: [], groups: [] },
})

/** 择时 regime 取值 */
type TimingRegimeValue = 'offensive' | 'neutral' | 'defensive'

/** 档位仓位方式：固定仓位 / 维持上一仓位 / 相对上一仓位增减 */
type TimingTierMode = 'fixed' | 'hold' | 'delta'

/** 单个仓位档位的表单状态（仓位类字段以百分数编辑，序列化时转为 0-1） */
interface TimingTierValue extends TimingRuleGroupValue {
  label: string
  mode: TimingTierMode
  exposure: number
  // 相对增减（mode=delta）：增减幅度与夹取区间
  delta: number
  min_exposure: number
  max_exposure: number
  // 加权模式的综合分下限；规则模式不使用
  min_score: number | null
  // 规则模式命中该档时的 regime；加权模式不使用
  regime: TimingRegimeValue
}

/** 规则模式下渲染的规则块：进攻 / 防守规则组，或启用多档仓位时的各档位 */
interface TimingRuleBlock {
  key: string
  label: string
  short: string
  group: TimingRuleGroupValue
  // 档位序号；规则组为 null
  tierIndex: number | null
}

// 多档仓位（timing.tiers）：缺省不启用，不写入 config_json
const timingTiersEnabled = ref(false)
const timingTiers = ref<TimingTierValue[]>([])
// 按因子指定代理的文本形式："别名:代码,代码; 别名2:代码"
const timingFactorProxiesText = ref('')
// 状态层：缺省值即恒等变换，不写入 config_json
const timingHysteresis = ref(0)
const timingHoldOnNeutral = ref(false)
const timingConfirmDays = ref(1)
const timingMinHoldDays = ref(0)
// 档位仓位单次变动上限（%），0=不限制，不写入 config_json
const timingMaxStep = ref(0)
// 动态档位（维持 / 相对增减）的起点仓位（%）
const timingTierBase = ref(50)

/** 条件仓位上限的表单状态（max_exposure 以百分数编辑） */
interface TimingCapValue {
  label: string
  max_exposure: number
  group: RuleGroupValue
}
const timingCaps = ref<TimingCapValue[]>([])

/** 是否存在依赖上一仓位的档位 */
const hasDynamicTier = computed(() => timingTiers.value.some(tier => tier.mode !== 'fixed'))

/** 新增一条条件仓位上限 */
function addTimingCap(): void {
  timingCaps.value.push({ label: '', max_exposure: 50, group: { logic: 'AND', rules: [], groups: [] } })
}

/** 递归收集原始（不透明）规则组里引用的因子，保证其别名声明不丢失 */
function collectRawGroupRefs(groups: unknown[], refs: string[]): void {
  for (const raw of groups) {
    const group = (raw ?? {}) as Record<string, unknown>
    for (const rule of (group.rules ?? []) as Array<Record<string, unknown>>) {
      if (typeof rule.factor === 'string' && rule.factor) refs.push(rule.factor)
      if (typeof rule.compare_to === 'string' && rule.compare_to) refs.push(rule.compare_to)
    }
    collectRawGroupRefs(Array.isArray(group.groups) ? group.groups : [], refs)
  }
}

/** 条件仓位上限引用的因子 */
function timingCapRefs(): string[] {
  const refs: string[] = []
  for (const cap of timingCaps.value) {
    for (const rule of cap.group.rules) if (rule.factor) refs.push(rule.factor)
    collectRawGroupRefs(cap.group.groups, refs)
  }
  return refs
}

/** 把 config_json 中的规则组解析为表单状态 */
function parseTimingRuleGroup(raw: unknown, defaultLogic: string): TimingRuleGroupValue {
  const group = (raw ?? {}) as Record<string, unknown>
  return {
    logic: group.logic === 'OR' || group.logic === 'AND' ? group.logic : defaultLogic,
    rules: ((group.rules ?? []) as Array<Record<string, unknown>>).map(r => ({
      factor: (r.factor as string) || '',
      op: (r.op as string) || 'gt',
      value: r.value as number | number[],
      compare_to: (r.compare_to as string | null | undefined) ?? undefined,
      missing_strategy: r.missing_strategy as string | undefined,
    })),
    groups: Array.isArray(group.groups) ? group.groups : [],
  }
}

/** 规则组与规则档位中引用的因子（含比较因子），用于在因子列表中补齐别名声明 */
function timingRuleRefs(): string[] {
  const refs: string[] = []
  const groups: TimingRuleGroupValue[] = [
    ...TIMING_RULE_GROUPS.map(({ key }) => timingRules.value[key]),
    ...timingTiers.value,
  ]
  for (const group of groups) {
    for (const rule of group.rules) {
      if (rule.factor) refs.push(rule.factor)
      if (rule.compare_to) refs.push(rule.compare_to)
    }
    collectRawGroupRefs(group.groups, refs)
  }
  return refs
}

/** 把 config_json 中的 regime 收窄为合法取值，未知值按 neutral 处理 */
function parseTierRegime(value: unknown): TimingRegimeValue {
  return value === 'offensive' || value === 'defensive' ? value : 'neutral'
}

/** 把 config_json 中的 timing.tiers 解析为表单状态 */
function parseTimingTiers(raw: unknown): TimingTierValue[] {
  if (!Array.isArray(raw)) return []
  return raw.map(item => {
    const tier = (item ?? {}) as Record<string, unknown>
    const mode: TimingTierMode = tier.hold === true ? 'hold' : typeof tier.exposure_delta === 'number' ? 'delta' : 'fixed'
    return {
      ...parseTimingRuleGroup(tier, 'AND'),
      label: typeof tier.label === 'string' ? tier.label : '',
      mode,
      exposure: toPct(tier.exposure, 50),
      delta: toPct(tier.exposure_delta, 25),
      min_exposure: toPct(tier.min_exposure, 0),
      max_exposure: toPct(tier.max_exposure, 100),
      min_score: typeof tier.min_score === 'number' ? tier.min_score : null,
      regime: parseTierRegime(tier.regime),
    }
  })
}

/** 规则模式下的规则块：启用多档仓位时为各档位，否则为防守 / 进攻规则组 */
const timingRuleBlocks = computed<TimingRuleBlock[]>(() => {
  if (timingTiersEnabled.value) {
    return timingTiers.value.map((tier, i) => ({
      key: `tier-${i}`,
      label: `档位 ${i + 1}${tier.label ? `：${tier.label}` : ''}（按顺序首个满足的档位生效）`,
      short: '命中',
      group: tier,
      tierIndex: i,
    }))
  }
  return TIMING_RULE_GROUPS.map(group => ({
    key: group.key,
    label: group.label,
    short: group.short,
    group: timingRules.value[group.key],
    tierIndex: null,
  }))
})

/** 新增一个仓位档位：加权模式默认下限比上一档低 15 分 */
function addTimingTier(): void {
  const last = timingTiers.value[timingTiers.value.length - 1]
  const nextMin = last && last.min_score !== null ? Math.max(0, last.min_score - 15) : 80
  timingTiers.value.push({
    label: '',
    mode: 'fixed',
    exposure: 50,
    delta: 25,
    min_exposure: 0,
    max_exposure: 100,
    min_score: nextMin,
    regime: 'neutral',
    logic: 'AND',
    rules: [],
    groups: [],
  })
}

/** 移除一个仓位档位 */
function removeTimingTier(i: number): void {
  timingTiers.value.splice(i, 1)
}

/** 调整档位顺序（首个命中生效，顺序即优先级） */
function moveTimingTier(i: number, delta: number): void {
  const target = i + delta
  if (target < 0 || target >= timingTiers.value.length) return
  const tiers = timingTiers.value
  ;[tiers[i], tiers[target]] = [tiers[target], tiers[i]]
}

/** 启用多档仓位且尚无档位时，预置一个空档位便于编辑 */
function onTimingTiersToggle(): void {
  if (timingTiersEnabled.value && timingTiers.value.length === 0) addTimingTier()
}

/** 解析“别名:代码,代码; 别名2:代码”为 factor_proxies；格式不完整的条目忽略 */
function parseFactorProxies(text: string): Record<string, string[]> {
  const result: Record<string, string[]> = {}
  for (const entry of text.split(/[;；\n]/)) {
    const [ref, codesText] = entry.split(/[:：]/)
    if (!ref || !codesText) continue
    const codes = codesText.split(/[,，]/).map(c => c.trim()).filter(Boolean)
    if (ref.trim() && codes.length > 0) result[ref.trim()] = codes
  }
  return result
}

/** 向规则组（进攻 / 防守组或规则档位）追加一条空规则 */
function addTimingRule(group: TimingRuleGroupValue): void {
  group.rules.push({ factor: '', op: 'gt', value: 0, compare_to: undefined, missing_strategy: 'fail' })
}

/** 更新规则组中第 i 条规则的单个字段 */
function updateTimingRule(group: TimingRuleGroupValue, i: number, field: string, value: unknown): void {
  ;(group.rules[i] as unknown as Record<string, unknown>)[field] = value
  emitConfig()
}

/** 更新 between 规则的上 / 下限 */
function updateTimingRuleBetween(group: TimingRuleGroupValue, i: number, index: number, raw: string): void {
  const rule = group.rules[i]
  const arr = Array.isArray(rule.value) ? [...rule.value] : [0, 0]
  arr[index] = raw === '' ? 0 : parseFloat(raw)
  rule.value = arr
  emitConfig()
}

/** 切换规则的比较模式（固定值 / 因子比较） */
function onTimingRuleModeChange(group: TimingRuleGroupValue, i: number, mode: string): void {
  const rule = group.rules[i]
  if (mode === 'compare') {
    rule.compare_to = ''
    rule.value = 0
  } else {
    rule.compare_to = undefined
  }
  emitConfig()
}

/** 移除规则组中第 i 条规则 */
function removeTimingRule(group: TimingRuleGroupValue, i: number): void {
  group.rules.splice(i, 1)
  emitConfig()
}

/** 把规则组的表单规则序列化为 config_json 规则（跳过未选因子的规则） */
function serializeTimingRules(rules: FilterRuleValue[]): Array<Record<string, unknown>> {
  return rules.filter(r => r.factor).map(r => {
    const rule: Record<string, unknown> = { factor: r.factor, op: r.op }
    if (r.compare_to) {
      rule.compare_to = r.compare_to
    } else {
      rule.value = r.value
    }
    if (r.missing_strategy && r.missing_strategy !== 'fail') rule.missing_strategy = r.missing_strategy
    return rule
  })
}

/** 把 config_json 中的 on_degraded 收窄为合法取值，未知值按默认 neutral 处理 */
function parseOnDegraded(value: unknown): TimingOnDegraded {
  return value === 'defensive' || value === 'hold_previous' ? value : 'neutral'
}

function initTiming(): void {
  const timing = props.modelValue.timing as Record<string, unknown> | undefined
  if (!timing) {
    timingEnabled.value = false
    timingOnDegraded.value = 'neutral'
    timingTiersEnabled.value = false
    timingTiers.value = []
    timingCaps.value = []
    timingMaxStep.value = 0
    timingTierBase.value = 50
    return
  }
  timingEnabled.value = true
  timingCombine.value = timing.combine === 'rules' ? 'rules' : 'weighted'
  const rules = (timing.rules ?? {}) as Record<string, unknown>
  timingRules.value = {
    defensive: parseTimingRuleGroup(rules.defensive, 'OR'),
    offensive: parseTimingRuleGroup(rules.offensive, 'AND'),
  }
  timingTiers.value = parseTimingTiers(timing.tiers)
  timingTiersEnabled.value = timingTiers.value.length > 0
  const factors = (timing.factors ?? {}) as Record<string, number>
  const transforms = (timing.transforms ?? {}) as Record<string, string>
  const aliases = readFactorAliases()
  const rows: FactorRowValue[] = Object.entries(factors).map(([ref, weight]) => ({
    ...resolveFactorRow(ref, aliases),
    weight,
    transform: transforms[ref] || undefined,
  }))
  timingCaps.value = ((timing.exposure_caps ?? []) as Array<Record<string, unknown>>).map(cap => ({
    label: typeof cap.label === 'string' ? cap.label : '',
    max_exposure: toPct(cap.max_exposure, 50),
    group: parseTimingRuleGroup(cap, 'AND'),
  }))
  timingTierBase.value = toPct(timing.tier_base_exposure, 50)
  // 规则模式引用的因子不在 factors 中：补成因子行，保证别名声明不丢失；
  // 条件仓位上限在两种模式下都只声明别名（加权模式下以权重 0 的行承载）
  const declaredOnly = [...(timingCombine.value === 'rules' ? timingRuleRefs() : []), ...timingCapRefs()]
  for (const ref of Array.from(new Set(declaredOnly))) {
    if (!rows.some(row => row.alias === ref)) rows.push({ ...resolveFactorRow(ref, aliases), weight: 0 })
  }
  timingFactors.value = rows
  const proxies = (timing.factor_proxies ?? {}) as Record<string, string[]>
  timingFactorProxiesText.value = Object.entries(proxies)
    .map(([ref, codes]) => `${ref}:${codes.join(',')}`)
    .join('; ')
  const state = (timing.state ?? {}) as Record<string, unknown>
  timingHysteresis.value = typeof state.hysteresis === 'number' ? state.hysteresis : 0
  timingHoldOnNeutral.value = state.hold_on_neutral === true
  timingConfirmDays.value = typeof state.confirm_days === 'number' ? state.confirm_days : 1
  timingMinHoldDays.value = typeof state.min_hold_days === 'number' ? state.min_hold_days : 0
  timingMaxStep.value = toPct(state.max_exposure_step, 0)
  const thresholds = (timing.thresholds ?? {}) as Record<string, number>
  timingOffensive.value = thresholds.offensive ?? 65
  timingDefensive.value = thresholds.defensive ?? 35
  const proxyCodes = (timing.proxy_index_codes ?? []) as string[]
  timingProxyIndexCodes.value = proxyCodes.length > 0 ? proxyCodes.join(', ') : ''
  timingOnDegraded.value = parseOnDegraded(timing.on_degraded)
}

/**
 * 择时因子原值是否需要变换：模板未声明原生 0-100 时返回 true。
 * 模板列表未加载（或模板已下线）时无法判断，返回 false，交给后端校验兜底。
 */
function needsTimingTransform(templateId: string): boolean {
  const template = availableFactors.value.find(f => f.factor_id === templateId)
  return template !== undefined && template.native_0_100 !== true
}

function addTimingFactor(): void {
  timingFactors.value.push({ alias: '', template_id: '', params: {}, weight: 0.5 })
}

function updateTimingFactor(i: number, val: FactorRowValue): void {
  timingFactors.value[i] = val
  emitConfig()
}

function removeTimingFactor(i: number): void {
  timingFactors.value.splice(i, 1)
  emitConfig()
}

/** 已声明的因子别名（评分 + 择时），过滤器与排名子因子从这里引用 */
const declaredAliases = computed(() => {
  const aliases = [...scoreFactors.value, ...timingFactors.value]
    .map(row => row.alias)
    .filter(Boolean)
  return Array.from(new Set(aliases))
})

/** 过滤器因子候选：已声明别名 + 零参数模板（可直接引用模板 ID，无需声明） */
const filterFactorOptions = computed(() => {
  const options: Array<{ value: string; label: string }> = declaredAliases.value.map(alias => ({
    value: alias,
    label: `${alias}（别名）`,
  }))
  for (const f of availableFactors.value) {
    const zeroParam = Object.keys(f.parameter_schema ?? {}).length === 0
    if (zeroParam && usableFor(f, 'filter')) {
      options.push({ value: f.factor_id, label: f.factor_id })
    }
  }
  return options
})

/** 择时规则因子候选：择时因子行的别名 + 可用于择时的零参数模板 */
const timingRuleFactorOptions = computed(() => {
  const options: Array<{ value: string; label: string }> = timingFactors.value
    .map(row => row.alias)
    .filter(Boolean)
    .map(alias => ({ value: alias, label: `${alias}（别名）` }))
  for (const f of availableFactors.value) {
    const zeroParam = Object.keys(f.parameter_schema ?? {}).length === 0
    if (zeroParam && usableFor(f, 'timing') && !options.some(o => o.value === f.factor_id)) {
      options.push({ value: f.factor_id, label: f.factor_id })
    }
  }
  return options
})

// ── 过滤模块 ──────────────────────────────────────────────────────
const filterEnabled = ref(false)
const filterLogic = ref('AND')
const filterRules = ref<FilterRuleValue[]>([])
// 过滤的嵌套子组：表单不编辑，原样保留
const filterGroups = ref<unknown[]>([])

function initFilter(): void {
  const filters = props.modelValue.filters as Record<string, unknown> | undefined
  if (!filters) { filterEnabled.value = false; filterGroups.value = []; return }
  filterEnabled.value = true
  filterLogic.value = (filters.logic as string) || 'AND'
  filterGroups.value = Array.isArray(filters.groups) ? filters.groups : []
  filterRules.value = ((filters.rules ?? []) as Array<Record<string, unknown>>).map(r => ({
    factor: r.factor as string || '',
    op: r.op as string || 'gt',
    value: r.value as number | number[],
    compare_to: r.compare_to as string | undefined,
    missing_strategy: r.missing_strategy as string | undefined,
  }))
}

function addFilterRule(): void {
  filterRules.value.push({ factor: '', op: 'gt', value: 0, compare_to: undefined, missing_strategy: 'fail' })
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
const rankOrder = ref('desc')
const rankTopN = ref<string>('')
const rankBottomN = ref<string>('')
const rankMomentumFactor = ref('')
const rankValuationFactor = ref('')
const rankBufferN = ref<string>('')
/** 原 rank 段存在但未声明 order（引擎缺省 desc）：未改动时保存继续省略 */
const rankOrderOmitted = ref(false)

function initRank(): void {
  const rank = props.modelValue.rank as Record<string, unknown> | undefined
  if (!rank) return
  rankOrderOmitted.value = rank.order == null
  rankOrder.value = (rank.order as string) || 'desc'
  rankTopN.value = rank.top_n != null ? String(rank.top_n) : ''
  rankBottomN.value = rank.bottom_n != null ? String(rank.bottom_n) : ''
  rankMomentumFactor.value = (rank.momentum_factor as string) || ''
  rankValuationFactor.value = (rank.valuation_factor as string) || ''
  rankBufferN.value = rank.buffer_n != null ? String(rank.buffer_n) : ''
}

// ── 组合模块 ──────────────────────────────────────────────────────
const portfolioMethod = ref('equal_weight')
const portfolioDefaultExposure = ref(50)
const exposureOffensive = ref(80)
const exposureNeutral = ref(50)
const exposureDefensive = ref(20)

type ExposureRegime = 'offensive' | 'neutral' | 'defensive'
/** 引擎未配置 timing_exposure 时使用的 regime 仓位表（engine/portfolio.py 的 _DEFAULT_TIMING_EXPOSURE） */
const ENGINE_DEFAULT_TIMING_EXPOSURE: Record<ExposureRegime, number> = { offensive: 80, neutral: 50, defensive: 20 }
/**
 * timing_exposure 的来源：
 * - absent：原配置未声明，引擎使用默认仓位表；值未改动时保存仍省略该字段；
 * - explicit：原配置已声明（可能只声明部分 regime）。
 */
const timingExposureMode = ref<'absent' | 'explicit'>('absent')
/** explicit 模式下未声明的 regime：引擎回退到 default_exposure，表单显示为「继承默认」，未改动时保存省略 */
const exposureInherited = ref<Record<ExposureRegime, boolean>>({ offensive: false, neutral: false, defensive: false })
/** 原配置是否显式声明了 default_exposure（显式写 0.5 时保存也保留，避免配置哈希无谓变化） */
const defaultExposureDeclared = ref(false)

/** 各 regime 仓位输入框的读写入口 */
const exposureRefs: Record<ExposureRegime, typeof exposureOffensive> = {
  offensive: exposureOffensive,
  neutral: exposureNeutral,
  defensive: exposureDefensive,
}

/** 用户手动修改某个 regime 仓位后，不再视为继承默认 */
function markExposureEdited(regime: ExposureRegime): void {
  exposureInherited.value = { ...exposureInherited.value, [regime]: false }
}

// 继承默认的 regime 跟随默认仓位变化
watch(portfolioDefaultExposure, (value) => {
  if (timingExposureMode.value !== 'explicit') return
  for (const regime of Object.keys(exposureRefs) as ExposureRegime[]) {
    if (exposureInherited.value[regime]) exposureRefs[regime].value = value
  }
})
const portfolioVolFactor = ref('')
const portfolioCovWindow = ref(60)

function initPortfolio(): void {
  const portfolio = props.modelValue.portfolio as Record<string, unknown> | undefined
  if (!portfolio) return
  portfolioMethod.value = (portfolio.method as string) || 'equal_weight'
  defaultExposureDeclared.value = typeof portfolio.default_exposure === 'number'
  portfolioDefaultExposure.value = toPct(portfolio.default_exposure, 50)
  const te = isPlainObject(portfolio.timing_exposure) ? (portfolio.timing_exposure as Record<string, unknown>) : {}
  const declared = Object.keys(te).length > 0
  timingExposureMode.value = declared ? 'explicit' : 'absent'
  const inherited: Record<ExposureRegime, boolean> = { offensive: false, neutral: false, defensive: false }
  for (const regime of Object.keys(exposureRefs) as ExposureRegime[]) {
    if (!declared) {
      exposureRefs[regime].value = ENGINE_DEFAULT_TIMING_EXPOSURE[regime]
    } else if (typeof te[regime] === 'number') {
      exposureRefs[regime].value = toPct(te[regime], ENGINE_DEFAULT_TIMING_EXPOSURE[regime])
    } else {
      // 部分声明：引擎对缺失的 regime 使用 default_exposure
      exposureRefs[regime].value = portfolioDefaultExposure.value
      inherited[regime] = true
    }
  }
  exposureInherited.value = inherited
  portfolioVolFactor.value = (portfolio.vol_factor as string) || ''
  portfolioCovWindow.value = (portfolio.cov_window as number) ?? 60
}

// ── 风控模块 ──────────────────────────────────────────────────────
const riskEnabled = ref(false)
const riskMaxAssetWeight = ref(30)
const riskMaxPortfolioExposure = ref(100)
const riskMinCashRatio = ref(0)
const volTargetEnabled = ref(false)
const volTarget = ref(15)
const volTargetWindow = ref(60)
const volTargetFloor = ref(0)
const drawdownStopEnabled = ref(false)
const drawdownThreshold = ref(15)
const drawdownExposure = ref(0)
const drawdownCooldown = ref(20)
const trailingStopEnabled = ref(false)
const trailingThreshold = ref(12)
const trailingCooldown = ref(20)

/** 小数 → 百分数（保留两位小数，避免 0.15 × 100 的浮点尾数） */
function toPct(value: unknown, fallback: number): number {
  return typeof value === 'number' ? Math.round(value * 10000) / 100 : fallback
}

/** 百分数 → 小数（4 位） */
function fromPct(value: number): number {
  return Math.round(value * 100) / 10000
}

function initRisk(): void {
  const risk = props.modelValue.risk as Record<string, unknown> | undefined
  if (!risk) { riskEnabled.value = false; return }
  riskEnabled.value = true
  riskMaxAssetWeight.value = toPct(risk.max_asset_weight, 30)
  riskMaxPortfolioExposure.value = toPct(risk.max_portfolio_exposure, 100)
  riskMinCashRatio.value = toPct(risk.min_cash_ratio, 0)
  const vt = risk.vol_target as Record<string, unknown> | null | undefined
  volTargetEnabled.value = !!vt
  volTarget.value = toPct(vt?.target, 15)
  volTargetWindow.value = (vt?.window as number) ?? 60
  volTargetFloor.value = toPct(vt?.floor, 0)
  const dd = risk.drawdown_stop as Record<string, unknown> | null | undefined
  drawdownStopEnabled.value = !!dd
  drawdownThreshold.value = toPct(dd?.threshold, 15)
  drawdownExposure.value = toPct(dd?.exposure, 0)
  drawdownCooldown.value = (dd?.cooldown_days as number) ?? 20
  const ts = risk.trailing_stop as Record<string, unknown> | null | undefined
  trailingStopEnabled.value = !!ts
  trailingThreshold.value = toPct(ts?.threshold, 12)
  trailingCooldown.value = (ts?.cooldown_days as number) ?? 20
}

// ── 调仓模块 ──────────────────────────────────────────────────────
// 启用调仓模块时两腿同时生效（风险腿 + 选股腿），两腿日程默认一致
const rebalanceEnabled = ref(false)
const selectionFrequency = ref('daily')
const selectionDayOfWeek = ref<number | null>(null)
const selectionWeekParity = ref('even')
const selectionDayOfMonth = ref<number | null>(null)
const riskSameAsSelection = ref(true)
const riskFrequency = ref('daily')
const riskDayOfWeek = ref<number | null>(null)
const riskWeekParity = ref('even')
const riskDayOfMonth = ref<number | null>(null)
const bandMinTurnover = ref<string>('')
const bandMaxDrift = ref<string>('')

/** 判断两条腿的日程是否等价（用于回显"同频"开关） */
function schedulesEqual(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const keys = ['frequency', 'day_of_week', 'week_parity', 'day_of_month']
  return keys.every(k => (a[k] ?? null) === (b[k] ?? null))
}

function initRebalance(): void {
  const rebalance = props.modelValue.rebalance as Record<string, unknown> | undefined
  if (!rebalance) { rebalanceEnabled.value = false; return }
  rebalanceEnabled.value = true
  const selection = (rebalance.selection as Record<string, unknown> | undefined) || rebalance
  const risk = (rebalance.risk as Record<string, unknown> | undefined) || selection
  selectionFrequency.value = (selection.frequency as string) || 'daily'
  selectionDayOfWeek.value = (selection.day_of_week as number) ?? null
  selectionWeekParity.value = (selection.week_parity as string) || 'even'
  selectionDayOfMonth.value = (selection.day_of_month as number) ?? null
  riskSameAsSelection.value = schedulesEqual(selection, risk)
  riskFrequency.value = (risk.frequency as string) || 'daily'
  riskDayOfWeek.value = (risk.day_of_week as number) ?? null
  riskWeekParity.value = (risk.week_parity as string) || 'even'
  riskDayOfMonth.value = (risk.day_of_month as number) ?? null
  const band = rebalance.band as Record<string, unknown> | null | undefined
  bandMinTurnover.value = typeof band?.min_turnover === 'number' ? String(toPct(band.min_turnover, 0)) : ''
  bandMaxDrift.value = typeof band?.max_drift === 'number' ? String(toPct(band.max_drift, 0)) : ''
}

// ── 校验 ──────────────────────────────────────────────────────────
const errors = computed((): string[] => {
  const errs: string[] = []
  const validFactors = scoreFactors.value.filter(f => f.alias && f.template_id)
  if (portfolioMethod.value === 'inverse_vol' && !portfolioVolFactor.value) {
    errs.push('组合模块：波动率倒数加权需要选择波动率因子')
  }
  if (rankBufferN.value !== '' && rankTopN.value === '') {
    errs.push('排名模块：缓冲带需要同时设置 Top N')
  }
  if (rebalanceEnabled.value && bandMaxDrift.value !== '' && Number(bandMaxDrift.value) <= 0) {
    errs.push('调仓模块：最大偏离必须大于 0')
  }
  if (validFactors.length === 0) {
    errs.push('评分模块：至少需要 1 个因子')
  }
  const allRows = [...scoreFactors.value, ...timingFactors.value].filter(f => f.template_id)
  const seen = new Set<string>()
  for (const f of allRows) {
    if (!f.alias) {
      errs.push(`因子 ${f.template_id}：未填写别名`)
      continue
    }
    if (seen.has(f.alias)) {
      errs.push(`因子别名 ${f.alias} 重复，同一策略内别名必须唯一`)
    }
    seen.add(f.alias)
    for (const [key, spec] of schemaOf(f.template_id)) {
      const value = f.params[key]
      if (spec.type === 'choice') {
        // 枚举参数：取值必须在模板声明的可选列表中
        if (typeof value !== 'string' || !(spec.choices ?? []).includes(value)) {
          errs.push(`因子 ${f.alias}：参数 ${key} 必须是 ${(spec.choices ?? []).join(' / ')} 之一`)
        }
        continue
      }
      if (typeof value !== 'number' || Number.isNaN(value)) {
        errs.push(`因子 ${f.alias}：参数 ${key} 未填写`)
        continue
      }
      if (spec.minimum != null && value < spec.minimum) {
        errs.push(`因子 ${f.alias}：参数 ${key} 不能小于 ${spec.minimum}`)
      }
      if (spec.maximum != null && value > spec.maximum) {
        errs.push(`因子 ${f.alias}：参数 ${key} 不能大于 ${spec.maximum}`)
      }
      if (spec.type === 'integer' && !Number.isInteger(value)) {
        errs.push(`因子 ${f.alias}：参数 ${key} 必须是整数`)
      }
    }
  }
  for (const f of validFactors) {
    if (f.weight === 0) {
      errs.push(`评分模块：因子 ${f.alias} 权重为 0，将不参与评分`)
    }
  }
  if (timingEnabled.value && timingCombine.value === 'weighted') {
    const validTiming = timingFactors.value.filter(f => f.alias && f.template_id)
    if (validTiming.length === 0) {
      errs.push('择时模块：已启用但未配置因子')
    }
    if (timingOffensive.value <= timingDefensive.value) {
      errs.push('择时模块：进攻阈值必须大于防守阈值')
    }
    for (const f of validTiming) {
      if (!f.transform && needsTimingTransform(f.template_id)) {
        errs.push(`择时模块：因子 ${f.alias} 原值不在 0-100 评分空间，必须选择变换函数`)
      }
    }
  }
  if (timingEnabled.value && timingTiersEnabled.value) {
    if (timingTiers.value.length === 0) errs.push('择时模块：已启用多档仓位但未配置档位')
    let previousMin: number | null = null
    timingTiers.value.forEach((tier, i) => {
      if (tier.mode === 'fixed' && !(tier.exposure >= 0 && tier.exposure <= 100)) {
        errs.push(`择时模块：档位 ${i + 1} 仓位必须在 0-100% 之间`)
      }
      if (tier.mode === 'delta') {
        if (!(tier.delta >= -100 && tier.delta <= 100)) errs.push(`择时模块：档位 ${i + 1} 增减幅度必须在 -100%~100% 之间`)
        if (!(tier.min_exposure >= 0 && tier.max_exposure <= 100 && tier.min_exposure <= tier.max_exposure)) {
          errs.push(`择时模块：档位 ${i + 1} 增减后的区间必须在 0-100% 内且下限不大于上限`)
        }
      }
      if (timingCombine.value === 'weighted') {
        if (tier.min_score === null || !Number.isFinite(tier.min_score)) {
          errs.push(`择时模块：档位 ${i + 1} 未填写综合分下限`)
          return
        }
        if (previousMin !== null && tier.min_score >= previousMin) {
          errs.push(`择时模块：档位 ${i + 1} 的综合分下限必须小于上一档（需从高到低排列）`)
        }
        previousMin = tier.min_score
      } else {
        if (tier.rules.length === 0 && tier.groups.length === 0) errs.push(`择时模块：档位 ${i + 1} 至少需要一条命中规则`)
        tier.rules.forEach((rule, j) => {
          if (!rule.factor) errs.push(`择时模块：档位 ${i + 1} 规则 ${j + 1} 未选择因子`)
          if (rule.compare_to !== undefined && rule.compare_to === '') {
            errs.push(`择时模块：档位 ${i + 1} 规则 ${j + 1} 已切换为因子比较模式，但未选择比较因子`)
          }
        })
      }
    })
  }
  if (timingEnabled.value) {
    timingCaps.value.forEach((cap, i) => {
      if (!(cap.max_exposure >= 0 && cap.max_exposure <= 100)) errs.push(`择时模块：仓位上限 ${i + 1} 必须在 0-100% 之间`)
      if (cap.group.rules.length === 0 && cap.group.groups.length === 0) {
        errs.push(`择时模块：仓位上限 ${i + 1} 至少需要一条规则`)
      }
      cap.group.rules.forEach((rule, j) => {
        if (!rule.factor) errs.push(`择时模块：仓位上限 ${i + 1} 规则 ${j + 1} 未选择因子`)
      })
    })
  }
  if (timingEnabled.value && timingCombine.value === 'rules' && !timingTiersEnabled.value) {
    const total = TIMING_RULE_GROUPS.reduce((n, g) => n + timingRules.value[g.key].rules.length, 0)
    if (total === 0) errs.push('择时模块：规则模式至少需要一条进攻或防守规则')
    for (const group of TIMING_RULE_GROUPS) {
      timingRules.value[group.key].rules.forEach((rule, i) => {
        if (!rule.factor) errs.push(`择时模块：${group.short}规则 ${i + 1} 未选择因子`)
        if (rule.compare_to !== undefined && rule.compare_to === '') {
          errs.push(`择时模块：${group.short}规则 ${i + 1} 已切换为因子比较模式，但未选择比较因子`)
        }
      })
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

/** 查询模板声明的参数模式 */
function schemaOf(templateId: string): Array<[string, FactorParameterSpec]> {
  const template = availableFactors.value.find(f => f.factor_id === templateId)
  return Object.entries(template?.parameter_schema ?? {})
}

/**
 * 生成别名声明：别名与模板 ID 同名且参数等于模板默认值时返回 null（无需声明）。
 */
function buildAliasDeclaration(
  alias: string,
  templateId: string,
  params: Record<string, FactorParameterValue>,
): { template_id: string; params: Record<string, FactorParameterValue> } | null {
  const defaults = templateDefaults(templateId)
  const paramsText = JSON.stringify(Object.fromEntries(Object.entries(params).sort()))
  const defaultsText = JSON.stringify(Object.fromEntries(Object.entries(defaults).sort()))
  if (alias === templateId && paramsText === defaultsText) return null
  return { template_id: templateId, params: { ...params } }
}

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
  const factorAliases: Record<string, { template_id: string; params: Record<string, FactorParameterValue> }> = {}
  for (const f of scoreFactors.value) {
    if (!f.alias || !f.template_id) continue
    factors[f.alias] = f.weight
    if (f.transform) transforms[f.alias] = f.transform
    const declaration = buildAliasDeclaration(f.alias, f.template_id, f.params)
    if (declaration) factorAliases[f.alias] = declaration
  }
  const scoreConfig: Record<string, unknown> = { factors }
  if (Object.keys(transforms).length > 0) scoreConfig.transforms = transforms
  if (scoreMissingStrategy.value !== 'ignore' || scoreMissingDeclared.value) {
    scoreConfig.missing_factor_strategy = scoreMissingStrategy.value
  }
  if (scoreScoringMode.value !== 'absolute') scoreConfig.scoring_mode = scoreScoringMode.value
  config.score = scoreConfig

  // 择时
  if (timingEnabled.value) {
    const tFactors: Record<string, number> = {}
    const tTransforms: Record<string, string> = {}
    const capRefs = new Set(timingCapRefs())
    for (const f of timingFactors.value) {
      if (!f.alias || !f.template_id) continue
      // 规则模式下因子行只用于声明别名，不写入 factors 权重；
      // 加权模式下只被条件仓位上限引用、权重为 0 的行同样只声明别名
      const declarationOnly = f.weight === 0 && capRefs.has(f.alias)
      if (timingCombine.value === 'weighted' && !declarationOnly) {
        tFactors[f.alias] = f.weight
        if (f.transform) tTransforms[f.alias] = f.transform
      }
      const declaration = buildAliasDeclaration(f.alias, f.template_id, f.params)
      if (declaration) factorAliases[f.alias] = declaration
    }
    const timingConfig: Record<string, unknown> = {}
    if (timingCombine.value === 'rules') {
      timingConfig.combine = 'rules'
      // 规则档位与进攻 / 防守规则组互斥：启用多档仓位时不写规则组
      if (!timingTiersEnabled.value) {
        const rules: Record<string, unknown> = {}
        for (const { key } of TIMING_RULE_GROUPS) {
          const group = timingRules.value[key]
          const groupRules = serializeTimingRules(group.rules)
          if (groupRules.length > 0 || group.groups.length > 0) {
            const item: Record<string, unknown> = { logic: group.logic, rules: groupRules }
            if (group.groups.length > 0) item.groups = group.groups
            rules[key] = item
          }
        }
        timingConfig.rules = rules
      }
    } else {
      timingConfig.factors = tFactors
      if (Object.keys(tTransforms).length > 0) timingConfig.transforms = tTransforms
      timingConfig.thresholds = {
        offensive: timingOffensive.value,
        defensive: timingDefensive.value,
      }
    }
    const factorProxies = parseFactorProxies(timingFactorProxiesText.value)
    if (Object.keys(factorProxies).length > 0) timingConfig.factor_proxies = factorProxies
    const state: Record<string, unknown> = {}
    if (timingCombine.value === 'weighted' && timingHysteresis.value > 0) state.hysteresis = timingHysteresis.value
    if (timingHoldOnNeutral.value) state.hold_on_neutral = true
    if (timingConfirmDays.value > 1) state.confirm_days = timingConfirmDays.value
    if (timingMinHoldDays.value > 0) state.min_hold_days = timingMinHoldDays.value
    if (timingMaxStep.value > 0) state.max_exposure_step = fromPct(timingMaxStep.value)
    if (Object.keys(state).length > 0) timingConfig.state = state
    const proxyCodes = timingProxyIndexCodes.value
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
    if (proxyCodes.length > 0) timingConfig.proxy_index_codes = proxyCodes
    if (timingOnDegraded.value !== 'neutral') timingConfig.on_degraded = timingOnDegraded.value
    if (timingTiersEnabled.value && timingTiers.value.length > 0) {
      timingConfig.tiers = timingTiers.value.map(tier => {
        const item: Record<string, unknown> = {}
        if (tier.mode === 'hold') {
          item.hold = true
        } else if (tier.mode === 'delta') {
          item.exposure_delta = fromPct(tier.delta)
          if (tier.min_exposure !== 0) item.min_exposure = fromPct(tier.min_exposure)
          if (tier.max_exposure !== 100) item.max_exposure = fromPct(tier.max_exposure)
        } else {
          item.exposure = fromPct(tier.exposure)
        }
        if (tier.label.trim()) item.label = tier.label.trim()
        if (timingCombine.value === 'weighted') {
          item.min_score = tier.min_score
        } else {
          item.regime = tier.regime
          item.logic = tier.logic
          item.rules = serializeTimingRules(tier.rules)
          if (tier.groups.length > 0) item.groups = tier.groups
        }
        return item
      })
      if (hasDynamicTier.value) timingConfig.tier_base_exposure = fromPct(timingTierBase.value)
    }
    if (timingCaps.value.length > 0) {
      timingConfig.exposure_caps = timingCaps.value.map(cap => {
        const item: Record<string, unknown> = {
          max_exposure: fromPct(cap.max_exposure),
          logic: cap.group.logic,
          rules: serializeTimingRules(cap.group.rules),
        }
        if (cap.label.trim()) item.label = cap.label.trim()
        if (cap.group.groups.length > 0) item.groups = cap.group.groups
        return item
      })
    }
    config.timing = timingConfig
  }

  // 因子别名声明：别名与模板 ID 同名且无参数覆盖时无需声明
  if (Object.keys(factorAliases).length > 0) {
    config.factor_aliases = factorAliases
  }

  // 过滤
  if (filterEnabled.value && (filterRules.value.length > 0 || filterGroups.value.length > 0)) {
    config.filters = {
      ...(filterGroups.value.length > 0 ? { groups: filterGroups.value } : {}),
      logic: filterLogic.value,
      rules: filterRules.value.filter(r => r.factor).map(r => {
        const rule: Record<string, unknown> = { factor: r.factor, op: r.op }
        if (r.compare_to) {
          rule.compare_to = r.compare_to
        } else {
          rule.value = r.value
        }
        if (r.missing_strategy && r.missing_strategy !== 'fail') {
          rule.missing_strategy = r.missing_strategy
        }
        return rule
      }),
    }
  }

  // 排名
  const rank: Record<string, unknown> = {}
  if (!(rankOrderOmitted.value && rankOrder.value === 'desc')) rank.order = rankOrder.value
  if (rankTopN.value !== '') rank.top_n = parseInt(rankTopN.value, 10)
  if (rankBottomN.value !== '') rank.bottom_n = parseInt(rankBottomN.value, 10)
  // 子排名因子仅用于结果展示，不参与排序
  if (rankMomentumFactor.value) rank.momentum_factor = rankMomentumFactor.value
  if (rankValuationFactor.value) rank.valuation_factor = rankValuationFactor.value
  if (rankBufferN.value !== '' && rankTopN.value !== '') rank.buffer_n = parseInt(rankBufferN.value, 10)
  config.rank = rank

  // 组合
  const portfolio: Record<string, unknown> = { method: portfolioMethod.value }
  const regimes = Object.keys(exposureRefs) as ExposureRegime[]
  if (timingExposureMode.value === 'absent') {
    // 原配置未声明且仍等于引擎默认表：保持省略（语义相同，配置哈希不变）
    const changed = regimes.some(r => exposureRefs[r].value !== ENGINE_DEFAULT_TIMING_EXPOSURE[r])
    if (changed) {
      portfolio.timing_exposure = Object.fromEntries(regimes.map(r => [r, fromPct(exposureRefs[r].value)]))
    }
  } else {
    // 继承 default_exposure 的 regime 不写出，保持引擎回退口径
    const te: Record<string, number> = {}
    for (const r of regimes) {
      if (!exposureInherited.value[r]) te[r] = fromPct(exposureRefs[r].value)
    }
    if (Object.keys(te).length > 0) portfolio.timing_exposure = te
  }
  if (portfolioDefaultExposure.value !== 50 || defaultExposureDeclared.value) {
    portfolio.default_exposure = fromPct(portfolioDefaultExposure.value)
  }
  if (portfolioMethod.value === 'inverse_vol' && portfolioVolFactor.value) {
    portfolio.vol_factor = portfolioVolFactor.value
  }
  if (portfolioMethod.value === 'risk_parity' && portfolioCovWindow.value !== 60) {
    portfolio.cov_window = portfolioCovWindow.value
  }
  config.portfolio = portfolio

  // 风控
  if (riskEnabled.value) {
    config.risk = {
      max_asset_weight: fromPct(riskMaxAssetWeight.value),
      max_portfolio_exposure: fromPct(riskMaxPortfolioExposure.value),
      min_cash_ratio: fromPct(riskMinCashRatio.value),
    }
    const risk = config.risk as Record<string, unknown>
    if (volTargetEnabled.value) {
      const vt: Record<string, unknown> = { target: fromPct(volTarget.value), window: volTargetWindow.value }
      if (volTargetFloor.value > 0) vt.floor = fromPct(volTargetFloor.value)
      risk.vol_target = vt
    }
    if (drawdownStopEnabled.value) {
      risk.drawdown_stop = {
        threshold: fromPct(drawdownThreshold.value),
        exposure: fromPct(drawdownExposure.value),
        cooldown_days: drawdownCooldown.value,
      }
    }
    if (trailingStopEnabled.value) {
      risk.trailing_stop = { threshold: fromPct(trailingThreshold.value), cooldown_days: trailingCooldown.value }
    }
  }

  // 调仓：两条腿同时输出（两腿必选）；勾选同频时风险腿复用选股腿日程
  if (rebalanceEnabled.value) {
    const buildSchedule = (
      frequency: string,
      dayOfWeek: number | null,
      weekParity: string,
      dayOfMonth: number | null,
    ): Record<string, unknown> => {
      const schedule: Record<string, unknown> = { frequency }
      if ((frequency === 'weekly' || frequency === 'biweekly') && dayOfWeek != null) schedule.day_of_week = dayOfWeek
      if (frequency === 'biweekly') schedule.week_parity = weekParity
      if (frequency === 'monthly' && dayOfMonth != null) schedule.day_of_month = dayOfMonth
      return schedule
    }
    const selection = buildSchedule(
      selectionFrequency.value,
      selectionDayOfWeek.value,
      selectionWeekParity.value,
      selectionDayOfMonth.value,
    )
    const risk = riskSameAsSelection.value
      ? { ...selection }
      : buildSchedule(riskFrequency.value, riskDayOfWeek.value, riskWeekParity.value, riskDayOfMonth.value)
    const rebalanceConfig: Record<string, unknown> = { selection, risk }
    const band: Record<string, unknown> = {}
    if (bandMinTurnover.value !== '') band.min_turnover = fromPct(Number(bandMinTurnover.value))
    if (bandMaxDrift.value !== '') band.max_drift = fromPct(Number(bandMaxDrift.value))
    if (Object.keys(band).length > 0) rebalanceConfig.band = band
    config.rebalance = rebalanceConfig
  }

  preserveUnmanaged(config)
  return config
}

// 表单直接编辑的顶层字段；其余字段（如 regime_rules、schema_version）保存时原样保留
const MANAGED_TOP_LEVEL_KEYS = new Set([
  'index_codes', 'score', 'timing', 'factor_aliases', 'filters', 'rank', 'portfolio', 'risk', 'rebalance',
])

/** 段内字段保留规则：managed 为表单直接编辑的键，nested 为需要递归处理的子对象。 */
interface SectionKeySpec {
  managed: Set<string>
  nested?: Record<string, SectionKeySpec>
}

/**
 * 各管理段内表单直接编辑的键。段内不在此列的键（新增配置能力、表单尚未渲染的字段）
 * 保存时从原配置带回；在此列的键由表单全权决定，buildConfig 省略即表示恢复缺省值，
 * 不能回填（否则用户改回缺省值的字段会被旧值复活）。
 */
const MANAGED_SECTION_KEYS: Record<string, SectionKeySpec> = {
  score: { managed: new Set(['factors', 'transforms', 'missing_factor_strategy', 'scoring_mode']) },
  timing: {
    managed: new Set([
      'combine', 'factors', 'transforms', 'thresholds', 'rules', 'proxy_index_codes', 'factor_proxies',
      'on_degraded', 'state', 'tiers', 'tier_base_exposure', 'exposure_caps',
    ]),
    nested: {
      state: {
        managed: new Set(['hysteresis', 'hold_on_neutral', 'confirm_days', 'min_hold_days', 'max_exposure_step']),
      },
    },
  },
  filters: { managed: new Set(['logic', 'rules', 'groups']) },
  rank: { managed: new Set(['order', 'top_n', 'bottom_n', 'momentum_factor', 'valuation_factor', 'buffer_n']) },
  portfolio: { managed: new Set(['method', 'timing_exposure', 'default_exposure', 'vol_factor', 'cov_window']) },
  risk: {
    managed: new Set([
      'max_asset_weight', 'max_portfolio_exposure', 'min_cash_ratio', 'vol_target', 'drawdown_stop', 'trailing_stop',
    ]),
  },
  rebalance: { managed: new Set(['selection', 'risk', 'band']) },
}

/** 判断值是否为普通对象（非数组、非 null）。 */
function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * 把原段对象里表单未管理的键带回新段对象；新段整段缺失（用户关闭了该模块）时不恢复。
 */
function mergeSectionUnmanaged(
  built: Record<string, unknown>,
  original: Record<string, unknown>,
  spec: SectionKeySpec,
): void {
  for (const [key, value] of Object.entries(original)) {
    const nestedSpec = spec.nested?.[key]
    if (nestedSpec && isPlainObject(value)) {
      const target = isPlainObject(built[key]) ? (built[key] as Record<string, unknown>) : {}
      mergeSectionUnmanaged(target, value, nestedSpec)
      if (Object.keys(target).length > 0) built[key] = target
      continue
    }
    if (!spec.managed.has(key) && !(key in built)) built[key] = value
  }
}

/** 从派生因子表达式文本中提取可能的引用名（函数名不会命中别名表，无需排除）。 */
function expressionNames(expr: unknown): string[] {
  return typeof expr === 'string' ? (expr.match(/[A-Za-z_][A-Za-z0-9_]*/g) ?? []) : []
}

/**
 * 保留表单不渲染的配置：顶层未管理字段、各管理段内未管理的键原样带回；仍被引用但表单
 * 没有生成声明的因子别名（嵌套子组、regime_rules、派生因子表达式等引用）从原配置补回，
 * 避免一次保存静默丢掉配置。
 */
function preserveUnmanaged(config: Record<string, unknown>): void {
  const original = props.modelValue ?? {}
  for (const [key, value] of Object.entries(original)) {
    if (!MANAGED_TOP_LEVEL_KEYS.has(key) && !(key in config)) config[key] = value
  }
  for (const [section, spec] of Object.entries(MANAGED_SECTION_KEYS)) {
    const built = config[section]
    const source = original[section]
    if (isPlainObject(built) && isPlainObject(source)) mergeSectionUnmanaged(built, source, spec)
  }
  const originalAliases = (original.factor_aliases ?? {}) as Record<string, unknown>
  const aliases = { ...((config.factor_aliases ?? {}) as Record<string, unknown>) }
  const body = JSON.stringify({ ...config, factor_aliases: undefined })
  for (const [alias, declaration] of Object.entries(originalAliases)) {
    if (!(alias in aliases) && body.includes(JSON.stringify(alias))) aliases[alias] = declaration
  }
  // 派生因子的依赖名只出现在 params.expr 文本里：按依赖闭包补回原声明
  const pending = Object.keys(aliases)
  while (pending.length > 0) {
    const declaration = aliases[pending.pop() as string]
    if (!isPlainObject(declaration) || declaration.template_id !== 'expr') continue
    const params = isPlainObject(declaration.params) ? declaration.params : {}
    for (const name of expressionNames(params.expr)) {
      if (!(name in aliases) && name in originalAliases) {
        aliases[name] = originalAliases[name]
        pending.push(name)
      }
    }
  }
  if (Object.keys(aliases).length > 0) config.factor_aliases = aliases
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
    timingEnabled, timingFactors, timingOffensive, timingDefensive, timingProxyIndexCodes, timingOnDegraded,
    timingCombine, timingRules, timingFactorProxiesText, timingTiersEnabled, timingTiers,
    timingHysteresis, timingHoldOnNeutral, timingConfirmDays, timingMinHoldDays,
    timingMaxStep, timingTierBase, timingCaps, filterGroups,
    filterEnabled, filterLogic, filterRules,
    rankOrder, rankTopN, rankBottomN, rankMomentumFactor, rankValuationFactor, rankBufferN,
    portfolioMethod, portfolioDefaultExposure, exposureOffensive, exposureNeutral, exposureDefensive,
    portfolioVolFactor, portfolioCovWindow,
    riskEnabled, riskMaxAssetWeight, riskMaxPortfolioExposure, riskMinCashRatio,
    volTargetEnabled, volTarget, volTargetWindow, volTargetFloor,
    drawdownStopEnabled, drawdownThreshold, drawdownExposure, drawdownCooldown,
    trailingStopEnabled, trailingThreshold, trailingCooldown,
    rebalanceEnabled, selectionFrequency, selectionDayOfWeek, selectionWeekParity, selectionDayOfMonth,
    riskSameAsSelection, riskFrequency, riskDayOfWeek, riskWeekParity, riskDayOfMonth,
    bandMinTurnover, bandMaxDrift,
  ],
  () => emitConfig(),
  { deep: true },
)

/** 从 modelValue 重新解析全部模块 */
function reinitialize(): void {
  initScope()
  initScore()
  initTiming()
  initFilter()
  initRank()
  initPortfolio()
  initRisk()
  initRebalance()
}

// 外部 modelValue 变化时重新初始化（排除自身 emit 导致的循环更新）
let selfUpdating = false
watch(() => props.modelValue, () => {
  if (selfUpdating) return
  reinitialize()
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
.inherit-tag { font-size: 11px; color: var(--text-muted); border: 1px dashed var(--border); border-radius: 4px; padding: 0 4px; }

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
.cap-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
  margin-bottom: 6px;
}
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
.fr-missing { flex: 0.9; }
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
.checkbox-label input { accent-color: var(--accent); flex-shrink: 0; }

/* 指数多选区域 */
.index-checkboxes {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 200px;
  overflow-y: auto;
}
.index-code { font-family: var(--font-mono); font-size: 12px; color: var(--accent); min-width: 52px; }
.index-name { font-size: 12px; color: var(--text-muted); }

.index-summary {
  font-size: 11px;
  color: var(--text-muted);
  padding: 4px 0;
}
.index-summary.empty { font-style: italic; }
.index-codes { font-family: var(--font-mono); color: var(--accent); }

/* 组合模块未启用警告 */
.portfolio-warn {
  padding: 10px 14px;
  margin-top: 4px;
  background: rgba(245,158,11,0.08);
  border: 1px solid rgba(245,158,11,0.25);
  border-radius: var(--radius-sm);
  font-size: 12px;
  color: #f59e0b;
  line-height: 1.6;
}

</style>
