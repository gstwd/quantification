<template>
  <!--
    策略详情页面。

    展示策略配置的完整内容，包括各模块配置（评分、过滤、排名、组合、风控）。
    支持编辑和删除策略。
  -->
  <div class="page">
    <div v-if="store.error && !currentMatches" class="error-tip">{{ store.error }}</div>
    <div v-else-if="store.loading && !currentMatches" class="loading">加载中...</div>
    <template v-else-if="store.current && currentMatches">
      <div class="page-header">
        <div class="header-left">
          <h1 class="page-title">
            {{ store.current.display_name }}
            <button
              class="star-btn"
              :class="{ starred: store.current.is_starred }"
              :title="store.current.is_starred ? '取消星标' : '星标关注'"
              @click="handleToggleStar"
            >
              {{ store.current.is_starred ? '★' : '☆' }}
            </button>
          </h1>
          <div class="chips">
            <span class="chip chip-version">v{{ store.current.version }}</span>
            <span
              class="chip chip-freq"
              title="策略标注频率（元数据，不控制调仓；实际调仓频率见下方调仓模块的 frequency）"
              >{{ frequencyLabel(store.current.frequency) }}</span
            >
            <span :class="['chip', store.current.status === 'active' ? 'chip-active' : 'chip-disabled']">
              {{ strategyStatusLabel(store.current.status) }}
            </span>
            <span v-if="isLive" class="chip chip-live" title="上线中的策略配置已冻结，不能直接修改 config_json">上线中</span>
          </div>
        </div>
        <div class="header-actions">
          <input
            type="date"
            class="date-input"
            v-model="selectedDate"
            :max="todayStr"
            title="选择交易日查看历史决策"
          />
          <button
            class="btn-accent"
            title="按所选交易日运行一次决策管线，输出研究用的目标配置（不产生交易）"
            @click="handleRunAllocation"
            :disabled="allocating"
          >
            {{ allocating ? '计算中...' : '计算当日配置' }}
          </button>
          <button class="btn-secondary" @click="showEdit = true">编辑</button>
          <button class="btn-danger" @click="handleClearResearchData" :disabled="clearingResearchData">
            {{ clearingResearchData ? '清理中...' : '清理研究数据' }}
          </button>
          <button class="btn-danger" @click="handleDelete">删除</button>
        </div>
      </div>

      <p class="description">{{ store.current.description || '暂无描述' }}</p>
      <div v-if="store.actionError && !showEdit" class="error-tip">{{ store.actionError }}</div>

      <!-- 页签：策略配置 / 稳健性验证 -->
      <div class="debug-tabs page-tabs">
        <button
          :class="['debug-tab', { active: activeTab === 'config' }]"
          @click="activeTab = 'config'"
        >
          策略配置
        </button>
        <button
          :class="['debug-tab', { active: activeTab === 'robustness' }]"
          @click="activeTab = 'robustness'"
        >
          稳健性验证
          <span v-if="robustnessRuns.length" class="tab-count">{{ robustnessRuns.length }}</span>
        </button>
      </div>

      <!-- 策略配置模块 -->
      <div v-if="activeTab === 'config'" class="config-grid">
        <!-- 组合策略成员 -->
        <div v-if="isComposite" class="config-card">
          <div class="config-header">组合成员 (Composite)</div>
          <div class="config-body">
            <div class="config-section">
              <div v-for="member in compositeConfig?.members ?? []" :key="member.strategy_id" class="config-row">
                <router-link :to="`/strategies/${member.strategy_id}`" class="config-key mono">
                  {{ member.strategy_id }}
                </router-link>
                <span class="config-val">{{ (member.weight * 100).toFixed(1) }}%</span>
              </div>
              <div v-if="(compositeConfig?.cash_weight ?? 0) > 0" class="config-row">
                <span class="config-key">现金</span>
                <span class="config-val">{{ ((compositeConfig?.cash_weight ?? 0) * 100).toFixed(1) }}%</span>
              </div>
              <div class="config-row">
                <span class="config-key">权重再平衡</span>
                <span class="config-val">
                  {{ compositeConfig?.rebalance ? formatRebalanceSchedule(compositeConfig.rebalance) : '每日（恒定比例）' }}
                </span>
              </div>
            </div>
            <div class="config-desc">
              成员各自保留择时、风控与调仓日程；组合持仓按成员权重合成后统一记账，成员间方向相反的交易被轧差。
              组合策略不支持稳健性扫描、优化会话与研究批量评估，请对成员策略分别执行。
            </div>
          </div>
        </div>

        <template v-if="!isComposite">
        <!-- 分状态配置覆盖（regime_rules）：表单不编辑，但保存时原样保留 -->
        <div v-if="regimeRuleEntries.length > 0" class="config-card">
          <div class="config-header">分状态覆盖 (Regime Rules)</div>
          <div class="config-body">
            <div v-for="entry in regimeRuleEntries" :key="entry.regime" class="config-row">
              <span class="config-key">{{ entry.label }}状态</span>
              <span class="config-val mono regime-rule-json">{{ entry.text }}</span>
            </div>
            <div class="config-desc">对应择时状态下用这里的模块配置替换默认配置，未列出的模块沿用默认。</div>
          </div>
        </div>

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
                    <span class="factor-name">{{ factorLabel(String(fid)) }}</span>
                    <span class="factor-weight">{{ weight }}</span>
                  </span>
                </div>
              </div>
              <div v-if="scoreConfig.transforms && Object.keys(scoreConfig.transforms).length > 0" class="config-row">
                <span class="config-key">变换函数</span>
                <div class="factor-weights">
                  <span v-for="(fn, fid) in scoreConfig.transforms" :key="fid" class="factor-tag transform">
                    <span class="factor-name">{{ factorLabel(String(fid)) }}</span>
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
                <span class="config-key">综合方式</span>
                <span class="config-val">
                  {{ timingCombineText }}
                </span>
              </div>
              <template v-if="timingConfig.combine === 'rules'">
                <div v-for="group in timingRuleGroups" :key="group.regime" class="config-row">
                  <span class="config-key">{{ group.label }}规则</span>
                  <span class="config-val">{{ group.text }}</span>
                </div>
              </template>
              <div v-if="timingConfig.combine !== 'rules'" class="config-row">
                <span class="config-key">因子权重</span>
                <div class="factor-weights">
                  <span v-for="(weight, fid) in timingConfig.factors" :key="fid" class="factor-tag">
                    <span class="factor-name">{{ factorLabel(String(fid)) }}</span>
                    <span class="factor-weight">{{ weight }}</span>
                  </span>
                </div>
              </div>
              <div v-if="timingConfig.combine !== 'rules'" class="config-row">
                <span class="config-key">阈值</span>
                <span class="config-val">
                  进攻 ≥ {{ timingConfig.thresholds?.offensive ?? 65 }}，防守 ≤ {{ timingConfig.thresholds?.defensive ?? 35 }}
                </span>
              </div>
              <div
                v-if="timingConfig.combine !== 'rules' && timingConfig.transforms && Object.keys(timingConfig.transforms).length > 0"
                class="config-row"
              >
                <span class="config-key">变换函数</span>
                <div class="factor-weights">
                  <span v-for="(fn, fid) in timingConfig.transforms" :key="fid" class="factor-tag transform">
                    <span class="factor-name">{{ factorLabel(String(fid)) }}</span>
                    <span class="factor-weight">{{ fn }}</span>
                  </span>
                </div>
              </div>
              <div v-if="timingConfig.proxy_index_codes && timingConfig.proxy_index_codes.length > 0" class="config-row">
                <span class="config-key">代理指数</span>
                <span class="config-val">
                  {{ timingConfig.proxy_index_codes.join(', ') }}（优先取全部择时因子都有值的首个代理）
                </span>
              </div>
              <div v-if="timingConfig.factor_proxies && Object.keys(timingConfig.factor_proxies).length > 0" class="config-row">
                <span class="config-key">按因子代理</span>
                <div class="factor-weights">
                  <span v-for="(codes, fid) in timingConfig.factor_proxies" :key="fid" class="factor-tag">
                    <span class="factor-name">{{ factorLabel(String(fid)) }}</span>
                    <span class="factor-weight">{{ codes.join(', ') }}</span>
                  </span>
                </div>
              </div>
              <div v-for="tier in timingTierRows" :key="tier.index" class="config-row">
                <span class="config-key">仓位档位 {{ tier.index + 1 }}</span>
                <span class="config-val">{{ tier.text }}</span>
              </div>
              <div v-if="timingTierRows.length > 0" class="config-row">
                <span class="config-key">档位说明</span>
                <span class="config-val">
                  按顺序首个命中的档位决定总仓位（取代组合模块的 regime 仓位）；都未命中或择时降级时按 regime 仓位；
                  状态层沿用前一 regime 时同时沿用上一档位
                </span>
              </div>
              <div v-if="timingConfig.tier_base_exposure != null" class="config-row">
                <span class="config-key">动态档位起点</span>
                <span class="config-val">{{ (timingConfig.tier_base_exposure * 100).toFixed(0) }}%（维持 / 增减档位没有上一仓位时）</span>
              </div>
              <div v-for="cap in timingCapRows" :key="cap.index" class="config-row">
                <span class="config-key">仓位上限 {{ cap.index + 1 }}</span>
                <span class="config-val">{{ cap.text }}</span>
              </div>
              <div v-if="timingStateText" class="config-row">
                <span class="config-key">状态层</span>
                <span class="config-val">{{ timingStateText }}</span>
              </div>
              <div class="config-row">
                <span class="config-key">降级回退</span>
                <span class="config-val">{{ onDegradedLabel(timingConfig.on_degraded) }}（择时因子全部缺失时）</span>
              </div>
            </div>
            <div v-else class="config-empty">未配置（无择时）</div>
          </div>
        </div>

        <!-- 过滤模块 -->
        <div class="config-card">
          <div class="config-header">过滤模块 (Filter)</div>
          <div class="config-body">
            <div
              v-if="filterConfig && ((filterConfig.rules?.length ?? 0) > 0 || (filterConfig.groups?.length ?? 0) > 0)"
              class="config-section"
            >
              <div class="config-row">
                <span class="config-key">逻辑</span>
                <span class="config-val">{{ filterConfig.logic || 'AND' }}</span>
              </div>
              <div v-for="(rule, i) in filterConfig.rules" :key="i" class="config-row">
                <span class="config-key">规则 {{ i + 1 }}</span>
                <span class="config-val mono">
                  {{ factorLabel(rule.factor) }} {{ rule.op }}
                  {{ rule.compare_to ? factorLabel(rule.compare_to) : rule.value }}
                  <span v-if="rule.missing_strategy && rule.missing_strategy !== 'fail'">
                    （缺失:{{ rule.missing_strategy === 'pass' ? '通过' : '排除' }}）
                  </span>
                </span>
              </div>
              <div v-for="(group, i) in filterConfig.groups ?? []" :key="`g${i}`" class="config-row">
                <span class="config-key">子规则组 {{ i + 1 }}</span>
                <span class="config-val mono">{{ ruleGroupText(group) }}</span>
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
                <span class="config-val">综合得分 {{ rankConfig.order === 'asc' ? '升序' : '降序' }}</span>
              </div>
              <div v-if="rankConfig.top_n" class="config-row">
                <span class="config-key">Top N</span>
                <span class="config-val">{{ rankConfig.top_n }}</span>
              </div>
              <div v-if="rankConfig.bottom_n" class="config-row">
                <span class="config-key">Bottom N</span>
                <span class="config-val">{{ rankConfig.bottom_n }}</span>
              </div>
              <div v-if="rankConfig.buffer_n" class="config-row">
                <span class="config-key">缓冲带</span>
                <span class="config-val">老持仓排名 ≤ {{ (rankConfig.top_n ?? 0) + rankConfig.buffer_n }} 时保留</span>
              </div>
              <div v-if="rankConfig.momentum_factor" class="config-row">
                <span class="config-key">动量子因子</span>
                <span class="config-val mono">{{ factorLabel(rankConfig.momentum_factor) }}</span>
              </div>
              <div v-if="rankConfig.valuation_factor" class="config-row">
                <span class="config-key">估值子因子</span>
                <span class="config-val mono">{{ factorLabel(rankConfig.valuation_factor) }}</span>
              </div>
            </div>
            <div v-else class="config-empty">默认排名</div>
          </div>
        </div>

        <!-- 因子别名声明 -->
        <div v-if="Object.keys(factorAliasMap).length > 0" class="config-card">
          <div class="config-header">因子别名声明 (factor_aliases)</div>
          <div class="config-body">
            <div class="config-section">
              <div v-for="(declaration, alias) in factorAliasMap" :key="alias" class="config-row">
                <span class="config-key mono">{{ alias }}</span>
                <span class="config-val mono">
                  {{ declaration.template_id }}
                  <template v-if="Object.keys(declaration.params ?? {}).length > 0">
                    ({{ formatParams(declaration.params) }})
                  </template>
                </span>
              </div>
            </div>
            <div class="config-desc">别名承载策略语义，模板 ID 与参数承载计算语义；未在此声明的引用按模板 ID 与默认参数解释。</div>
          </div>
        </div>

        <!-- 组合模块 -->
        <div class="config-card">
          <div class="config-header">组合模块 (Portfolio)</div>
          <div class="config-body">
            <div class="config-section">
              <div class="config-row">
                <span class="config-key">分配方法</span>
                <span class="config-val">{{ portfolioMethodLabel(portfolioConfig.method) }}</span>
              </div>
              <div v-if="portfolioConfig.method === 'inverse_vol'" class="config-row">
                <span class="config-key">波动率因子</span>
                <span class="config-val mono">{{ factorLabel(portfolioConfig.vol_factor ?? '') }}</span>
              </div>
              <div v-if="portfolioConfig.method === 'risk_parity'" class="config-row">
                <span class="config-key">协方差窗口</span>
                <span class="config-val">{{ portfolioConfig.cov_window ?? 60 }} 个交易日（历史不足时退回等权）</span>
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
              <div v-if="timingConfig && missingExposureRegimes.length > 0" class="config-row">
                <span class="config-key">缺档提示</span>
                <span class="config-val config-warn">
                  未配置 {{ missingExposureRegimes.join(' / ') }}：这些 regime 回退到默认仓位
                  {{ ((portfolioConfig.default_exposure ?? 0.5) * 100).toFixed(0) }}%，而不是默认三档表，建议三档写全
                </span>
              </div>
              <div v-if="portfolioConfig.default_exposure != null" class="config-row">
                <span class="config-key">默认仓位</span>
                <span class="config-val">{{ (portfolioConfig.default_exposure * 100).toFixed(0) }}%（无择时信号时）</span>
              </div>
            </div>
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
              <div v-if="riskConfig.vol_target" class="config-row">
                <span class="config-key">波动率目标</span>
                <span class="config-val">
                  年化 {{ (riskConfig.vol_target.target * 100).toFixed(1) }}%（{{ riskConfig.vol_target.window ?? 60 }} 日协方差，只降不加<template v-if="riskConfig.vol_target.floor">，缩放后仓位不低于 {{ (riskConfig.vol_target.floor * 100).toFixed(1) }}%</template>）
                </span>
              </div>
              <div v-if="riskConfig.drawdown_stop" class="config-row">
                <span class="config-key">回撤止损</span>
                <span class="config-val">
                  回撤 {{ (riskConfig.drawdown_stop.threshold * 100).toFixed(1) }}% →
                  仓位 ≤ {{ ((riskConfig.drawdown_stop.exposure ?? 0) * 100).toFixed(0) }}%，
                  冷却 {{ riskConfig.drawdown_stop.cooldown_days ?? 20 }} 日
                </span>
              </div>
              <div v-if="riskConfig.trailing_stop" class="config-row">
                <span class="config-key">移动止损</span>
                <span class="config-val">
                  自入场高点回撤 {{ (riskConfig.trailing_stop.threshold * 100).toFixed(1) }}% 清出，
                  冷却 {{ riskConfig.trailing_stop.cooldown_days ?? 20 }} 日
                </span>
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
                <span class="config-key">选股腿（重建成分）</span>
                <span class="config-val">{{ formatRebalanceSchedule(rebalanceConfig.selection || rebalanceConfig) }}</span>
              </div>
              <div class="config-row">
                <span class="config-key">风险腿（等比调仓位）</span>
                <span class="config-val">{{ formatRebalanceSchedule(rebalanceConfig.risk || rebalanceConfig.selection || rebalanceConfig) }}</span>
              </div>
              <div v-if="legsAreIdentical" class="config-row">
                <span class="config-key">口径</span>
                <span class="config-val">两腿同频：每个调仓日重建成分并应用择时目标仓位（等价于单腿口径）</span>
              </div>
              <div v-else class="config-row">
                <span class="config-key">口径</span>
                <span class="config-val">两腿异频：选股腿日只换成分、维持当前总仓位；总仓位仅在风险腿日按择时目标调整</span>
              </div>
              <div v-if="rebalanceConfig.band" class="config-row">
                <span class="config-key">偏离阈值</span>
                <span class="config-val">
                  <template v-if="rebalanceConfig.band.min_turnover != null">
                    换手 &lt; {{ (rebalanceConfig.band.min_turnover * 100).toFixed(1) }}% 跳过；
                  </template>
                  <template v-if="rebalanceConfig.band.max_drift != null">
                    偏离 &gt; {{ (rebalanceConfig.band.max_drift * 100).toFixed(1) }}% 时回到最近目标
                  </template>
                </span>
              </div>
            </div>
            <div v-else class="config-empty">未配置（默认每日调仓，两腿同频）</div>
          </div>
        </div>
        </template>

      </div>

      <!-- 稳健性验证面板：批次列表 + 邻域 Δ / 消融边际贡献 / PBO / DSR -->
      <div v-else class="robustness-panel">
        <div class="section-header-row">
          <h2 class="section-title">稳健性验证</h2>
          <div class="header-actions">
            <button class="btn-secondary" :disabled="robustnessLoading" @click="loadRobustness">
              {{ robustnessLoading ? '加载中...' : '刷新' }}
            </button>
          </div>
        </div>
        <p class="robustness-hint">
          候选集级别的过拟合风险检验（单次回测无法计算）。批次由 CLI 创建：
          <code>robustness scan --strategy {{ strategyId }} --preset quick</code>；
          汇总后 <code>robustness collect &lt;id&gt;</code> 与
          <code>robustness stats &lt;id&gt;</code> 给出邻域与显著性结论。
        </p>
        <div v-if="robustnessError" class="run-error">{{ robustnessError }}</div>
        <div v-if="!robustnessLoading && !robustnessRuns.length" class="config-empty">
          暂无稳健性验证批次
        </div>

        <table v-if="robustnessRuns.length" class="robust-table">
          <thead>
            <tr>
              <th>批次</th>
              <th>类型</th>
              <th>状态</th>
              <th>试验数</th>
              <th title="不同执行模型的批次结果不可直接比较">执行模型</th>
              <th>窗口</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="run in robustnessRuns"
              :key="run.robustness_id"
              :class="{ 'row-selected': selectedRun?.robustness_id === run.robustness_id }"
            >
              <td class="mono">{{ run.robustness_id.slice(0, 8) }}</td>
              <td>{{ kindLabel(run.kind) }}</td>
              <td>
                <span :class="['chip', statusChipClass(run.status)]">
                  {{ statusText(run.status) }}
                </span>
                <span v-if="run.is_stale" class="chip chip-disabled" title="长时间没有进展，可用 robustness abandon 收口">
                  疑似停滞
                </span>
              </td>
              <td>{{ run.trial_count }}</td>
              <td>{{ executionModelLabel(run.execution_model) }}</td>
              <td class="mono">{{ run.start_date }} ~ {{ run.end_date }}</td>
              <td>{{ formatCnTime(run.created_at) }}</td>
              <td class="row-actions">
                <button class="btn-link" @click="selectRun(run.robustness_id)">查看</button>
                <button
                  v-if="run.status === 'running'"
                  class="btn-link danger"
                  @click="handleCancelRun(run.robustness_id)"
                >
                  取消
                </button>
                <button
                  v-if="run.status === 'running' || run.status === 'partial'"
                  class="btn-link danger"
                  @click="handleAbandonRun(run.robustness_id)"
                >
                  作废
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <div v-if="selectedRun" class="robust-detail">
          <h3 class="robust-detail-title">
            批次 {{ selectedRun.robustness_id.slice(0, 8) }}（{{ kindLabel(selectedRun.kind) }}）
          </h3>

          <div v-if="selectedRun.scan_params?.preset" class="stat-chip chip-total">
            扫描口径：{{ selectedRun.scan_params.preset }}
            <template v-if="Array.isArray(selectedRun.scan_params.knobs)">
              · 关键旋钮 {{ (selectedRun.scan_params.knobs as unknown[]).length }} 个
            </template>
          </div>

          <div v-if="coverageOf(selectedRun)" class="robust-block">
            <div class="robust-block-title">覆盖率</div>
            <div class="robust-kv">
              <span>完整 {{ coverageOf(selectedRun)?.completed_windows }} /
                {{ coverageOf(selectedRun)?.expected_windows }} 窗口</span>
              <span v-if="coverageOf(selectedRun)?.is_partial" class="chip chip-disabled">
                部分汇总（结论仅供参考）
              </span>
              <span v-if="coverageOf(selectedRun)?.comparable === false" class="chip chip-warn">
                各变体没有共同窗口：Δ 不可比较
              </span>
            </div>
          </div>

          <!-- 参数邻域（scan） -->
          <div v-if="selectedRun.summary?.neighborhood" class="robust-block">
            <div class="robust-block-title">参数邻域稳定度（毛口径）</div>
            <div class="robust-kv">
              <span>扰动变体 {{ selectedRun.summary.neighborhood.n_variants }} 个</span>
              <span>Δ夏普范围 {{ fmt(selectedRun.summary.neighborhood.delta_min) }} ~
                {{ fmt(selectedRun.summary.neighborhood.delta_max) }}</span>
              <span>劣于基线占比 {{ fmtRatio(selectedRun.summary.neighborhood.worse_ratio) }}</span>
              <span
                v-if="selectedRun.summary.neighborhood.is_plateau === null"
                class="chip chip-disabled"
              >
                有效变体不足，无法判定
              </span>
              <span
                v-else
                :class="['chip', selectedRun.summary.neighborhood.is_plateau ? 'chip-active' : 'chip-disabled']"
              >
                {{ selectedRun.summary.neighborhood.is_plateau ? '处于平台' : '非平台（参数脆弱）' }}
              </span>
              <span v-if="selectedRun.summary.neighborhood.reversal" class="chip chip-disabled">
                存在方向反转
              </span>
            </div>
          </div>

          <!-- 因子消融（ablate） -->
          <div v-if="selectedRun.summary?.marginal?.length" class="robust-block">
            <div class="robust-block-title">因子／过滤条件边际贡献（Δ夏普，升序，毛口径）</div>
            <table class="robust-table inner">
              <thead>
                <tr><th>移除项</th><th>Δ夏普</th><th>Δ年化(%)</th></tr>
              </thead>
              <tbody>
                <tr v-for="item in selectedRun.summary.marginal" :key="item.label">
                  <td class="mono">{{ item.label }}</td>
                  <td>{{ fmt(item.delta_sharpe) }}</td>
                  <td>{{ fmt(item.delta_annualized_return) }}</td>
                </tr>
              </tbody>
            </table>
            <p class="robustness-hint">Δ 接近 0 的项对结果没有贡献，可考虑删除以降低复杂度。</p>
          </div>

          <!-- 资产池扰动（pool） -->
          <div v-if="selectedRun.summary?.pool" class="robust-block">
            <div class="robust-block-title">资产池扰动分布（毛口径）</div>
            <div class="robust-kv">
              <span>子池变体 {{ selectedRun.summary.pool.n_variants }} 个</span>
              <span>中位 Δ夏普 {{ fmt(selectedRun.summary.pool.delta_median) }}</span>
              <span>范围 {{ fmt(selectedRun.summary.pool.delta_min) }} ~
                {{ fmt(selectedRun.summary.pool.delta_max) }}</span>
            </div>
          </div>

          <!-- 统计显著性 -->
          <div v-if="selectedRun.statistics" class="robust-block">
            <div class="robust-block-title">
              统计显著性（试验次数 N = {{ selectedRun.statistics.n_trials }}）
            </div>
            <div class="robust-kv">
              <span v-if="selectedRun.statistics.pbo && selectedRun.statistics.pbo.value !== null">
                CSCV-PBO：<b class="mono">{{ selectedRun.statistics.pbo.value }}</b>
                （≈0.5 相当于纯噪声，越高越可疑）
              </span>
              <span v-else class="text-muted">
                PBO：{{ selectedRun.statistics.pbo?.reason || '窗口数或候选数不足，未计算' }}
              </span>
            </div>
            <div class="robust-kv">
              <span v-if="selectedRun.statistics.deflated_sharpe">
                基线 Deflated Sharpe（{{ selectedRun.statistics.cost_bps }}bp 净口径）：<b class="mono">{{ selectedRun.statistics.deflated_sharpe.deflated_sharpe }}</b>
                （年化夏普 {{ selectedRun.statistics.deflated_sharpe.sharpe_annualized }}，
                期望最大夏普 {{ selectedRun.statistics.deflated_sharpe.expected_max_sharpe_annualized }}）
              </span>
              <span v-else class="text-muted">Deflated Sharpe：未计算</span>
            </div>
            <div class="robust-kv">
              <span v-if="selectedRun.statistics.bootstrap">
                自助法 {{ selectedRun.statistics.bootstrap.confidence * 100 }}% 置信区间：
                [{{ selectedRun.statistics.bootstrap.lower }},
                {{ selectedRun.statistics.bootstrap.upper }}]
              </span>
              <span v-else class="text-muted">自助法区间：未计算</span>
            </div>
          </div>

          <!-- 变体逐个结果 -->
          <div v-if="selectedRun.summary?.variants?.length" class="robust-block">
            <div class="robust-block-title">变体逐项结果（相对基线，毛口径）</div>
            <table class="robust-table inner">
              <thead>
                <tr>
                  <th>变体</th>
                  <th>扰动项</th>
                  <th>均值夏普</th>
                  <th>Δ夏普</th>
                  <th>Δ年化(%)</th>
                  <th>窗口数</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in selectedRun.summary.variants" :key="item.label">
                  <td>{{ item.label }}</td>
                  <td class="mono">{{ item.knob || '-' }}</td>
                  <td>{{ fmt(item.sharpe_mean) }}</td>
                  <td>{{ fmt(item.delta_sharpe) }}</td>
                  <td>{{ fmt(item.delta_annualized_return) }}</td>
                  <td>{{ item.windows }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="config-empty">该批次尚未汇总（collect 后可查看结果）</div>
        </div>
      </div>

      <!-- 执行错误提示 -->
      <div v-if="runError" class="run-error">{{ runError }}</div>
      <!-- 决策管线结果 -->
      <div v-if="allocation" class="allocation-section">
        <div class="section-header-row">
          <h2 class="section-title">决策管线结果</h2>
          <span v-if="allocation.data_date" class="data-date-tag">数据日期：{{ allocation.data_date }}</span>
        </div>

        <!-- 概览条 -->
        <div class="alloc-summary-bar">
          <span v-if="allocation.timing?.regime" class="summary-item">
            <span class="summary-label">择时</span>
            <span :class="['regime-tag-sm', `regime-${allocation.timing.regime}`]">
              {{ regimeLabel(allocation.timing.regime) }}
            </span>
          </span>
          <span class="summary-item">
            <span class="summary-label">标的</span>
            <span v-if="allocation.rankings?.length" class="summary-val-accent">{{ allocation.rankings.length }} 个</span>
            <span v-else class="summary-val-muted">无标的通过筛选</span>
          </span>
          <span class="summary-item">
            <span class="summary-label">仓位</span>
            <span v-if="allocation.plan?.total_exposure > 0" class="summary-val-accent">{{ (allocation.plan.total_exposure * 100).toFixed(1) }}%</span>
            <span v-else class="summary-val-muted">目标仓位 0</span>
          </span>
          <span v-if="allocation.plan?.method" class="summary-item">
            <span class="summary-label">方法</span>
            <span class="summary-val">{{ portfolioMethodLabel(allocation.plan.method) }}</span>
          </span>
        </div>

        <!-- 择时信号明细 -->
        <div v-if="allocation.timing && allocation.timing.regime" class="alloc-card">
          <div class="alloc-header">择时信号</div>
          <div class="alloc-body">
            <div class="alloc-row">
              <span class="alloc-key">Regime</span>
              <span :class="['regime-tag', `regime-${allocation.timing.regime}`]">
                {{ regimeLabel(allocation.timing.regime) }}
              </span>
            </div>
            <div class="alloc-row">
              <span class="alloc-key">确信度</span>
              <span class="alloc-val">{{ (allocation.timing.confidence ?? 0).toFixed(0) }}%</span>
            </div>
            <div v-if="allocation.timing.label" class="alloc-row">
              <span class="alloc-key">标签</span>
              <span class="alloc-val">{{ allocation.timing.label }}</span>
            </div>
            <div v-if="allocation.timing.tier != null && allocation.timing.exposure != null" class="alloc-row">
              <span class="alloc-key">仓位档位</span>
              <span class="alloc-val">
                {{ allocation.timing.tier_label }}（档位仓位 {{ (allocation.timing.exposure * 100).toFixed(0) }}%）
              </span>
            </div>
            <div v-if="allocation.timing.exposure_cap != null" class="alloc-row">
              <span class="alloc-key">条件上限</span>
              <span class="alloc-val">
                {{ allocation.timing.exposure_cap_label }}：总仓位不超过 {{ (allocation.timing.exposure_cap * 100).toFixed(0) }}%
              </span>
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
                <tr v-for="(r, i) in allocation.rankings" :key="r.index_code">
                  <td>{{ i + 1 }}</td>
                  <td class="mono">{{ r.index_code }}</td>
                  <td>{{ r.name_cn }}</td>
                  <td class="mono">{{ r.score?.toFixed(1) }}</td>
                  <td>{{ r.category || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- 排名为空：展示原因 -->
        <div v-else class="alloc-empty-hint">
          <span class="empty-icon">📋</span>
          <p>当前无资产通过策略筛选条件</p>
          <p class="hint-sub">所有标的被过滤规则剔除，或因子数据尚未就绪</p>
        </div>

        <!-- 路径依赖回放状态 -->
        <div v-if="allocation.plan?.path_state" class="alloc-card">
          <div class="alloc-header">路径状态（回放重建）</div>
          <div class="alloc-body">
            <div class="alloc-row">
              <span class="alloc-key">回放起点</span>
              <span class="alloc-val">{{ allocation.plan.path_state.replay_start }}（结果取决于回放起点）</span>
            </div>
            <div class="alloc-row">
              <span class="alloc-key">调仓日</span>
              <span class="alloc-val">
                {{ allocation.plan.path_state.rebalance_due ? '今日为调仓日（下方为模型目标持仓）' : '今日不调仓（下方为回放得到的模型持仓）' }}
                <template v-if="allocation.plan.path_state.reason">· {{ pathReasonLabel(allocation.plan.path_state.reason) }}</template>
              </span>
            </div>
            <div v-if="allocation.plan.path_state.drawdown_stop_days_left" class="alloc-row">
              <span class="alloc-key">回撤止损</span>
              <span class="alloc-val">止损中，剩余 {{ allocation.plan.path_state.drawdown_stop_days_left }} 个决策日</span>
            </div>
            <div
              v-if="Object.keys(allocation.plan.path_state.trailing_stop_cooldowns ?? {}).length > 0"
              class="alloc-row"
            >
              <span class="alloc-key">移动止损冷却</span>
              <span class="alloc-val">
                <span v-for="(days, code) in allocation.plan.path_state.trailing_stop_cooldowns" :key="code">
                  {{ code }}（{{ days }} 日）
                </span>
              </span>
            </div>
          </div>
        </div>

        <!-- 组合策略成员 -->
        <div v-if="allocation.plan?.members?.length" class="alloc-card">
          <div class="alloc-header">组合成员</div>
          <div class="alloc-body">
            <div v-for="member in allocation.plan.members" :key="member.strategy_id" class="alloc-row">
              <span class="alloc-key mono">{{ member.strategy_id }}（{{ (member.weight * 100).toFixed(0) }}%）</span>
              <span class="alloc-val">
                <span v-for="(w, code) in member.positions" :key="code">{{ code }} {{ (w * 100).toFixed(1) }}% </span>
                <template v-if="Object.keys(member.positions).length === 0">空仓</template>
              </span>
            </div>
          </div>
        </div>

        <!-- 仓位分配 -->
        <div v-if="allocation.plan && Object.keys(allocation.plan.positions ?? {}).length > 0" class="alloc-card">
          <div class="alloc-header">仓位分配</div>
          <div class="alloc-body">
            <div class="position-tags">
              <span v-for="(w, code) in allocation.plan.positions" :key="code" class="position-tag">
                <span class="pos-code">{{ code }}</span>
                <span class="pos-weight">{{ (w * 100).toFixed(1) }}%</span>
              </span>
            </div>
          </div>
        </div>

        <!-- 仓位为空：展示空仓说明 -->
        <div v-else class="alloc-empty-hint">
          <span class="empty-icon">💤</span>
          <p>模型目标仓位为 0</p>
          <p class="hint-sub">该交易日策略管线没有产出非零持仓（研究输出，不构成交易建议）</p>
        </div>
      </div>

      <!-- 管线调试详情 -->
      <div v-if="allocation?.pipeline_detail" class="debug-section">
        <h2 class="section-title">管线调试详情</h2>

        <!-- Tab 切换 -->
        <div class="debug-tabs">
          <button
            v-for="tab in debugTabs"
            :key="tab.key"
            :class="['debug-tab', { active: activeDebugTab === tab.key }]"
            @click="activeDebugTab = tab.key"
          >
            {{ tab.label }}
            <span v-if="tab.count != null" class="tab-count">{{ tab.count }}</span>
          </button>
        </div>

        <!-- 评分明细 Tab -->
        <div v-if="activeDebugTab === 'scoring'" class="debug-panel">
          <!-- 横截面统计 -->
          <div v-if="allocation.pipeline_detail.cross_section_stats" class="debug-stat-bar">
            <span v-for="(val, key) in allocation.pipeline_detail.cross_section_stats" :key="key" class="stat-chip">
              <template v-if="key === 'count'">资产数 </template>
              <template v-else-if="key === 'mean'">均值 </template>
              <template v-else-if="key === 'std'">标准差 </template>
              <template v-else-if="key === 'min'">最低 </template>
              <template v-else-if="key === 'max'">最高 </template>
              <template v-else-if="key === 'scoring_mode'">模式 </template>
              <template v-else>{{ key }} </template>
              {{ typeof val === 'number' ? val.toFixed(2) : val }}
            </span>
          </div>

          <details
            v-for="asset in sortedScoringDetails"
            :key="asset.index_code"
            class="debug-asset"
            :class="{ excluded: asset.excluded }"
          >
            <summary class="debug-asset-header">
              <span class="debug-code mono">{{ asset.index_code }}</span>
              <span class="debug-name">{{ asset.name_cn }}</span>
              <span v-if="asset.excluded" class="debug-excluded-tag">已排除</span>
              <span v-else-if="asset.final_score != null" class="debug-final-score">{{ asset.final_score.toFixed(1) }}</span>
              <span v-else class="debug-na">-</span>
              <span v-if="asset.excluded" class="debug-exclude-reason">{{ asset.exclude_reason }}</span>
            </summary>
            <div v-if="asset.factors.length > 0" class="debug-asset-body">
              <table class="debug-factor-table">
                <thead>
                  <tr>
                    <th>因子</th>
                    <th>原始值</th>
                    <th>变换后</th>
                    <th>权重</th>
                    <th>贡献</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="f in asset.factors" :key="f.factor_id" :class="`factor-status-${f.status}`">
                    <td class="mono">{{ f.factor_id }}</td>
                    <td class="mono">{{ f.raw_value != null ? (typeof f.raw_value === 'number' ? f.raw_value.toFixed(2) : f.raw_value) : '-' }}</td>
                    <td class="mono">{{ f.transformed_value != null ? (typeof f.transformed_value === 'number' ? f.transformed_value.toFixed(1) : f.transformed_value) : '-' }}</td>
                    <td class="mono">{{ f.weight }}</td>
                    <td class="mono">{{ f.contribution.toFixed(2) }}</td>
                    <td>
                      <span :class="['status-badge', `status-${f.status}`]">
                        {{ statusLabel(f.status) }}
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
              <div v-if="asset.raw_score != null" class="debug-raw-score">
                原始得分: {{ asset.raw_score.toFixed(2) }}
                <span v-if="asset.raw_score !== asset.final_score && asset.final_score != null">
                  → 归一化: {{ asset.final_score.toFixed(1) }}
                </span>
              </div>
            </div>
            <div v-else class="debug-empty">无因子数据</div>
          </details>
        </div>

        <!-- 过滤明细 Tab -->
        <div v-if="activeDebugTab === 'filter'" class="debug-panel">
          <div v-if="allocation.pipeline_detail.filter_results && allocation.pipeline_detail.filter_results.length > 0">
            <!-- 过滤汇总 -->
            <div class="debug-stat-bar">
              <span class="stat-chip">
                通过 {{ allocation.pipeline_detail.filter_results.filter(a => a.passed).length }}
              </span>
              <span class="stat-chip chip-fail">
                未通过 {{ allocation.pipeline_detail.filter_results.filter(a => !a.passed).length }}
              </span>
              <span class="stat-chip chip-total">
                总计 {{ allocation.pipeline_detail.filter_results.length }}
              </span>
            </div>

            <details
              v-for="asset in allocation.pipeline_detail.filter_results"
              :key="asset.index_code"
              class="debug-asset"
              :class="{ excluded: !asset.passed }"
            >
              <summary class="debug-asset-header">
                <span class="debug-code mono">{{ asset.index_code }}</span>
                <span class="debug-name">{{ asset.name_cn }}</span>
                <span v-if="asset.passed" class="debug-passed-tag">通过</span>
                <span v-else class="debug-failed-tag">未通过</span>
                <span v-if="!asset.passed" class="debug-exclude-reason">{{ asset.fail_reason }}</span>
              </summary>
              <div class="debug-asset-body">
                <table class="debug-factor-table">
                  <thead>
                    <tr>
                      <th>规则</th>
                      <th>因子</th>
                      <th>操作符</th>
                      <th>阈值</th>
                      <th>因子值</th>
                      <th>结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="r in asset.rule_results" :key="r.rule_index" :class="{ 'rule-fail': !r.passed }">
                      <td>{{ r.rule_index + 1 }}</td>
                      <td class="mono">{{ r.factor }}</td>
                      <td class="mono">{{ r.op }}</td>
                      <td class="mono">{{ formatThreshold(r.threshold) }}</td>
                      <td class="mono">{{ r.factor_value != null ? (typeof r.factor_value === 'number' ? r.factor_value.toFixed(2) : r.factor_value) : '-' }}</td>
                      <td>
                        <span :class="r.passed ? 'result-pass' : 'result-fail'">
                          {{ r.passed ? '✓ 通过' : '✗ 未通过' }}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </details>
          </div>
          <div v-else class="debug-empty-hint">无过滤规则配置</div>
        </div>

        <!-- 择时明细 Tab -->
        <div v-if="activeDebugTab === 'timing'" class="debug-panel">
          <div v-if="allocation.pipeline_detail.timing_detail && Object.keys(allocation.pipeline_detail.timing_detail).length > 0">
            <div v-if="timingSummary" class="timing-summary">
              <span v-if="timingSummary.composite != null">综合分 {{ timingSummary.composite }}</span>
              <span v-if="timingSummary.proxyText">代理：{{ timingSummary.proxyText }}</span>
              <span v-if="timingSummary.stateText">{{ timingSummary.stateText }}</span>
              <span v-if="timingSummary.rulesText">{{ timingSummary.rulesText }}</span>
              <span v-if="timingSummary.tierText">{{ timingSummary.tierText }}</span>
              <span v-if="timingSummary.degraded && timingSummary.degradedText" class="config-warn">
                {{ timingSummary.degradedText }}
              </span>
            </div>
            <table class="debug-factor-table">
              <thead>
                <tr>
                  <th>因子/指标</th>
                  <th>原始值</th>
                  <th>变换后</th>
                  <th>权重</th>
                  <th>代理</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="[fid, detail] in timingFactorRows" :key="fid">
                  <td class="mono">{{ fid }}</td>
                  <td class="mono">{{ detail.raw != null ? (typeof detail.raw === 'number' ? detail.raw.toFixed(2) : detail.raw) : '-' }}</td>
                  <td class="mono">{{ detail.transformed != null ? detail.transformed : '-' }}</td>
                  <td class="mono">{{ detail.weight != null ? detail.weight : '-' }}</td>
                  <td class="mono">{{ detail.proxy ?? '-' }}</td>
                  <td>
                    <span :class="['status-badge', detail.status === 'missing' ? 'status-missing_ignored' : 'status-ok']">
                      {{ detail.status === 'missing' ? '缺失' : '正常' }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="debug-empty-hint">无择时信号数据</div>
        </div>
      </div>

      <!-- 标的K线（决策日期前后：前60后20个交易日） -->
      <div v-if="allocation && allocation.rankings && allocation.rankings.length > 0" class="kline-section">
        <h2 class="section-title">标的K线（决策日 {{ decisionDate }} 前后）</h2>
        <div v-if="chartsLoading" class="chart-placeholder">加载K线数据中...</div>
        <template v-else>
          <div v-for="(item, i) in allocation.rankings" :key="item.index_code" class="kline-card">
            <div class="kline-header-bar">
              <span class="kline-rank">#{{ i + 1 }}</span>
              <span class="kline-code mono">{{ item.index_code }}</span>
              <span class="kline-name">{{ item.name_cn }}</span>
              <span class="kline-score">得分 {{ (item.score ?? 0).toFixed(1) }}</span>
            </div>
            <div v-if="!chartsData[item.index_code] || chartsData[item.index_code].length === 0" class="chart-placeholder chart-placeholder-sm">
              暂无K线数据
            </div>
            <div v-else :ref="(el: unknown) => { if (el) setChartRef(item.index_code, el as HTMLElement) }" class="kline-chart"></div>
          </div>
        </template>
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

            <div v-if="isLive" class="live-freeze-tip">
              该策略上线中，配置已冻结：本次保存只更新名称、描述与频率，不提交 config_json。
              需要修改配置请先在生命周期页退役，或派生新策略。
            </div>
            <CompositeStrategyForm
              v-if="isComposite && !editAdvancedMode"
              v-model="editConfigJson"
              :self-id="strategyId"
            />
            <StrategyConfigForm v-else-if="!editAdvancedMode" v-model="editConfigJson" />

            <template v-else>
              <textarea v-model="editConfigText" class="form-textarea mono" rows="14"></textarea>
              <div v-if="editJsonError" class="form-error">{{ editJsonError }}</div>
            </template>
          </div>
          <div v-if="store.actionError" class="form-error">{{ store.actionError }}</div>
          <div class="modal-actions">
            <button class="btn-secondary" @click="showEdit = false">取消</button>
            <button class="btn-primary" @click="handleUpdate" :disabled="store.saving">
              {{ store.saving ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>
      </div>
    </template>
    <div v-else-if="!store.loading" class="loading">策略不存在</div>
  </div>
</template>

<script setup lang="ts">
import { getErrorMessage } from '../api/client'
/**
 * 策略详情页面。
 *
 * 展示策略配置的各模块详情，支持编辑和删除操作。
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { fetchFactorSpecs } from '../api/factors'
import { fetchIndexDailyBars } from '../api/market_data'
import {
  abandonRobustnessRun,
  cancelRobustnessRun,
  fetchRobustnessDetail,
  fetchRobustnessRuns,
} from '../api/robustness'
import { clearStrategyResearchData, runAllocation } from '../api/strategies'
import StrategyConfigForm from '../components/StrategyConfigForm.vue'
import CompositeStrategyForm from '../components/CompositeStrategyForm.vue'
import { useStrategyStore } from '../stores/strategies'
import { toast } from '../stores/toast'
import type {
  AllocationResponse,
  CompositeConfigJson,
  DailyBar,
  FactorSpec,
  RobustnessCoverage,
  RobustnessDetail,
  RobustnessSummary,
  TimingConfigJson,
  TimingRuleGroupJson,
  TimingTierJson,
  TimingDetailValue,
  TimingFactorDetail,
  TimingOnDegraded,
} from '../types/api'
import { formatCnTime, shiftDateCn, todayCn } from '../utils/date'
import { CHART_THEME, loadEcharts } from '../utils/echarts'
import {
  executionModelLabel,
  frequencyLabel,
  REGIME_LABELS,
  regimeLabel,
  strategyStatusLabel,
} from '../utils/labels'
import { fetchLifecycles } from '../api/lifecycle'

const props = defineProps<{ strategyId: string }>()
const store = useStrategyStore()
const router = useRouter()

/** store.current 是否就是当前路由的策略（避免切换策略时短暂显示上一个策略） */
const currentMatches = computed(() => store.current?.strategy_id === props.strategyId)

/** 是否处于上线中（LIVE）：上线中的策略禁止直接更新 config_json */
const isLive = ref(false)

/** 读取生命周期状态；失败时按未上线处理（后端仍会拒绝对 LIVE 策略改配置） */
async function loadLiveStatus(): Promise<void> {
  try {
    const items = await fetchLifecycles()
    isLive.value = items.some(item => item.strategy_id === props.strategyId && item.lifecycle_status === 'LIVE')
  } catch {
    isLive.value = false
  }
}

/** 因子模板列表（用于把引用名解析成可读模板名） */
const availableFactors = ref<FactorSpec[]>([])

/** 今天日期字符串，用于日期选择器上限 */
const todayStr = todayCn()
/** 用户选择的交易日，默认今天 */
const selectedDate = ref(todayStr)
const allocating = ref(false)
const clearingResearchData = ref(false)
const runError = ref('')
const allocation = ref<AllocationResponse | null>(null)

/** 决策日期（优先用后端返回的 data_date，其次用用户选择的日期） */
const decisionDate = computed(() => allocation.value?.data_date || selectedDate.value)

/** K线图表 */
const chartsLoading = ref(false)
const chartsData = ref<Record<string, DailyBar[]>>({})
const chartInstances = new Map<string, unknown>()
/** 各标的 K 线容器的尺寸监听（窗口缩放时 resize） */
let chartResizeObserver: ResizeObserver | null = null
const chartRefs: Record<string, HTMLElement> = {}

/** 设置图表容器引用 */
function setChartRef(code: string, el: HTMLElement): void {
  chartRefs[code] = el
}

/** 根据决策日期计算起止日期（前60后20个交易日 ≈ 前90后30个自然日） */
function calcDateRange(centerDate: string): { startDate: string; endDate: string } {
  return {
    startDate: shiftDateCn(centerDate, -90),
    endDate: shiftDateCn(centerDate, 30),
  }
}

const showEdit = ref(false)
const editJsonError = ref('')
const editAdvancedMode = ref(false)
const editForm = ref({ display_name: '', description: '', frequency: 'daily' })
const editConfigJson = ref<Record<string, unknown>>({})
const editConfigText = ref('')

/** 从 config_json 中提取各模块配置 */
const configJson = computed(() => store.current?.config_json ?? {})
const indexCodesList = computed(() => store.current?.index_codes ?? [])
const scoreConfig = computed(() => configJson.value.score as { factors?: Record<string, number>; transforms?: Record<string, string>; missing_factor_strategy?: string; scoring_mode?: string } | undefined)
const timingConfig = computed(() => configJson.value.timing as TimingConfigJson | undefined)

/** 择时降级回退策略的展示文案（缺省为 neutral） */
function onDegradedLabel(policy: TimingOnDegraded | undefined): string {
  if (policy === 'defensive') return '按防守'
  if (policy === 'hold_previous') return '沿用上一决策日（实时回放最近交易日得到）'
  return '按中性（默认）'
}
const filterConfig = computed(() => configJson.value.filters as { logic?: string; rules?: Array<{ factor: string; op: string; value?: number | number[]; compare_to?: string; missing_strategy?: string }>; groups?: TimingRuleGroupJson[] } | undefined)
const rankConfig = computed(() => configJson.value.rank as { order?: string; top_n?: number; bottom_n?: number; momentum_factor?: string; valuation_factor?: string; buffer_n?: number } | undefined)
/** 是否为组合策略（kind=composite） */
const isComposite = computed(() => configJson.value.kind === 'composite')
const compositeConfig = computed(() => (isComposite.value ? (configJson.value as unknown as CompositeConfigJson) : null))

/** 仓位分配方法的中文名 */
function portfolioMethodLabel(method: string | undefined): string {
  const labels: Record<string, string> = {
    equal_weight: '等权',
    score_weight: '得分加权',
    winner_take_all: '赢家通吃',
    inverse_vol: '波动率倒数加权',
    risk_parity: '风险平价',
    composite: '组合策略合成',
  }
  return method ? (labels[method] ?? method) : ''
}

/** 路径状态说明 */
function pathReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    band_drift: '持仓偏离超过阈值，回到最近目标',
    band_skip: '所需换手低于阈值，跳过本次调仓',
    risk_stop: '止损触发，强制调仓',
  }
  return labels[reason] ?? reason
}

/** 因子别名声明：别名 → 模板与参数 */
const factorAliasMap = computed(
  () =>
    (configJson.value.factor_aliases ?? {}) as Record<
      string,
      { template_id: string; params?: Record<string, number> }
    >,
)

/** 参数对象的展示文本（键有序） */
function formatParams(params?: Record<string, number>): string {
  return Object.entries(params ?? {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${value}`)
    .join(', ')
}

/**
 * 因子引用的展示文案。
 * 声明了别名时展示「别名 → 模板(参数)」；未声明时引用名本身就是模板 ID。
 */
function factorLabel(ref: string): string {
  const declaration = factorAliasMap.value[ref]
  if (!declaration) {
    const template = availableFactors.value.find(f => f.factor_id === ref)
    return template ? `${ref}（${template.name}）` : ref
  }
  const paramsText = formatParams(declaration.params)
  return paramsText
    ? `${ref} → ${declaration.template_id}(${paramsText})`
    : `${ref} → ${declaration.template_id}`
}
const portfolioConfig = computed(() => (
  configJson.value.portfolio ?? { method: 'equal_weight', default_exposure: 0.5 }
) as {
  method?: string
  timing_exposure?: Record<string, number>
  default_exposure?: number
  vol_factor?: string
  cov_window?: number
})

/** timing_exposure 只配置了部分 regime 时缺失的档位（未配置 timing_exposure 时按默认三档表，不算缺档） */
const missingExposureRegimes = computed((): string[] => {
  const exposure = portfolioConfig.value.timing_exposure
  if (!exposure || Object.keys(exposure).length === 0) return []
  return ['offensive', 'neutral', 'defensive'].filter(regime => !(regime in exposure))
})
const riskConfig = computed(() => configJson.value.risk as {
  max_asset_weight?: number
  max_portfolio_exposure?: number
  min_cash_ratio?: number
  vol_target?: { target: number; window?: number; floor?: number } | null
  drawdown_stop?: { threshold: number; exposure?: number; cooldown_days?: number } | null
  trailing_stop?: { threshold: number; cooldown_days?: number } | null
} | undefined)
type RebalanceSchedule = { frequency?: string; day_of_week?: number; week_parity?: string; day_of_month?: number }
const rebalanceConfig = computed(() => configJson.value.rebalance as (RebalanceSchedule & {
  selection?: RebalanceSchedule
  risk?: RebalanceSchedule
  band?: { min_turnover?: number | null; max_drift?: number | null } | null
}) | undefined)

/** 两条腿的日程是否等价（等价时按单腿口径展示） */
const legsAreIdentical = computed((): boolean => {
  const rebalance = rebalanceConfig.value
  if (!rebalance) return true
  const selection = rebalance.selection || rebalance
  const risk = rebalance.risk || selection
  return (['frequency', 'day_of_week', 'week_parity', 'day_of_month'] as const).every(
    key => (selection[key] ?? null) === (risk[key] ?? null),
  )
})
function formatRebalanceSchedule(schedule: RebalanceSchedule): string {
  const weekdays = ['周一', '周二', '周三', '周四', '周五']
  if (schedule.frequency === 'biweekly') return `每两周${schedule.week_parity === 'odd' ? '奇' : '偶'}周 ${weekdays[schedule.day_of_week ?? 4]}`
  if (schedule.frequency === 'weekly') return `每周 ${weekdays[schedule.day_of_week ?? 4]}`
  if (schedule.frequency === 'monthly') return `每月 ${schedule.day_of_month ?? 1} 日`
  return '每日'
}

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

// ── 稳健性验证面板 ────────────────────────────────────────────────

/** 当前页签 */
const activeTab = ref<'config' | 'robustness'>('config')
/** 该策略的稳健性批次列表 */
const robustnessRuns = ref<RobustnessSummary[]>([])
/** 当前查看的批次详情 */
const selectedRun = ref<RobustnessDetail | null>(null)
const robustnessLoading = ref(false)
const robustnessError = ref('')

/** 批次类型中文标签 */
const KIND_LABELS: Record<string, string> = {
  scan: '参数邻域',
  ablate: '因子消融',
  pool: '资产池扰动',
}

/** 批次状态中文标签 */
const STATUS_TEXTS: Record<string, string> = {
  running: '执行中',
  success: '已完成',
  partial: '部分汇总',
  failed: '失败',
  cancelled: '已取消',
  paused: '已暂停',
  abandoned: '已作废',
}

/** 批次类型标签 */
function kindLabel(kind: string): string {
  return KIND_LABELS[kind] || kind
}

/** 批次状态标签 */
function statusText(status: string): string {
  return STATUS_TEXTS[status] || status
}

/** 批次状态对应的徽标样式 */
function statusChipClass(status: string): string {
  if (status === 'success') return 'chip-active'
  if (status === 'running' || status === 'partial') return 'chip-version'
  if (status === 'paused') return 'chip-freq'
  return 'chip-disabled'
}

/** 数字格式化（空值显示 '-'） */
function fmt(value: number | null | undefined): string {
  return value == null ? '-' : String(value)
}

/** 比例格式化（0-1 → 百分比） */
function fmtRatio(value: number | null | undefined): string {
  return value == null ? '-' : `${(value * 100).toFixed(1)}%`
}

/** 汇总覆盖率（部分汇总时提示结论仅供参考） */
function coverageOf(detail: RobustnessDetail): RobustnessCoverage | undefined {
  return detail.summary?.coverage
}

/** 加载该策略的稳健性批次列表（默认选中最近一个已完成的批次） */
async function loadRobustness(): Promise<void> {
  robustnessLoading.value = true
  robustnessError.value = ''
  try {
    const response = await fetchRobustnessRuns({ strategyId: props.strategyId, limit: 20 })
    robustnessRuns.value = response.items
    const target =
      selectedRun.value && response.items.some((r) => r.robustness_id === selectedRun.value?.robustness_id)
        ? selectedRun.value.robustness_id
        : response.items[0]?.robustness_id
    if (target) {
      await selectRun(target)
    } else {
      selectedRun.value = null
    }
  } catch (e) {
    robustnessError.value = getErrorMessage(e, '稳健性批次加载失败')
  } finally {
    robustnessLoading.value = false
  }
}

/** 查看单个批次详情 */
async function selectRun(robustnessId: string): Promise<void> {
  try {
    selectedRun.value = await fetchRobustnessDetail(robustnessId)
  } catch (e) {
    selectedRun.value = null
    robustnessError.value = getErrorMessage(e, '批次详情加载失败')
  }
}

/** 取消整批稳健性回测并刷新 */
async function handleCancelRun(robustnessId: string): Promise<void> {
  try {
    await cancelRobustnessRun(robustnessId)
    toast.info('已请求取消该批次，运行中的任务会在安全检查点退出')
    await loadRobustness()
  } catch (e) {
    toast.error(getErrorMessage(e, '取消失败'))
  }
}

/** 作废批次：把长期 running 的批次显式收口（证据与试验台账保留） */
async function handleAbandonRun(robustnessId: string): Promise<void> {
  try {
    await abandonRobustnessRun(robustnessId, '人工在策略详情页作废')
    toast.info('批次已作废（证据与试验台账保留）')
    if (selectedRun.value?.robustness_id === robustnessId) selectedRun.value = null
    await loadRobustness()
  } catch (e) {
    toast.error(getErrorMessage(e, '作废失败'))
  }
}

/** 切到稳健性页签时按需加载 */
watch(activeTab, (tab) => {
  if (tab === 'robustness' && !robustnessRuns.value.length && !robustnessLoading.value) {
    void loadRobustness()
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

  // 上线中的策略禁止直接更新 config_json：只提交元数据字段
  const success = await store.update(
    props.strategyId,
    isLive.value ? { ...editForm.value } : { ...editForm.value, config_json: finalConfig },
  )
  if (success) {
    showEdit.value = false
  }
}

// ── 管线调试面板 ──────────────────────────────────────────────────────────

/** 调试 Tab 配置 */
const debugTabs = computed(() => {
  const pd = allocation.value?.pipeline_detail
  if (!pd) return []
  const tabs: { key: string; label: string; count: number | null }[] = [
    { key: 'scoring', label: '评分明细', count: pd.scoring?.length ?? 0 },
  ]
  if (pd.filter_results) {
    tabs.push({
      key: 'filter',
      label: '过滤明细',
      count: pd.filter_results.filter((a) => !a.passed).length || null,
    })
  }
  if (pd.timing_detail && Object.keys(pd.timing_detail).length > 0) {
    tabs.push({
      key: 'timing',
      label: '择时明细',
      // 只计因子行，composite_score / proxy_index_code 等汇总字段不计入
      count: timingFactorRows.value.length,
    })
  }
  return tabs
})
const activeDebugTab = ref('scoring')

/** timing_detail 中对象型的汇总字段（不是逐因子明细） */
const TIMING_OBJECT_SUMMARY_KEYS = new Set(['state', 'rule_groups', 'tier', 'tier_rules'])

/** timing_detail 的值是否为逐因子明细（其余为汇总字段） */
function isTimingFactorDetail(value: TimingDetailValue): value is TimingFactorDetail {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

const REGIME_TEXT: Record<string, string> = REGIME_LABELS

/** 规则模式下的进攻 / 防守规则组展示文本（含嵌套子组；只有子组的 regime 也要展示） */
const timingRuleGroups = computed((): Array<{ regime: string; label: string; text: string }> => {
  const rules = timingConfig.value?.rules
  if (!rules) return []
  const groups: Array<{ regime: string; label: string; text: string }> = []
  for (const [regime, label] of [['defensive', '防守'], ['offensive', '进攻']] as const) {
    const group = rules[regime]
    if (!group) continue
    const text = ruleGroupText(group)
    if (!text) continue
    groups.push({ regime, label, text })
  }
  return groups
})

/** regime_rules（分状态配置覆盖）的展示行 */
const regimeRuleEntries = computed((): Array<{ regime: string; label: string; text: string }> => {
  const raw = configJson.value.regime_rules
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw as Record<string, unknown>).map(([regime, override]) => ({
    regime,
    label: regimeLabel(regime),
    text: JSON.stringify(override),
  }))
})

/** 择时综合方式的展示文本（区分是否启用多档仓位） */
const timingCombineText = computed((): string => {
  const timing = timingConfig.value
  const hasTiers = (timing?.tiers ?? []).length > 0
  if (timing?.combine === 'rules') {
    return hasTiers
      ? '规则模式 · 多档仓位（按档位顺序首个满足的档位决定 regime 与仓位，都不满足为中性）'
      : '规则模式（防守组优先，其次进攻组，都不满足为中性）'
  }
  return hasTiers ? '因子加权综合分过阈值 · 多档仓位（综合分分段映射总仓位）' : '因子加权综合分过阈值'
})

/** 规则组（含嵌套子组）的展示文本，子组加括号 */
function ruleGroupText(group: TimingRuleGroupJson): string {
  const joiner = group.logic === 'OR' ? ' 或 ' : ' 且 '
  const parts = (group.rules ?? []).map(rule => {
    const target = rule.compare_to ? factorLabel(rule.compare_to) : JSON.stringify(rule.value)
    return `${factorLabel(rule.factor)} ${rule.op} ${target}`
  })
  for (const child of group.groups ?? []) parts.push(`（${ruleGroupText(child)}）`)
  return parts.join(joiner)
}

/** 档位仓位方式的展示文本：固定 / 维持 / 相对增减 */
function tierExposureText(tier: TimingTierJson): string {
  if (tier.hold) return '维持上一仓位'
  if (tier.exposure_delta != null) {
    const sign = tier.exposure_delta >= 0 ? '+' : ''
    const low = ((tier.min_exposure ?? 0) * 100).toFixed(0)
    const high = ((tier.max_exposure ?? 1) * 100).toFixed(0)
    return `仓位 ${sign}${(tier.exposure_delta * 100).toFixed(0)}%（夹在 ${low}%~${high}%）`
  }
  return `仓位 ${((tier.exposure ?? 0) * 100).toFixed(0)}%`
}

/** 多档仓位配置的逐档展示文本 */
const timingTierRows = computed((): Array<{ index: number; text: string }> => {
  const timing = timingConfig.value
  const tiers = timing?.tiers ?? []
  return tiers.map((tier, index) => {
    const name = tier.label || `档位${index + 1}`
    const exposure = tierExposureText(tier)
    if (timing?.combine === 'rules') {
      const condition = ruleGroupText(tier)
      const regime = REGIME_TEXT[tier.regime ?? 'neutral'] ?? tier.regime
      return { index, text: `${name}：${condition || '（无规则）'} → ${regime}，${exposure}` }
    }
    return { index, text: `${name}：综合分 ≥ ${tier.min_score ?? '—'} → ${exposure}` }
  })
})

/** 条件仓位上限的逐条展示文本 */
const timingCapRows = computed((): Array<{ index: number; text: string }> =>
  (timingConfig.value?.exposure_caps ?? []).map((cap, index) => ({
    index,
    text: `${cap.label || `上限${index + 1}`}：${ruleGroupText(cap) || '（无规则）'} → 总仓位不超过 ${(cap.max_exposure * 100).toFixed(0)}%`,
  })),
)

/** 状态层配置的展示文本；恒等（全部缺省）时为空串 */
const timingStateText = computed((): string => {
  const state = timingConfig.value?.state
  if (!state) return ''
  const parts: string[] = []
  if ((state.hysteresis ?? 0) > 0) parts.push(`滞回 ${state.hysteresis} 分`)
  if (state.hold_on_neutral) parts.push('无信号维持前值')
  if ((state.confirm_days ?? 1) > 1) parts.push(`连续 ${state.confirm_days} 日确认才切换`)
  if ((state.min_hold_days ?? 0) > 0) parts.push(`切换后最短持有 ${state.min_hold_days} 日`)
  if (state.max_exposure_step != null) parts.push(`档位仓位每次最多变动 ${(state.max_exposure_step * 100).toFixed(0)}%`)
  return parts.join('，')
})

/** 择时明细中的逐因子行（排除综合分、缺失列表、代理与降级等汇总字段） */
const timingFactorRows = computed((): Array<[string, TimingFactorDetail]> => {
  const detail = allocation.value?.pipeline_detail?.timing_detail ?? {}
  const rows: Array<[string, TimingFactorDetail]> = []
  for (const [fid, value] of Object.entries(detail)) {
    if (TIMING_OBJECT_SUMMARY_KEYS.has(fid)) continue
    if (isTimingFactorDetail(value)) rows.push([fid, value as TimingFactorDetail])
  }
  return rows
})

/** 择时明细汇总：综合分、实际使用的代理指数与降级回退说明 */
const timingSummary = computed(() => {
  const detail = allocation.value?.pipeline_detail?.timing_detail
  if (!detail) return null
  const proxy = typeof detail.proxy_index_code === 'string' ? detail.proxy_index_code : null
  let proxyText: string | null = null
  if (detail.proxy_mode === 'consistent' && proxy) {
    proxyText = `全部择时因子统一取 ${proxy}`
  } else if (detail.proxy_mode === 'configured') {
    proxyText = '按因子指定代理，各代理组均全覆盖（见各因子“代理”列）'
  } else if (detail.proxy_mode === 'per_factor') {
    proxyText = '存在未全覆盖的代理组，已逐因子取第一个有值的代理（见各因子“代理”列）'
  }
  let stateText: string | null = null
  const rawRegime = typeof detail.raw_regime === 'string' ? detail.raw_regime : null
  const stateDetail = detail.state
  if (rawRegime && typeof stateDetail === 'object' && stateDetail !== null && !Array.isArray(stateDetail)) {
    const record = stateDetail as Record<string, unknown>
    const held = typeof record.held_days === 'number' ? record.held_days : null
    const pending = typeof record.pending_regime === 'string' ? record.pending_regime : null
    const pendingDays = typeof record.pending_days === 'number' ? record.pending_days : 0
    stateText = `状态层：原始判定 ${REGIME_TEXT[rawRegime] ?? rawRegime}`
    if (held != null) stateText += `，当前 regime 已持有 ${held} 日`
    if (pending) stateText += `，待确认 ${REGIME_TEXT[pending] ?? pending}（${pendingDays} 日）`
  }
  let rulesText: string | null = null
  const ruleGroups = detail.rule_groups
  if (typeof ruleGroups === 'object' && ruleGroups !== null && !Array.isArray(ruleGroups)) {
    const groups = ruleGroups as Record<string, { matched?: boolean } | undefined>
    const defensive = groups.defensive?.matched ? '满足' : '未满足'
    const offensive = groups.offensive?.matched ? '满足' : '未满足'
    rulesText = `规则组：防守${defensive}，进攻${offensive}`
  }
  let tierText: string | null = null
  const tierDetail = detail.tier
  if (typeof tierDetail === 'object' && tierDetail !== null && !Array.isArray(tierDetail)) {
    const record = tierDetail as Record<string, unknown>
    const label = typeof record.label === 'string' ? record.label : null
    const exposure = typeof record.exposure === 'number' ? record.exposure : null
    if (label && exposure != null) {
      const source = record.source === 'held' ? '（状态层沿用上一档位）' : ''
      tierText = `仓位档位：${label}，档位仓位 ${(exposure * 100).toFixed(0)}%${source}`
    } else {
      tierText = '仓位档位：未命中，按 regime 仓位'
    }
  }
  const fallbackRegime = typeof detail.fallback_regime === 'string' ? detail.fallback_regime : null
  const fallbackReason = typeof detail.fallback_reason === 'string' ? detail.fallback_reason : null
  return {
    composite: typeof detail.composite_score === 'number' ? detail.composite_score : null,
    proxyText,
    stateText,
    rulesText,
    tierText,
    degraded: detail.degraded === true,
    degradedText: fallbackRegime
      ? `择时因子全部缺失，按 ${fallbackRegime} 回退${fallbackReason ? `（${fallbackReason}）` : ''}`
      : null,
  }
})

/** 评分明细按最终得分降序展示；无得分的已排除资产置于末尾。 */
const sortedScoringDetails = computed(() => {
  const scoring = allocation.value?.pipeline_detail?.scoring ?? []
  return [...scoring].sort((a, b) => {
    const aScore = a.final_score ?? Number.NEGATIVE_INFINITY
    const bScore = b.final_score ?? Number.NEGATIVE_INFINITY
    if (aScore !== bScore) return bScore - aScore
    return a.index_code.localeCompare(b.index_code)
  })
})

/** 因子状态中文标签映射 */
function statusLabel(status: string): string {
  const map: Record<string, string> = {
    ok: '正常',
    missing_excluded: '缺失(排除)',
    missing_zero: '缺失(按0)',
    missing_ignored: '缺失(忽略)',
  }
  return map[status] || status
}

/** 格式化过滤规则阈值（数字/数组/字符串） */
function formatThreshold(t: unknown): string {
  if (Array.isArray(t) && t.length === 2) return `[${t[0]}, ${t[1]}]`
  if (typeof t === 'number') return String(t)
  if (typeof t === 'string') return t
  return String(t ?? '-')
}

/** 运行决策管线（支持指定交易日） */
/** 已弹过的 allocation 警告 key（同一警告只提示一次） */
const seenAllocationWarningKeys = new Set<string>()
async function handleRunAllocation(): Promise<void> {
  allocating.value = true
  runError.value = ''
  try {
    const dateParam = selectedDate.value || undefined
    allocation.value = await runAllocation(props.strategyId, dateParam)
    // 实时决策中的缺失因子警告随响应返回，按级别弹一次提示
    for (const w of allocation.value.warnings ?? []) {
      const key = `alloc:${w.code}:${w.trade_date ?? ''}:${w.index_code ?? ''}`
      if (seenAllocationWarningKeys.has(key)) continue
      seenAllocationWarningKeys.add(key)
      if (w.level === 'error') toast.error(w.message, { key })
      else if (w.level === 'warning') toast.warning(w.message, { key })
      else toast.info(w.message, { key })
    }
    // 决策完成后加载标的K线数据
    await loadKlineData()
  } catch (e) {
    runError.value = getErrorMessage(e, '决策管线执行失败')
    allocation.value = null
  } finally {
    allocating.value = false
  }
}

/** 加载排名标的决策日前后K线数据并初始化图表 */
async function loadKlineData(): Promise<void> {
  if (!allocation.value?.rankings?.length) return
  chartsLoading.value = true
  chartsData.value = {}

  // 销毁旧图表实例
  disposeCharts()

  const range = calcDateRange(decisionDate.value)
  const codes = allocation.value.rankings.map(r => r.index_code)
  const results = await Promise.allSettled(
    codes.map(code =>
      fetchIndexDailyBars(code, { startDate: range.startDate, endDate: range.endDate }),
    ),
  )
  const data: Record<string, DailyBar[]> = {}
  codes.forEach((code, i) => {
    const r = results[i]
    data[code] = r.status === 'fulfilled' ? r.value : []
  })
  chartsData.value = data
  chartsLoading.value = false

  await nextTick()
  initCharts()
}

/** 初始化所有标的K线图表 */
async function initCharts(): Promise<void> {
  const echarts = await loadEcharts()
  chartResizeObserver?.disconnect()
  chartResizeObserver =
    typeof ResizeObserver !== 'undefined'
      ? new ResizeObserver((entries) => {
          for (const entry of entries) {
            for (const [code, el] of Object.entries(chartRefs)) {
              if (el === entry.target) (chartInstances.get(code) as { resize(): void } | undefined)?.resize()
            }
          }
        })
      : null

  for (const code of Object.keys(chartsData.value)) {
    const el = chartRefs[code]
    if (!el) continue
    const bars = chartsData.value[code]
    if (!bars || bars.length === 0) continue

    const chart = echarts.init(el, CHART_THEME, { renderer: 'canvas' })
    chartInstances.set(code, chart)
    chartResizeObserver?.observe(el)

    const dates = bars.map(b => b.trade_date)
    const candleData = bars.map(b => [b.open_price, b.close_price, b.low_price, b.high_price])
    const volumes = bars.map(b => b.volume ?? 0)
    // 找到决策日对应的索引，用于标记线
    const decisionIdx = dates.indexOf(decisionDate.value)

    chart.setOption({
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: '#1e293b',
        borderColor: '#334155',
        textStyle: { color: '#f1f5f9', fontSize: 11 },
      },
      grid: [
        { left: 50, right: 12, top: 16, bottom: 60 },
        { left: 50, right: 12, top: '68%', bottom: 20 },
      ],
      xAxis: [
        {
          type: 'category', data: dates, gridIndex: 0,
          axisLine: { lineStyle: { color: '#334155' } },
          axisLabel: { color: '#94a3b8', fontSize: 10 },
        },
        {
          type: 'category', data: dates, gridIndex: 1,
          axisLine: { lineStyle: { color: '#334155' } },
          axisLabel: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true, gridIndex: 0,
          splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
          axisLabel: { color: '#94a3b8', fontSize: 10 },
        },
        {
          scale: true, splitNumber: 2, gridIndex: 1,
          splitLine: { show: false },
          axisLabel: { show: false },
        },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
        {
          type: 'slider', xAxisIndex: [0, 1],
          bottom: 4, height: 16,
          borderColor: '#334155',
          fillerColor: 'rgba(59,130,246,0.1)',
          handleStyle: { color: '#3b82f6' },
          textStyle: { color: '#94a3b8', fontSize: 10 },
        },
      ],
      series: [
        {
          name: 'K线', type: 'candlestick',
          xAxisIndex: 0, yAxisIndex: 0, data: candleData,
          itemStyle: { color: '#22c55e', color0: '#ef4444', borderColor: '#22c55e', borderColor0: '#ef4444' },
          markLine: decisionIdx >= 0 ? {
            silent: true,
            symbol: 'none',
            label: { show: true, formatter: '决策日', position: 'start', color: '#f59e0b', fontSize: 10 },
            lineStyle: { color: '#f59e0b', type: 'dashed', width: 1.5 },
            data: [{ xAxis: decisionIdx }],
          } : undefined,
        },
        {
          name: '量', type: 'bar',
          xAxisIndex: 1, yAxisIndex: 1, data: volumes,
          itemStyle: { color: 'rgba(59,130,246,0.5)' },
        },
      ],
    })
  }
}

/** 销毁所有图表实例 */
function disposeCharts(): void {
  chartResizeObserver?.disconnect()
  chartResizeObserver = null
  chartInstances.forEach((c) => {
    try { (c as { dispose(): void }).dispose() } catch { /* ignore */ }
  })
  chartInstances.clear()
  chartRefs && Object.keys(chartRefs).forEach(k => delete chartRefs[k])
}

/** 切换星标状态 */
async function handleToggleStar(): Promise<void> {
  if (!store.current) return
  if (store.current.is_starred) {
    await store.unstar(store.current.strategy_id)
  } else {
    await store.star(store.current.strategy_id)
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

/** 清理本策略的回测、对比、稳健性与优化研究数据，保留策略配置。 */
async function handleClearResearchData(): Promise<void> {
  if (!confirm('确定清理此策略及关联 draft 策略的全部研究数据？将删除回测、对比、稳健性验证、优化记录和 draft 策略，且不可撤销。')) return
  clearingResearchData.value = true
  try {
    const result = await clearStrategyResearchData(props.strategyId)
    const total = Object.values(result.deleted).reduce((sum, count) => sum + count, 0)
    toast.info(`研究数据已清理（共 ${total} 条记录）`)
    robustnessRuns.value = []
  } catch (e) {
    toast.error(getErrorMessage(e, '清理研究数据失败'))
  } finally {
    clearingResearchData.value = false
  }
}

onMounted(async () => {
  await Promise.all([store.loadOne(props.strategyId), loadLiveStatus()])
  try {
    availableFactors.value = await fetchFactorSpecs()
  } catch {
    availableFactors.value = []
  }
})
onUnmounted(() => disposeCharts())
</script>

<style scoped>
.page { display: flex; flex-direction: column; gap: 20px; }
.loading { padding: 60px; text-align: center; color: var(--text-muted); }
.error-tip { padding: 12px 16px; background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); border-radius: var(--radius); color: #f87171; font-size: 13px; }
.chip-live { background: rgba(245,158,11,0.15); color: var(--warning); }
.chip-warn { background: rgba(245,158,11,0.15); color: var(--warning); }
.live-freeze-tip {
  padding: 10px 12px; margin-bottom: 8px; border-radius: var(--radius-sm);
  background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.3); color: var(--warning); font-size: 12px;
}
.regime-rule-json { white-space: pre-wrap; word-break: break-all; font-size: 12px; }

.page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.header-left { display: flex; flex-direction: column; gap: 8px; }
.page-title { font-size: 22px; font-weight: 700; }
.page-title .star-btn {
  vertical-align: middle;
  margin-left: 8px;
  padding: 2px 8px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
  line-height: 1;
}
.page-title .star-btn.starred {
  color: #f59e0b;
  border-color: rgba(245, 158, 11, 0.3);
}
.page-title .star-btn:hover {
  border-color: #f59e0b;
  color: #f59e0b;
}
.header-actions { display: flex; gap: 8px; }

.chips { display: flex; gap: 6px; }
.chip { font-size: 11px; padding: 2px 8px; border-radius: 20px; font-weight: 500; }
.chip-version { background: rgba(59,130,246,0.15); color: #60a5fa; }
.chip-freq { background: rgba(34,197,94,0.12); color: #4ade80; }
.chip-active { background: rgba(34,197,94,0.12); color: #4ade80; }
.chip-disabled { background: rgba(239,68,68,0.12); color: #f87171; }

.description { color: var(--text-muted); font-size: 14px; line-height: 1.7; max-width: 700px; }

/* 稳健性验证面板 */
.page-tabs { margin-top: -8px; }
.robustness-panel { display: flex; flex-direction: column; gap: 12px; }
.robustness-hint { color: var(--text-muted); font-size: 12px; line-height: 1.7; max-width: 900px; }
.robustness-hint code {
  background: var(--surface-2);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}
.robust-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.robust-table th {
  text-align: left;
  color: var(--text-muted);
  font-weight: 500;
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
}
.robust-table td { padding: 6px 8px; border-bottom: 1px solid var(--border); }
.robust-table.inner { margin-top: 6px; }
.robust-table tr.row-selected { background: rgba(59,130,246,0.08); }
.row-actions { display: flex; gap: 8px; }
.btn-link {
  background: none;
  border: none;
  color: #60a5fa;
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}
.btn-link.danger { color: #f87171; }
.robust-detail {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.robust-detail-title { font-size: 14px; font-weight: 600; }
.robust-block { display: flex; flex-direction: column; gap: 4px; }
.robust-block-title { font-size: 12px; color: var(--text-muted); font-weight: 500; }
.robust-kv { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; align-items: center; }
.mono { font-family: var(--font-mono); }

/* 配置网格 */
.config-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
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
.config-warn { color: #f59e0b; }

/* 择时明细汇总行（综合分 / 代理来源 / 降级回退） */
.timing-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  padding: 8px 0 12px;
  font-size: 12px;
  color: var(--text-muted);
}
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

.mono { font-family: var(--font-mono); }

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
  width: min(720px, calc(100vw - 32px));
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

/* 日期选择器 */
.date-input {
  padding: 8px 12px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: border-color 0.15s;
}
.date-input:focus { outline: none; border-color: var(--accent); }
.date-input::-webkit-calendar-picker-indicator { filter: invert(0.7); cursor: pointer; }

/* 执行结果提示 */
.run-error {
  padding: 10px 16px;
  border-radius: var(--radius);
  font-size: 13px;
  background: rgba(239,68,68,0.1);
  border: 1px solid rgba(239,68,68,0.25);
  color: #f87171;
}

/* 决策结果区域 */
.allocation-section { display: flex; flex-direction: column; gap: 12px; }
.section-title { font-size: 16px; font-weight: 600; }
.section-header-row { display: flex; align-items: center; gap: 10px; }
.data-date-tag { font-size: 11px; color: var(--text-muted); background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 2px 8px; }

/* 概览条 */
.alloc-summary-bar {
  display: flex; gap: 16px; flex-wrap: wrap;
  padding: 12px 16px;
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
}
.summary-item { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.summary-label { color: var(--text-muted); }
.summary-val { color: var(--text); }
.summary-val-accent { color: #4ade80; font-weight: 600; }
.summary-val-muted { color: var(--text-muted); font-style: italic; }
.regime-tag-sm {
  font-size: 11px; font-weight: 600; padding: 1px 8px; border-radius: 10px;
}
.regime-tag-sm.regime-offensive { background: rgba(239,68,68,0.15); color: #f87171; }
.regime-tag-sm.regime-defensive { background: rgba(34,197,94,0.15); color: #4ade80; }
.regime-tag-sm.regime-neutral { background: rgba(148,163,184,0.15); color: #94a3b8; }

/* 空状态提示 */
.alloc-empty-hint {
  display: flex; flex-direction: column; align-items: center; gap: 4px;
  padding: 24px 16px;
  background: var(--surface); border: 1px dashed var(--border); border-radius: var(--radius);
  text-align: center;
}
.alloc-empty-hint p { margin: 0; font-size: 14px; color: var(--text-muted); }
.alloc-empty-hint .hint-sub { font-size: 12px; color: var(--text-muted); opacity: 0.7; }
.empty-icon { font-size: 24px; margin-bottom: 4px; }

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

/* K线图表区域 */
.kline-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.kline-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.kline-header-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--surface-2);
}
.kline-rank { font-size: 12px; font-weight: 700; color: var(--accent); min-width: 24px; }
.kline-code { font-size: 12px; font-weight: 600; }
.kline-name { font-size: 12px; color: var(--text-muted); flex: 1; }
.kline-score { font-size: 11px; color: var(--text-muted); }
.kline-chart { width: 100%; height: 280px; }

.chart-placeholder {
  padding: 40px 16px;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}
.chart-placeholder-sm {
  padding: 24px 16px;
  font-size: 12px;
  border: none;
  border-top: 1px solid var(--border);
  border-radius: 0;
}

/* ── 管线调试面板 ──────────────────────────────────────────────────────── */

.debug-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.debug-tabs {
  display: flex;
  gap: 4px;
}

.debug-tab {
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  gap: 6px;
}
.debug-tab:hover {
  background: var(--surface-2);
  color: var(--text);
}
.debug-tab.active {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}

.tab-count {
  font-size: 10px;
  background: rgba(255, 255, 255, 0.2);
  padding: 0 5px;
  border-radius: 8px;
  min-width: 18px;
  text-align: center;
}

.debug-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.debug-stat-bar {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px 12px;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
}

.stat-chip {
  font-size: 11px;
  padding: 2px 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  color: var(--text);
}
.stat-chip.chip-fail { color: #f87171; border-color: rgba(239, 68, 68, 0.3); }
.stat-chip.chip-total { color: var(--text-muted); }

/* 调试卷卡 */
.debug-asset {
  border-bottom: 1px solid var(--border);
}
.debug-asset:last-child { border-bottom: none; }
.debug-asset.excluded { border-left: 3px solid #f87171; }
.debug-asset:not(.excluded) { border-left: 3px solid #4ade80; }

.debug-asset-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 12px;
  user-select: none;
}
.debug-asset-header:hover { background: var(--surface-2); }

.debug-code {
  font-size: 12px;
  font-weight: 600;
  min-width: 56px;
}
.debug-name { color: var(--text-muted); flex: 1; }
.debug-final-score {
  color: var(--accent);
  font-weight: 600;
  font-size: 13px;
}
.debug-na { color: var(--text-muted); }

.debug-excluded-tag,
.debug-failed-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.15);
  color: #f87171;
}
.debug-passed-tag {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: rgba(34, 197, 94, 0.15);
  color: #4ade80;
}
.debug-exclude-reason {
  font-size: 11px;
  color: #f87171;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.debug-asset-body {
  padding: 8px 12px 12px;
}

.debug-factor-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
}
.debug-factor-table th {
  text-align: left;
  padding: 4px 6px;
  color: var(--text-muted);
  font-weight: 500;
  border-bottom: 1px solid var(--border);
}
.debug-factor-table td {
  padding: 4px 6px;
  border-bottom: 1px solid rgba(51, 65, 85, 0.2);
}

.debug-raw-score {
  margin-top: 8px;
  font-size: 11px;
  color: var(--text-muted);
  padding: 4px 8px;
  background: var(--surface-2);
  border-radius: 4px;
}

/* 因子状态标签 */
.status-badge {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 6px;
}
.status-ok { color: #4ade80; }
.status-missing_excluded { color: #f87171; }
.status-missing_zero { color: #f59e0b; }
.status-missing_ignored { color: var(--text-muted); }

/* 过滤结果 */
.rule-fail td { color: #f87171; }
.result-pass { color: #4ade80; font-size: 11px; }
.result-fail { color: #f87171; font-size: 11px; }

/* 因子状态行着色 */
.factor-status-missing_excluded td { opacity: 0.6; }
.factor-status-missing_ignored td { opacity: 0.5; }

.debug-empty {
  padding: 8px 0;
  font-size: 11px;
  color: var(--text-muted);
  text-align: center;
}

.debug-empty-hint {
  padding: 24px 16px;
  text-align: center;
  font-size: 13px;
  color: var(--text-muted);
}
</style>
