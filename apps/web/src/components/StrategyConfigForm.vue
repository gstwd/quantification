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
        <div class="sub-field">
          <label class="sub-label">代理指数代码（逗号分隔）</label>
          <input
            v-model="timingProxyIndexCodes"
            class="fp-input"
            placeholder="如 000300,000016，留空=默认沪深300"
          />
          <span class="threshold-hint">用于加载市场级择时因子的代表性指数</span>
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
          <div v-if="rankSortBy === 'momentum_rank'" class="sub-field">
            <label class="sub-label">动量子因子</label>
            <select v-model="rankMomentumFactor" class="fp-select">
              <option value="" disabled>选择动量子因子</option>
              <option v-for="alias in declaredAliases" :key="alias" :value="alias">
                {{ alias }}
              </option>
            </select>
          </div>
          <div v-if="rankSortBy === 'valuation_rank'" class="sub-field">
            <label class="sub-label">估值子因子</label>
            <select v-model="rankValuationFactor" class="fp-select">
              <option value="" disabled>选择估值子因子</option>
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
            <option value="odd">奇数周（ISO 周序）</option>
            <option value="even">偶数周（ISO 周序）</option>
          </select>
          <span class="threshold-hint">双周必须指定奇/偶周：按 ISO 周序的奇偶决定哪一周调仓（第 1 周起算）。</span>
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
              <option value="odd">奇数周（ISO 周序）</option>
              <option value="even">偶数周（ISO 周序）</option>
            </select>
          </div>
          <div v-if="riskFrequency === 'monthly'" class="sub-field">
            <label class="sub-label">风险腿月调仓日</label>
            <input v-model.number="riskDayOfMonth" type="number" min="1" max="28" class="fp-input" placeholder="1-28" />
          </div>
          <div class="threshold-hint">
            提示：两腿日程不同时，选股腿日只替换成分、维持当前总仓位；总仓位仅在风险腿日按择时目标调整。
          </div>
        </template>

        <div v-if="!timingEnabled" class="threshold-hint">
          提示：未启用择时模块时风险腿只会把总仓位缩放到组合模块的默认仓位（default_exposure），不会随市场状态变化。
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
import type { BenchmarkIndex, FactorSpec } from '../types/api'
import FactorPicker from './FactorPicker.vue'
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
  params: Record<string, number>
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
const scoreScoringMode = ref('absolute')

/** 从 config_json 读取因子别名声明 */
function readFactorAliases(): Record<
  string,
  { template_id: string; params: Record<string, number> }
> {
  const raw = props.modelValue.factor_aliases as
    | Record<string, { template_id?: string; params?: Record<string, number> }>
    | undefined
  const result: Record<string, { template_id: string; params: Record<string, number> }> = {}
  for (const [alias, declaration] of Object.entries(raw ?? {})) {
    result[alias] = {
      template_id: declaration?.template_id ?? '',
      params: { ...(declaration?.params ?? {}) },
    }
  }
  return result
}

/** 模板声明的参数默认值 */
function templateDefaults(templateId: string): Record<string, number> {
  const template = availableFactors.value.find(f => f.factor_id === templateId)
  const defaults: Record<string, number> = {}
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
  aliases: Record<string, { template_id: string; params: Record<string, number> }>,
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

function initTiming(): void {
  const timing = props.modelValue.timing as Record<string, unknown> | undefined
  if (!timing) { timingEnabled.value = false; return }
  timingEnabled.value = true
  const factors = (timing.factors ?? {}) as Record<string, number>
  const transforms = (timing.transforms ?? {}) as Record<string, string>
  const aliases = readFactorAliases()
  timingFactors.value = Object.entries(factors).map(([ref, weight]) => ({
    ...resolveFactorRow(ref, aliases),
    weight,
    transform: transforms[ref] || undefined,
  }))
  const thresholds = (timing.thresholds ?? {}) as Record<string, number>
  timingOffensive.value = thresholds.offensive ?? 65
  timingDefensive.value = thresholds.defensive ?? 35
  const proxyCodes = (timing.proxy_index_codes ?? []) as string[]
  timingProxyIndexCodes.value = proxyCodes.length > 0 ? proxyCodes.join(', ') : ''
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
const rankSortBy = ref('score')
const rankOrder = ref('desc')
const rankTopN = ref<string>('')
const rankBottomN = ref<string>('')
const rankMomentumFactor = ref('')
const rankValuationFactor = ref('')

function initRank(): void {
  const rank = props.modelValue.rank as Record<string, unknown> | undefined
  if (!rank) return
  rankSortBy.value = (rank.sort_by as string) || 'score'
  rankOrder.value = (rank.order as string) || 'desc'
  rankTopN.value = rank.top_n != null ? String(rank.top_n) : ''
  rankBottomN.value = rank.bottom_n != null ? String(rank.bottom_n) : ''
  rankMomentumFactor.value = (rank.momentum_factor as string) || ''
  rankValuationFactor.value = (rank.valuation_factor as string) || ''
}

// ── 组合模块 ──────────────────────────────────────────────────────
const portfolioMethod = ref('equal_weight')
const portfolioDefaultExposure = ref(50)
const exposureOffensive = ref(80)
const exposureNeutral = ref(50)
const exposureDefensive = ref(20)

function initPortfolio(): void {
  const portfolio = props.modelValue.portfolio as Record<string, unknown> | undefined
  if (!portfolio) return
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
}

// ── 校验 ──────────────────────────────────────────────────────────
const errors = computed((): string[] => {
  const errs: string[] = []
  const validFactors = scoreFactors.value.filter(f => f.alias && f.template_id)
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
      if (value === undefined || Number.isNaN(value)) {
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
  if (timingEnabled.value) {
    const validTiming = timingFactors.value.filter(f => f.alias && f.template_id)
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
  if (rankSortBy.value === 'momentum_rank' && !rankMomentumFactor.value) {
    errs.push('排名模块：按动量排名排序时必须选择动量子因子')
  }
  if (rankSortBy.value === 'valuation_rank' && !rankValuationFactor.value) {
    errs.push('排名模块：按估值排名排序时必须选择估值子因子')
  }
  return errs
})

/** 查询模板声明的参数模式 */
function schemaOf(templateId: string): Array<[string, { type: string; minimum: number | null; maximum: number | null }]> {
  const template = availableFactors.value.find(f => f.factor_id === templateId)
  return Object.entries(template?.parameter_schema ?? {})
}

/**
 * 生成别名声明：别名与模板 ID 同名且参数等于模板默认值时返回 null（无需声明）。
 */
function buildAliasDeclaration(
  alias: string,
  templateId: string,
  params: Record<string, number>,
): { template_id: string; params: Record<string, number> } | null {
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
  const factorAliases: Record<string, { template_id: string; params: Record<string, number> }> = {}
  for (const f of scoreFactors.value) {
    if (!f.alias || !f.template_id) continue
    factors[f.alias] = f.weight
    if (f.transform) transforms[f.alias] = f.transform
    const declaration = buildAliasDeclaration(f.alias, f.template_id, f.params)
    if (declaration) factorAliases[f.alias] = declaration
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
      if (!f.alias || !f.template_id) continue
      tFactors[f.alias] = f.weight
      if (f.transform) tTransforms[f.alias] = f.transform
      const declaration = buildAliasDeclaration(f.alias, f.template_id, f.params)
      if (declaration) factorAliases[f.alias] = declaration
    }
    const timingConfig: Record<string, unknown> = { factors: tFactors }
    if (Object.keys(tTransforms).length > 0) timingConfig.transforms = tTransforms
    timingConfig.thresholds = {
      offensive: timingOffensive.value,
      defensive: timingDefensive.value,
    }
    const proxyCodes = timingProxyIndexCodes.value
      .split(',')
      .map(s => s.trim())
      .filter(Boolean)
    if (proxyCodes.length > 0) timingConfig.proxy_index_codes = proxyCodes
    config.timing = timingConfig
  }

  // 因子别名声明：别名与模板 ID 同名且无参数覆盖时无需声明
  if (Object.keys(factorAliases).length > 0) {
    config.factor_aliases = factorAliases
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
        if (r.missing_strategy && r.missing_strategy !== 'fail') {
          rule.missing_strategy = r.missing_strategy
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
  if (rankSortBy.value === 'momentum_rank' && rankMomentumFactor.value) {
    rank.momentum_factor = rankMomentumFactor.value
  }
  if (rankSortBy.value === 'valuation_rank' && rankValuationFactor.value) {
    rank.valuation_factor = rankValuationFactor.value
  }
  config.rank = rank

  // 组合
  const portfolio: Record<string, unknown> = { method: portfolioMethod.value }
  portfolio.timing_exposure = {
    offensive: exposureOffensive.value / 100,
    neutral: exposureNeutral.value / 100,
    defensive: exposureDefensive.value / 100,
  }
  if (portfolioDefaultExposure.value !== 50) portfolio.default_exposure = portfolioDefaultExposure.value / 100
  config.portfolio = portfolio

  // 风控
  if (riskEnabled.value) {
    config.risk = {
      max_asset_weight: riskMaxAssetWeight.value / 100,
      max_portfolio_exposure: riskMaxPortfolioExposure.value / 100,
      min_cash_ratio: riskMinCashRatio.value / 100,
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
    config.rebalance = { selection, risk }
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
    timingEnabled, timingFactors, timingOffensive, timingDefensive, timingProxyIndexCodes,
    filterEnabled, filterLogic, filterRules,
    rankSortBy, rankOrder, rankTopN, rankBottomN, rankMomentumFactor, rankValuationFactor,
    portfolioMethod, portfolioDefaultExposure, exposureOffensive, exposureNeutral, exposureDefensive,
    riskEnabled, riskMaxAssetWeight, riskMaxPortfolioExposure, riskMinCashRatio,
    rebalanceEnabled, selectionFrequency, selectionDayOfWeek, selectionWeekParity, selectionDayOfMonth,
    riskSameAsSelection, riskFrequency, riskDayOfWeek, riskWeekParity, riskDayOfMonth,
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
.index-code { font-family: monospace; font-size: 12px; color: var(--accent); min-width: 52px; }
.index-name { font-size: 12px; color: var(--text-muted); }

.index-summary {
  font-size: 11px;
  color: var(--text-muted);
  padding: 4px 0;
}
.index-summary.empty { font-style: italic; }
.index-codes { font-family: monospace; color: var(--accent); }

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
