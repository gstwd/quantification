<template>
  <div class="page">
    <div class="page-header">
      <div class="header-main">
        <h1 class="page-title">RRG / 扩散 数据研究调试台</h1>
        <span class="code-badge">研究</span>
      </div>
      <p class="factor-desc">
        面向申万一级行业的 RRG 与数量占比扩散因子研究页：用于查看数据覆盖、缺数规则、
        因子取值与信号口径。本页仅做数据研究，不创建策略、不触发回测，也不写入任何因子表。
      </p>
      <div class="notice-bar">
        <div class="notice-item">
          <span class="notice-label">单次区间上限</span>
          RRG ≈ 10 年（3700 自然日）；扩散 ≈ 6 年（2200 自然日），超出返回 422 错误提示。
        </div>
        <div class="notice-item">
          <span class="notice-label">数据提示</span>
          “暂无有效数据”与“计算失败/缺口”现在可区分：所有缺口、warm-up 不足与数据越界都会在
          各模块问题区显式列出。
        </div>
        <div class="notice-item">
          <span class="notice-label">超时提示</span>
          RRG/扩散研究接口已放宽到 120 秒；若仍超时，错误区会显示 request_id，
          可到后端日志按该 ID 检索“即时计算完成”的耗时与结果。
        </div>
        <div class="notice-item">
          <span class="notice-label">因子中心</span>
          本页四个指标已在因子中心登记（正式策略/回测默认参数由日频预计算维护）：
          <a href="/factors/rrg_rs_ratio">rrg_rs_ratio</a> ·
          <a href="/factors/rrg_rs_momentum">rrg_rs_momentum</a> ·
          <a href="/factors/rrg_quadrant">rrg_quadrant</a> ·
          <a href="/factors/diffusion_count_ratio">diffusion_count_ratio</a>
        </div>
      </div>
    </div>

    <!-- 参数面板 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">查询参数</span>
        <div class="controls">
          <button class="query-btn" :disabled="rrgLoading" @click="queryRRG()">
            {{ rrgLoading ? 'RRG 计算中...' : '查询 RRG' }}
          </button>
          <button class="query-btn" :disabled="diffusionLoading" @click="queryDiffusion()">
            {{ diffusionLoading ? '扩散计算中...' : '查询扩散' }}
          </button>
          <button class="query-btn" :disabled="correlationLoading" @click="queryCorrelation()">
            {{ correlationLoading ? '相关度计算中...' : '计算行业相关度' }}
          </button>
          <button
            class="query-btn"
            :disabled="indexDiffusionLoading || !indexDiffusionCode || !indexDiffusionDate"
            @click="queryIndexDiffusion()"
          >
            {{ indexDiffusionLoading ? '指数扩散计算中...' : '计算指数扩散' }}
          </button>
          <button
            class="query-btn"
            :disabled="rrgLoading || diffusionLoading"
            @click="queryAll()"
          >
            {{ rrgLoading || diffusionLoading ? '执行中...' : '顺序执行全部' }}
          </button>
        </div>
      </div>
      <div class="lab-form">
        <div class="form-row wrap">
          <label>日期范围</label>
          <button
            v-for="preset in rangePresets"
            :key="preset.label"
            class="range-btn"
            :class="{ active: activePreset === preset.label }"
            @click="applyPreset(preset)"
          >
            {{ preset.label }}
          </button>
          <input type="date" class="date-input" v-model="startDate" />
          <span class="sep">~</span>
          <input type="date" class="date-input" v-model="endDate" />
        </div>
        <div class="form-row">
          <label>RRG 参数</label>
          <input
            type="number"
            class="num-input"
            v-model.number="lookbackRatio"
            min="1"
            max="500"
          />
          <span class="field-hint">ratio 回看</span>
          <input
            type="number"
            class="num-input"
            v-model.number="lookbackMom"
            min="1"
            max="500"
          />
          <span class="field-hint">momentum 回看</span>
          <input
            type="number"
            class="num-input"
            v-model.number="smoothWindow"
            min="1"
            max="60"
          />
          <span class="field-hint">平滑</span>
          <span class="warmup-hint">
            warm-up ≈ {{ warmupDays }} 个交易日，结果会按实际数据自动前移并在覆盖表中展示
          </span>
        </div>
        <div class="form-row">
          <label>扩散参数</label>
          <input
            type="number"
            class="num-input"
            v-model.number="diffusionLookback"
            min="1"
            max="500"
          />
          <span class="field-hint">上涨回看</span>
          <input
            type="number"
            class="num-input"
            v-model.number="diffusionSmooth"
            min="1"
            max="60"
          />
          <span class="field-hint">平滑</span>
          <label class="inline-check">
            <input type="checkbox" v-model="withCoverage" />
            返回每日覆盖度（有效样本/成员数）
          </label>
        </div>
        <div class="form-row wrap">
          <label>行业范围</label>
          <span class="scope-actions">
            <button class="link-btn" @click="selectAllIndustries">全选</button>
            <button class="link-btn" @click="clearIndustries">清空</button>
            <span class="field-hint">已选 {{ selectedCodes.length }}/{{ industries.length }}</span>
          </span>
          <span v-for="item in industries" :key="item.industry_code" class="ind-check">
            <input
              type="checkbox"
              :value="item.industry_code"
              v-model="selectedCodes"
            />
            {{ item.name_cn }}
          </span>
        </div>
      </div>
    </div>

    <!-- 指数单日扩散模块 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">指数成分扩散（单交易日）</span>
        <div class="controls">
          <select v-model="indexDiffusionCode" class="select-input wide" :disabled="!indexes.length">
            <option value="" disabled>选择指数</option>
            <option v-for="item in indexes" :key="item.index_code" :value="item.index_code">
              {{ item.index_name }}（{{ item.index_code }}）
            </option>
          </select>
          <input v-model="indexDiffusionDate" type="date" class="date-input" />
          <button
            class="query-btn"
            :disabled="indexDiffusionLoading || !indexDiffusionCode || !indexDiffusionDate"
            @click="queryIndexDiffusion()"
          >
            {{ indexDiffusionLoading ? '计算中...' : '计算单日扩散' }}
          </button>
        </div>
      </div>
      <div class="chart-note">
        最多使用该指数已入库交易日轴的目标日前 240 日，按 220 日涨跌与连续 20 日均值计算。
        成分在目标日或对应回看日缺收盘时不计分子和分母；本操作只读数据，不写入因子表。
      </div>
      <div v-if="indexDiffusionError" class="error-banner">{{ indexDiffusionError }}</div>
      <div v-else-if="indexDiffusionLoading" class="empty">正在读取指数成分与个股收盘并计算...</div>
      <div v-else-if="!indexDiffusionResp" class="empty">选择指数与已入库交易日后，点击“计算单日扩散”。</div>
      <template v-else>
        <div class="meta-line">
          <span>指数 {{ indexDiffusionResp.index_code }}</span>
          <span>目标日 {{ indexDiffusionResp.trade_date }}</span>
          <span>计算轴 {{ indexDiffusionResp.calculation_date_count }} 个交易日</span>
          <span>数据版本 {{ indexDiffusionResp.calculation_version || '—' }}</span>
        </div>
        <table class="data-table">
          <thead>
            <tr>
              <th>原始上涨占比</th>
              <th>平滑扩散因子</th>
              <th>上涨样本</th>
              <th>有效样本</th>
              <th>缺失样本</th>
              <th>成分总数</th>
              <th>平滑窗口</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="mono">{{ formatPercent(indexDiffusionResp.raw_ratio) }}</td>
              <td class="mono" :class="indexDiffusionResp.factor_value === null ? 'text-warn' : 'text-ok'">
                {{ formatPercent(indexDiffusionResp.factor_value, true) }}
              </td>
              <td class="mono">{{ indexDiffusionResp.rising_sample_count }}</td>
              <td class="mono">{{ indexDiffusionResp.valid_sample_count }}</td>
              <td class="mono" :class="indexDiffusionResp.missing_sample_count ? 'text-warn' : 'text-ok'">
                {{ indexDiffusionResp.missing_sample_count }}
              </td>
              <td class="mono">{{ indexDiffusionResp.member_count }}</td>
              <td class="mono" :class="indexDiffusionResp.window_complete ? 'text-ok' : 'text-warn'">
                {{ indexDiffusionResp.valid_days }}/{{ indexDiffusionResp.smooth_window }} 有效日
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="!indexDiffusionResp.window_complete" class="chart-note text-warn">
          连续 20 日窗口含无有效扩散值，严格口径下平滑因子为 NULL；未跳过缺失日压缩窗口。
        </div>
        <div class="chart-note">
          本次加载 {{ indexDiffusionResp.stock_count }} 只成分股、{{ indexDiffusionResp.stock_close_point_count }} 个收盘点。
        </div>
      </template>
    </div>

    <!-- RRG 模块 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">RRG 四象限与历史轨迹</span>
        <div class="controls">
          <select v-model="rrgDate" class="select-input" :disabled="!rrgDates.length">
            <option v-for="d in rrgDates" :key="d" :value="d">{{ d }}</option>
          </select>
          <label class="inline-check">
            <input type="checkbox" v-model="showTrail" />
            显示轨迹
          </label>
          <select
            v-if="showTrail"
            v-model="trailDays"
            class="select-input"
          >
            <option v-for="n in [10, 30, 60, 120, 250]" :key="n" :value="n">
              轨迹 {{ n }} 日
            </option>
          </select>
          <button class="query-btn" :disabled="rrgLoading" @click="queryRRG()">
            重新计算
          </button>
        </div>
      </div>
      <div v-if="rrgError" class="error-banner">{{ rrgError }}</div>
      <div v-if="rrgIssues.length" class="issue-list">
        <div
          v-for="(issue, idx) in rrgIssues"
          :key="`${issue.code}-${idx}`"
          class="issue-item"
          :class="issue.level"
        >
          <span class="issue-code">{{ issue.code }}</span>
          <span class="issue-message">{{ issue.message }}</span>
          <span v-if="issue.count !== null" class="issue-count">×{{ issue.count }}</span>
          <span v-if="issue.sample_dates.length" class="issue-samples">
            示例：{{ issue.sample_dates.join('、') }}
          </span>
        </div>
      </div>
      <div v-if="rrgLoading && !rrgResp" class="empty">RRG 计算中（服务端按行业日线窗口加载）...</div>
      <div v-else-if="!rrgResp" class="empty">
        暂无 RRG 结果，点击“查询 RRG”开始（默认近 3 年）。
      </div>
      <template v-else>
        <div v-if="rrgMeta" class="meta-line">
          <span>请求 {{ rrgMeta.requested_start }} ~ {{ rrgMeta.requested_end }}</span>
          <span>有效 {{ rangeText(rrgMeta.effective_start, rrgMeta.effective_end) }}</span>
          <span>市场交易日 {{ rrgMeta.market_trading_days }}</span>
          <span>warm-up {{ rrgMeta.warmup_required }} 交易日</span>
          <span>上限 {{ rrgMeta.max_range_days }} 自然日</span>
        </div>
        <div class="chart-area" ref="rrgChartEl"></div>
        <div class="chart-note">散点=所选日期全部行业；轨迹=各行业最近 N 日 (RS-Ratio, RS-Momentum) 路径。</div>

        <details class="fold">
          <summary>RRG 数据覆盖（{{ rrgMeta?.coverage.length ?? 0 }} 个行业）</summary>
          <table class="data-table">
            <thead>
              <tr>
                <th>行业</th>
                <th>输入日线</th>
                <th>覆盖区间</th>
                <th>缺口天数</th>
                <th>前置无效</th>
                <th>有效点数</th>
                <th>首个有效日</th>
                <th>最后有效日</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in rrgMeta?.coverage ?? []" :key="item.industry_code">
                <td>{{ item.name_cn || item.industry_code }}</td>
                <td class="mono">{{ item.present_input_days }}/{{ item.expected_input_days }}</td>
                <td class="mono">{{ rangeText(item.data_start_date, item.data_end_date) }}</td>
                <td class="mono" :class="item.missing_input_days > 0 ? 'text-warn' : 'text-ok'">
                  {{ item.missing_input_days }}
                </td>
                <td class="mono" :class="item.leading_nan_days > 0 ? 'text-warn' : ''">
                  {{ item.leading_nan_days }}
                </td>
                <td class="mono">{{ item.valid_count }}/{{ item.output_days }}</td>
                <td class="mono">{{ item.valid_from ?? '—' }}</td>
                <td class="mono">{{ item.valid_until ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </details>
        <div v-if="rrgMeta?.notes.length" class="rule-note">
          <div v-for="note in rrgMeta.notes" :key="note" class="rule-item">• {{ note }}</div>
        </div>
      </template>
    </div>

    <!-- 指数行业相关度模块 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">行业与指数日收益相关度</span>
        <div class="controls">
          <select v-model="correlationIndexCode" class="select-input wide" :disabled="!indexes.length">
            <option value="" disabled>选择指数</option>
            <option v-for="item in indexes" :key="item.index_code" :value="item.index_code">
              {{ item.index_name }}（{{ item.index_code }}）
            </option>
          </select>
          <button class="query-btn" :disabled="correlationLoading || !correlationIndexCode" @click="queryCorrelation()">
            {{ correlationLoading ? '计算中...' : '计算相关度' }}
          </button>
        </div>
      </div>
      <div class="chart-note">
        按查询日期范围计算 Pearson 日收益相关系数。仅使用指数与行业均有当日及前一交易日收盘的共同样本；
        缺失收盘不前填，缺口后的跨日涨跌也不计入。
      </div>
      <div v-if="correlationError" class="error-banner">{{ correlationError }}</div>
      <div v-else-if="correlationLoading" class="empty">正在读取已入库的指数与行业日线并计算相关度...</div>
      <div v-else-if="!correlationResp" class="empty">选择指数后点击“计算相关度”查看各所选行业的结果。</div>
      <template v-else>
        <div class="meta-line">
          <span>指数 {{ correlationResp.index_code }}</span>
          <span>区间 {{ correlationResp.start }} ~ {{ correlationResp.end }}</span>
          <span>指数收盘 {{ correlationResp.index_close_days }} 日</span>
          <span>行业按相关度降序</span>
        </div>
        <div v-if="!correlationResp.items.length" class="empty">所选行业没有可用日线数据。</div>
        <table v-else class="data-table">
          <thead>
            <tr>
              <th>排名</th>
              <th>行业</th>
              <th>相关系数</th>
              <th>有效收益样本</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, idx) in correlationResp.items" :key="item.industry_code">
              <td>{{ idx + 1 }}</td>
              <td>{{ item.name_cn || item.industry_code }}（{{ item.industry_code }}）</td>
              <td class="mono" :class="item.correlation === null ? 'text-warn' : ''">
                {{ item.correlation === null ? '—' : item.correlation.toFixed(4) }}
              </td>
              <td class="mono">{{ item.sample_count }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>

    <!-- 扩散模块 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">数量占比扩散</span>
        <div class="controls">
          <button class="query-btn" :disabled="diffusionLoading" @click="queryDiffusion()">
            重新计算
          </button>
        </div>
      </div>
      <div v-if="diffusionError" class="error-banner">{{ diffusionError }}</div>
      <div v-if="diffusionIssues.length" class="issue-list">
        <div
          v-for="(issue, idx) in diffusionIssues"
          :key="`${issue.code}-${idx}`"
          class="issue-item"
          :class="issue.level"
        >
          <span class="issue-code">{{ issue.code }}</span>
          <span class="issue-message">{{ issue.message }}</span>
          <span v-if="issue.count !== null" class="issue-count">×{{ issue.count }}</span>
          <span v-if="issue.sample_dates.length" class="issue-samples">
            示例：{{ issue.sample_dates.join('、') }}
          </span>
        </div>
      </div>
      <div v-if="diffusionLoading && !diffusionResp" class="empty">
        扩散计算中（服务端按行业分批加载成员股收盘，单批内存受限）...
      </div>
      <div v-else-if="!diffusionResp" class="empty">
        暂无扩散结果，点击“查询扩散”开始（默认近 3 年，单次上限约 6 年）。
      </div>
      <template v-else>
        <div v-if="diffusionMeta" class="meta-line">
          <span>请求 {{ diffusionMeta.requested_start }} ~ {{ diffusionMeta.requested_end }}</span>
          <span>有效 {{ rangeText(diffusionMeta.effective_start, diffusionMeta.effective_end) }}</span>
          <span>输出交易日 {{ diffusionMeta.output_days }}</span>
          <span>上限 {{ diffusionMeta.max_range_days }} 自然日</span>
        </div>

        <div class="sub-block">
          <div class="sub-title">扩散时序（最多同图 8 个行业）</div>
          <div class="chip-row">
            <button
              v-for="code in latestRankingCodes"
              :key="code"
              class="chip"
              :class="{ active: chartCodes.includes(code) }"
              :disabled="!chartCodes.includes(code) && chartCodes.length >= 8"
              @click="toggleChartCode(code)"
            >
              {{ codeName(code) }}
            </button>
          </div>
          <div class="chart-area small" ref="diffChartEl"></div>
        </div>

        <div class="sub-block" v-if="withCoverage">
          <div class="sub-title">全行业扩散覆盖度（有效样本/成员数，逐日均值）</div>
          <div class="chart-area small" ref="coverageChartEl"></div>
          <div class="chart-note">
            个股数据参差导致的覆盖缺口：停牌/未上市/退市/拉取缺失都会降低覆盖度；
            某行业某日有效样本为 0 时扩散为 NaN。
          </div>
        </div>

        <div class="sub-block">
          <div class="sub-title">最新日扩散排名</div>
          <div class="controls">
            <select v-model="diffusionDate" class="select-input" :disabled="!diffusionDates.length">
              <option v-for="d in diffusionDates" :key="d" :value="d">{{ d }}</option>
            </select>
          </div>
          <div v-if="!rankingAtDate.length" class="empty">
            所选日期无有效扩散（请检查数据覆盖/问题区）。
          </div>
          <table v-else class="data-table">
            <thead>
              <tr>
                <th>排名</th>
                <th>行业</th>
                <th>扩散值</th>
                <th>有效样本</th>
                <th>成员数</th>
                <th>覆盖度</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in rankingAtDate" :key="row.code">
                <td>{{ idx + 1 }}</td>
                <td>{{ row.name }}（{{ row.code }}）</td>
                <td class="mono">{{ row.value.toFixed(4) }}</td>
                <td class="mono">{{ row.valid_count ?? '—' }}</td>
                <td class="mono">{{ row.member_count ?? '—' }}</td>
                <td class="mono">
                  {{ row.coverage !== null && row.coverage !== undefined
                    ? row.coverage.toFixed(4)
                    : '—' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <details class="fold">
          <summary>扩散数据覆盖（{{ diffusionMeta?.coverage.length ?? 0 }} 个行业）</summary>
          <table class="data-table">
            <thead>
              <tr>
                <th>行业</th>
                <th>成员股</th>
                <th>输出天数</th>
                <th>无样本日</th>
                <th>有效点数</th>
                <th>平均有效样本</th>
                <th>首个有效日</th>
                <th>最后有效日</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in diffusionMeta?.coverage ?? []" :key="item.industry_code">
                <td>{{ item.name_cn || item.industry_code }}</td>
                <td class="mono">{{ item.member_count }}</td>
                <td class="mono">{{ item.output_days }}</td>
                <td class="mono" :class="item.no_sample_days > 0 ? 'text-warn' : 'text-ok'">
                  {{ item.no_sample_days }}
                </td>
                <td class="mono">{{ item.valid_count }}</td>
                <td class="mono">{{ item.avg_sample_count?.toFixed(1) ?? '—' }}</td>
                <td class="mono">{{ item.valid_from ?? '—' }}</td>
                <td class="mono">{{ item.valid_until ?? '—' }}</td>
              </tr>
            </tbody>
          </table>
        </details>
        <div v-if="diffusionMeta?.rules.length" class="rule-note">
          <div v-for="rule in diffusionMeta.rules" :key="rule" class="rule-item">• {{ rule }}</div>
        </div>
      </template>
    </div>

    <!-- 方法论说明 -->
    <div class="card">
      <div class="card-header">
        <span class="card-title">研究方法与口径说明</span>
      </div>
      <div class="method-body">
        <section>
          <h3>RRG（相对旋转图）</h3>
          <ul>
            <li>RS = 行业收盘 / 基准等权组合 × 100；基准为所选行业剔除“综合”后的日收益等权累乘净值。</li>
            <li>
              RS-Ratio = MA(smooth)（100 × RS_t / RS_{t-lookback_ratio}）；
              RS-Momentum = MA(smooth)（100 × RSR_t / RSR_{t-lookback_mom}），中枢均为 100。
            </li>
            <li>象限：1 领先（双 &gt;100）、2 改善（Ratio&lt;100、Mom&gt;100）、3 滞后（双 &lt;100）、4 疲软（Ratio&gt;100、Mom&lt;100）；恰为 100 或 NaN 不归边。</li>
            <li>数据问题（缺交易日严格置空、warm-up 不足、请求越界）全部进入“问题区/覆盖表”，不再静默。</li>
          </ul>
        </section>
        <section>
          <h3>数量占比扩散</h3>
          <ul>
            <li>判涨：close_t &gt; close_{t-lookback}（默认 220 个交易日），随后做 20 日 MA 平滑。</li>
            <li>有效样本：当日属于该行业且两日收盘均非空的成员股；无有效样本日扩散为 NaN，且会污染平滑窗口。</li>
            <li>收盘为未复权口径；除权除息会引入人工涨跌，页面仅提示、不做校正（口径问题另行专项）。</li>
            <li>计算按“行业 → 成员股 → 收盘”分批加载，逐行业在内存中计算后释放，避免全市场一次物化。</li>
          </ul>
        </section>
        <section>
          <h3>信号规则（研究预览，本页不执行策略与回测）</h3>
          <ul>
            <li>A：象限过滤（默认 1+2）后按距 (100,100) 欧氏距离取 top_n。</li>
            <li>B：扩散指标每日 top_n。</li>
            <li>C（复刻口径）：扩散 top_n 先选，再剔除不处于 1+2 象限的行业，且不补足。</li>
            <li>研究结论与参数仅反映数据观察；正式落地需走后续策略管线专项。</li>
          </ul>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * RRG / 扩散 数据研究调试台。
 *
 * 仅做研究展示：RRG 四象限与历史轨迹、扩散时序/排名/覆盖度、数据问题显性化，
 * 不创建策略、不写因子表、不触发回测。查询参数与数据上限均有服务端校验与提示。
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { AxiosError } from 'axios'
import type { ECharts } from 'echarts'

import {
  fetchDiffusion,
  fetchIndexDiffusionDebug,
  fetchIndustryIndexCorrelation,
  fetchIndustryIndexes,
  fetchRRG,
  type IndustryDiffusionPoint,
  type IndustryDiffusionResponse,
  type IndustryIndexCorrelationResponse,
  type IndustryIndexSummary,
  type IndexDiffusionDebugResponse,
  type IndustryLabIssue,
  type IndustryRRGResponse,
} from '../api/industry'
import { fetchBenchmarkIndexes } from '../api/market_data'
import type { BenchmarkIndex } from '../types/api'

const industries = ref<IndustryIndexSummary[]>([])
const indexes = ref<BenchmarkIndex[]>([])
const selectedCodes = ref<string[]>([])
const startDate = ref('')
const endDate = ref('')
const activePreset = ref('近3年')
const lookbackRatio = ref(220)
const lookbackMom = ref(60)
const smoothWindow = ref(20)
const diffusionLookback = ref(220)
const diffusionSmooth = ref(20)
const withCoverage = ref(true)

const correlationIndexCode = ref('')
const correlationResp = ref<IndustryIndexCorrelationResponse | null>(null)
const correlationLoading = ref(false)
const correlationError = ref('')

const indexDiffusionCode = ref('')
const indexDiffusionDate = ref('')
const indexDiffusionResp = ref<IndexDiffusionDebugResponse | null>(null)
const indexDiffusionLoading = ref(false)
const indexDiffusionError = ref('')

const rrgResp = ref<IndustryRRGResponse | null>(null)
const rrgLoading = ref(false)
const rrgError = ref('')
const rrgDate = ref('')
const showTrail = ref(true)
const trailDays = ref(60)
const rrgChartEl = ref<HTMLElement | null>(null)
let rrgChart: ECharts | null = null
let rrgResizeObserver: ResizeObserver | null = null

const diffusionResp = ref<IndustryDiffusionResponse | null>(null)
const diffusionLoading = ref(false)
const diffusionError = ref('')
const diffusionDate = ref('')
const chartCodes = ref<string[]>([])
const diffChartEl = ref<HTMLElement | null>(null)
const coverageChartEl = ref<HTMLElement | null>(null)
let diffChart: ECharts | null = null
let coverageChart: ECharts | null = null
let diffResizeObserver: ResizeObserver | null = null

const rangePresets = [
  { label: '近1年', days: 365 },
  { label: '近3年', days: 1095 },
  { label: '近6年', days: 2190 },
  { label: '近10年', days: 3650 },
]

const warmupDays = computed(
  () => lookbackRatio.value + lookbackMom.value + 2 * (smoothWindow.value - 1),
)

const rrgMeta = computed(() => rrgResp.value?.meta ?? null)
const rrgIssues = computed<IndustryLabIssue[]>(() => rrgMeta.value?.issues ?? [])
const diffusionMeta = computed(() => diffusionResp.value?.meta ?? null)
const diffusionIssues = computed<IndustryLabIssue[]>(() => diffusionMeta.value?.issues ?? [])

const codeToName = computed(() => {
  const map: Record<string, string> = {}
  for (const item of industries.value) map[item.industry_code] = item.name_cn
  return map
})

function codeName(code: string): string {
  return codeToName.value[code] ?? code
}

function toDateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function rangeText(start: string | null | undefined, end: string | null | undefined): string {
  if (start && end) return `${start} ~ ${end}`
  return '—'
}

/** 格式化原始占比或已乘以 100 的因子百分数。 */
function formatPercent(value: number | null, alreadyPercent = false): string {
  if (value === null) return '—'
  const percent = alreadyPercent ? value : value * 100
  return `${percent.toFixed(2)}%`
}

function errorText(e: unknown): string {
  const err = e as AxiosError<{ detail?: unknown }>
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  const headers = err?.config?.headers
  let reqId = ''
  if (headers && typeof headers.get === 'function') {
    const value = headers.get('X-Request-ID')
    if (value) reqId = String(value)
  } else if (headers) {
    reqId = String((headers as Record<string, unknown>)['X-Request-ID'] ?? '')
  }
  const idText = reqId ? `（request_id=${reqId}，可在后端日志中检索该 ID）` : ''
  if (!err?.response) {
    if (err?.code === 'ECONNABORTED') {
      const timeout = err?.config?.timeout ?? 30000
      return (
        `请求超时：等待超过 ${timeout}ms${idText}。服务端可能仍在计算，` +
        '请查看后端日志中“即时计算完成”的耗时；若超过 15s 建议缩短日期区间。'
      )
    }
    return `网络错误或服务不可达${idText}，请确认后端已启动且网络正常`
  }
  return `请求失败（HTTP ${err.response.status}）${idText}`
}

function initDates(): void {
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - 1095)
  endDate.value = toDateStr(end)
  startDate.value = toDateStr(start)
}

function applyPreset(preset: { label: string; days: number }): void {
  activePreset.value = preset.label
  const end = new Date()
  const start = new Date()
  start.setDate(start.getDate() - preset.days)
  endDate.value = toDateStr(end)
  startDate.value = toDateStr(start)
}

function selectAllIndustries(): void {
  selectedCodes.value = industries.value.map((item) => item.industry_code)
}

function clearIndustries(): void {
  selectedCodes.value = []
}

async function loadIndustries(): Promise<void> {
  try {
    industries.value = await fetchIndustryIndexes()
    selectAllIndustries()
  } catch (e) {
    industries.value = []
    rrgError.value = errorText(e)
  }
}

/** 加载可用于行业相关度对照的活跃指数目录。 */
async function loadIndexes(): Promise<void> {
  try {
    indexes.value = await fetchBenchmarkIndexes()
    correlationIndexCode.value = indexes.value[0]?.index_code ?? ''
    indexDiffusionCode.value = indexes.value[0]?.index_code ?? ''
  } catch (e) {
    indexes.value = []
    correlationError.value = errorText(e)
  }
}

/** 计算当前日期范围内所选行业与指定指数的严格日收益相关度。 */
async function queryCorrelation(): Promise<void> {
  if (correlationLoading.value || !correlationIndexCode.value) return
  correlationLoading.value = true
  correlationError.value = ''
  correlationResp.value = null
  try {
    correlationResp.value = await fetchIndustryIndexCorrelation(
      correlationIndexCode.value,
      startDate.value,
      endDate.value,
      selectedCodes.value,
    )
  } catch (e) {
    correlationError.value = errorText(e)
  } finally {
    correlationLoading.value = false
  }
}

/** 即时计算指定指数、指定交易日的成分扩散调试结果。 */
async function queryIndexDiffusion(): Promise<void> {
  if (indexDiffusionLoading.value || !indexDiffusionCode.value || !indexDiffusionDate.value) return
  indexDiffusionLoading.value = true
  indexDiffusionError.value = ''
  indexDiffusionResp.value = null
  try {
    indexDiffusionResp.value = await fetchIndexDiffusionDebug(
      indexDiffusionCode.value,
      indexDiffusionDate.value,
    )
  } catch (e) {
    indexDiffusionError.value = errorText(e)
  } finally {
    indexDiffusionLoading.value = false
  }
}

/** 查询 RRG（结果含数据问题与覆盖元信息） */
async function queryRRG(): Promise<void> {
  if (rrgLoading.value) return
  rrgLoading.value = true
  rrgError.value = ''
  rrgResp.value = null
  try {
    const resp = await fetchRRG(startDate.value, endDate.value, selectedCodes.value, {
      lookbackRatio: lookbackRatio.value,
      lookbackMom: lookbackMom.value,
      smoothWindow: smoothWindow.value,
    })
    rrgResp.value = resp
    const dates = rrgDatesFrom(resp)
    rrgDate.value = dates.length ? dates[dates.length - 1] : ''
    await nextTick()
    await renderRrgChart()
  } catch (e) {
    rrgError.value = errorText(e)
  } finally {
    rrgLoading.value = false
  }
}

/** 查询扩散（按行业分批计算，页面不再并发重复计算） */
async function queryDiffusion(): Promise<void> {
  if (diffusionLoading.value) return
  diffusionLoading.value = true
  diffusionError.value = ''
  diffusionResp.value = null
  try {
    const resp = await fetchDiffusion(
      startDate.value,
      endDate.value,
      selectedCodes.value,
      {
        diffusionLookback: diffusionLookback.value,
        smoothWindow: diffusionSmooth.value,
      },
      withCoverage.value,
    )
    diffusionResp.value = resp
    const dates = diffusionDatesFrom(resp)
    diffusionDate.value = dates.length ? dates[dates.length - 1] : ''
    const latest = latestRankingAt(diffusionDate.value)
    chartCodes.value = latest.slice(0, 8).map((row) => row.code)
    await nextTick()
    await Promise.all([renderDiffusionChart(), renderCoverageChart()])
  } catch (e) {
    diffusionError.value = errorText(e)
  } finally {
    diffusionLoading.value = false
  }
}

/** 顺序执行全部查询，避免 RRG/扩散同时全量计算造成内存叠加 */
async function queryAll(): Promise<void> {
  if (rrgLoading.value || diffusionLoading.value) return
  await queryRRG()
  await queryDiffusion()
}

function rrgDatesFrom(resp: IndustryRRGResponse): string[] {
  const seen = new Set<string>()
  for (const p of resp.points) {
    if (p.rs_ratio !== null && p.rs_momentum !== null) seen.add(p.trade_date)
  }
  return Array.from(seen).sort()
}

function diffusionDatesFrom(resp: IndustryDiffusionResponse): string[] {
  const seen = new Set<string>()
  for (const p of resp.points) {
    if (p.value !== null) seen.add(p.trade_date)
  }
  return Array.from(seen).sort()
}

const rrgDates = computed(() => (rrgResp.value ? rrgDatesFrom(rrgResp.value) : []))
const diffusionDates = computed(() =>
  diffusionResp.value ? diffusionDatesFrom(diffusionResp.value) : [],
)

/** 指定日期各行业 RRG 点（仅有效值） */
function pointsAtDate(date: string): Array<{
  code: string
  name: string
  ratio: number
  mom: number
  quadrant: number | null
}> {
  if (!rrgResp.value) return []
  return rrgResp.value.points
    .filter(
      (p) =>
        p.trade_date === date &&
        p.rs_ratio !== null &&
        p.rs_momentum !== null &&
        selectedCodes.value.includes(p.industry_code),
    )
    .map((p) => ({
      code: p.industry_code,
      name: p.name_cn || p.industry_code,
      ratio: p.rs_ratio as number,
      mom: p.rs_momentum as number,
      quadrant: p.quadrant,
    }))
}

/** 渲染 RRG 散点 + 历史轨迹 */
async function renderRrgChart(): Promise<void> {
  if (!rrgChartEl.value || !rrgDate.value) return
  const echarts = await import('echarts')
  rrgChart?.dispose()
  rrgChart = echarts.init(rrgChartEl.value, undefined, { renderer: 'canvas' })
  const latest = pointsAtDate(rrgDate.value)
  if (!latest.length) {
    rrgChart.setOption({ title: { text: '该日期无有效 RRG 数据', left: 'center', top: 'middle' } })
    return
  }

  const quadColors: Record<number, string> = {
    1: '#10b981',
    2: '#3b82f6',
    3: '#f97316',
    4: '#ef4444',
  }
  const palette = [
    '#64748b',
    '#a78bfa',
    '#22d3ee',
    '#f472b6',
    '#a3e635',
    '#fb923c',
    '#e879f9',
    '#34d399',
  ]
  const scatterData = latest.map((p) => ({
    name: p.name,
    value: [p.ratio, p.mom],
    itemStyle: {
      color: p.quadrant ? quadColors[p.quadrant] : '#94a3b8',
    },
  }))
  const trailSeries: unknown[] = []
  if (showTrail.value && rrgDate.value) {
    const dateIndex = rrgDates.value.indexOf(rrgDate.value)
    const startIdx = Math.max(0, dateIndex - trailDays.value + 1)
    const trailDates = rrgDates.value.slice(startIdx, dateIndex + 1)
    for (const code of selectedCodes.value) {
      const trail: number[][] = []
      for (const d of trailDates) {
        const p = rrgResp.value?.points.find(
          (point) => point.trade_date === d && point.industry_code === code,
        )
        if (
          p &&
          p.rs_ratio !== null &&
          p.rs_momentum !== null
        ) {
          trail.push([p.rs_ratio, p.rs_momentum])
        }
      }
      if (trail.length < 2) continue
      trailSeries.push({
        name: codeName(code),
        type: 'line',
        data: trail,
        showSymbol: false,
        smooth: false,
        lineStyle: { width: 1, opacity: 0.55, color: palette[selectedCodes.value.indexOf(code) % palette.length] },
        emphasis: { disabled: true },
      })
    }
  }

  const allRatio = [100, ...latest.map((p) => p.ratio), ...(trailSeries.length ? (trailSeries as Array<{ data: number[][] }>).flatMap((s) => s.data.map((v) => v[0])) : [])]
  const allMom = [100, ...latest.map((p) => p.mom), ...(trailSeries.length ? (trailSeries as Array<{ data: number[][] }>).flatMap((s) => s.data.map((v) => v[1])) : [])]
  const minRatio = Math.min(...allRatio)
  const maxRatio = Math.max(...allRatio)
  const minMom = Math.min(...allMom)
  const maxMom = Math.max(...allMom)
  const padR = Math.max(3, (maxRatio - minRatio) * 0.06)
  const padM = Math.max(3, (maxMom - minMom) * 0.06)

  rrgChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (params: { data: { name: string; value: number[] } }) =>
        `${params.data.name}<br/>RS-Ratio: ${params.data.value[0].toFixed(2)}<br/>RS-Momentum: ${params.data.value[1].toFixed(2)}`,
    },
    legend: { show: false },
    grid: { left: 70, right: 40, top: 40, bottom: 50 },
    xAxis: {
      name: 'RS-Ratio',
      type: 'value',
      min: Math.min(minRatio, 100) - padR,
      max: Math.max(maxRatio, 100) + padR,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
    },
    yAxis: {
      name: 'RS-Momentum',
      type: 'value',
      min: Math.min(minMom, 100) - padM,
      max: Math.max(maxMom, 100) + padM,
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
    },
    graphic: [
      { type: 'text', left: '63%', top: '7%', style: { text: '1 领先', fill: '#10b981', fontSize: 12 } },
      { type: 'text', left: '7%', top: '7%', style: { text: '2 改善', fill: '#3b82f6', fontSize: 12 } },
      { type: 'text', left: '7%', bottom: '8%', style: { text: '3 滞后', fill: '#f97316', fontSize: 12 } },
      { type: 'text', left: '63%', bottom: '8%', style: { text: '4 疲软', fill: '#ef4444', fontSize: 12 } },
    ],
    series: [
      ...trailSeries,
      {
        type: 'scatter',
        data: scatterData,
        symbolSize: 14,
        label: {
          show: true,
          position: 'top',
          formatter: (params: { data: { name: string } }) => params.data.name,
          fontSize: 10,
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#64748b', type: 'dashed', width: 1 },
          data: [{ xAxis: 100 }, { yAxis: 100 }],
        },
      },
    ],
  })
}

const pointMap = computed(() => {
  const map: Record<string, Record<string, IndustryDiffusionPoint>> = {}
  for (const p of diffusionResp.value?.points ?? []) {
    map[p.trade_date] ??= {}
    map[p.trade_date][p.industry_code] = p
  }
  return map
})

/** 指定日扩散排名（有效值降序） */
function latestRankingAt(date: string): Array<{
  code: string
  name: string
  value: number
  valid_count: number | null
  member_count: number | null
  coverage: number | null
}> {
  const dayMap = pointMap.value[date] ?? {}
  return Object.values(dayMap)
    .filter((p) => p.value !== null && selectedCodes.value.includes(p.industry_code))
    .map((p) => ({
      code: p.industry_code,
      name: p.name_cn || p.industry_code,
      value: p.value as number,
      valid_count: p.valid_count,
      member_count: p.member_count,
      coverage: p.coverage,
    }))
    .sort((a, b) => b.value - a.value)
}

const rankingAtDate = computed(() => latestRankingAt(diffusionDate.value))

const latestRankingCodes = computed(() => {
  if (!diffusionDate.value) return []
  return latestRankingAt(diffusionDate.value).map((row) => row.code)
})

function toggleChartCode(code: string): void {
  if (chartCodes.value.includes(code)) {
    chartCodes.value = chartCodes.value.filter((c) => c !== code)
  } else if (chartCodes.value.length < 8) {
    chartCodes.value = [...chartCodes.value, code]
  }
  void renderDiffusionChart()
}

/** 渲染扩散时序折线 */
async function renderDiffusionChart(): Promise<void> {
  if (!diffChartEl.value || !diffusionResp.value || !diffusionDates.value.length) return
  const echarts = await import('echarts')
  diffChart?.dispose()
  diffChart = echarts.init(diffChartEl.value, undefined, { renderer: 'canvas' })
  const palette = [
    '#3b82f6',
    '#10b981',
    '#f59e0b',
    '#ec4899',
    '#8b5cf6',
    '#14b8a6',
    '#f97316',
    '#64748b',
  ]
  const series = chartCodes.value.map((code, idx) => ({
    name: codeName(code),
    type: 'line' as const,
    showSymbol: false,
    connectNulls: false,
    lineStyle: { width: 1.5, color: palette[idx % palette.length] },
    itemStyle: { color: palette[idx % palette.length] },
    data: diffusionDates.value.map((d) => {
      const p = pointMap.value[d]?.[code]
      return p?.value ?? null
    }),
  }))
  diffChart.setOption({
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis' },
    legend: {
      type: 'scroll',
      textStyle: { color: '#94a3b8', fontSize: 11 },
      top: 0,
    },
    grid: { left: 70, right: 30, top: 34, bottom: 60 },
    xAxis: {
      type: 'category',
      data: diffusionDates.value,
      axisLabel: { color: '#94a3b8', fontSize: 11, hideOverlap: true },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      axisLabel: { color: '#94a3b8', fontSize: 11 },
      splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 22, bottom: 8 }],
    series,
  })
}

/** 渲染全行业覆盖度（逐日均值，有效样本/成员数） */
async function renderCoverageChart(): Promise<void> {
  if (!coverageChartEl.value || !diffusionResp.value || !withCoverage.value) return
  const echarts = await import('echarts')
  coverageChart?.dispose()
  coverageChart = echarts.init(coverageChartEl.value, undefined, { renderer: 'canvas' })
  const seriesData: Array<number | null> = diffusionDates.value.map((d) => {
    const dayMap = pointMap.value[d] ?? {}
    const points = Object.values(dayMap).filter(
      (p) =>
        p.coverage !== null &&
        p.coverage !== undefined &&
        p.member_count !== null &&
        p.member_count !== undefined &&
        p.member_count > 0 &&
        selectedCodes.value.includes(p.industry_code),
    )
    if (!points.length) return null
    return points.reduce((sum, p) => sum + (p.coverage ?? 0), 0) / points.length
  })
  coverageChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v: number) => `${(v * 100).toFixed(1)}%`,
    },
    grid: { left: 70, right: 30, top: 24, bottom: 60 },
    xAxis: {
      type: 'category',
      data: diffusionDates.value,
      axisLabel: { color: '#94a3b8', fontSize: 11, hideOverlap: true },
      axisLine: { lineStyle: { color: '#334155' } },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 1,
      axisLabel: { color: '#94a3b8', fontSize: 11, formatter: (v: number) => `${Math.round(v * 100)}%` },
      splitLine: { lineStyle: { color: '#334155', type: 'dashed' } },
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 22, bottom: 8 }],
    series: [
      {
        name: '全行业覆盖度均值',
        type: 'line',
        showSymbol: false,
        connectNulls: true,
        lineStyle: { color: '#22d3ee', width: 1.5 },
        itemStyle: { color: '#22d3ee' },
        data: seriesData,
      },
    ],
  })
}

function renderAllCharts(): void {
  void renderRrgChart()
  void renderDiffusionChart()
  void renderCoverageChart()
}

watch(
  [rrgDate, showTrail, trailDays, rrgResp],
  () => {
    nextTick(() => void renderRrgChart())
  },
  { flush: 'post' },
)

watch(
  [diffusionDate, diffusionResp],
  () => {
    nextTick(() => {
      void renderDiffusionChart()
      void renderCoverageChart()
    })
  },
  { flush: 'post' },
)

function setupResizeObservers(): void {
  const target = rrgChartEl.value
  if (target) {
    rrgResizeObserver = new ResizeObserver(() => rrgChart?.resize())
    rrgResizeObserver.observe(target)
  }
  const target2 = diffChartEl.value
  if (target2) {
    diffResizeObserver = new ResizeObserver(() => {
      diffChart?.resize()
      coverageChart?.resize()
    })
    diffResizeObserver.observe(target2)
    if (coverageChartEl.value) diffResizeObserver.observe(coverageChartEl.value)
  }
}

onMounted(async () => {
  initDates()
  indexDiffusionDate.value = endDate.value
  await Promise.all([loadIndustries(), loadIndexes()])
  setupResizeObservers()
  await queryRRG()
})

onUnmounted(() => {
  rrgResizeObserver?.disconnect()
  diffResizeObserver?.disconnect()
  rrgResizeObserver = null
  diffResizeObserver = null
  rrgChart?.dispose()
  diffChart?.dispose()
  coverageChart?.dispose()
  rrgChart = null
  diffChart = null
  coverageChart = null
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
.factor-desc {
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
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  flex-wrap: wrap;
  gap: 8px;
}
.card-title {
  font-weight: 600;
  font-size: 15px;
}
.controls {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.query-btn {
  background: var(--accent);
  color: #fff;
  border: none;
  border-radius: var(--radius-sm);
  padding: 7px 14px;
  font-size: 13px;
  cursor: pointer;
}
.query-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.lab-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.form-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}
.form-row.wrap {
  flex-wrap: wrap;
}
.form-row label {
  min-width: 64px;
  color: #94a3b8;
  font-size: 13px;
}
.num-input,
.select-input,
.date-input {
  background: var(--surface-2, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 4px;
  padding: 4px 6px;
  font-size: 12px;
}
.num-input,
.select-input {
  width: 92px;
}
.select-input.wide {
  width: 210px;
}
.field-hint {
  color: #64748b;
  font-size: 12px;
  margin-right: 6px;
}
.warmup-hint {
  color: #64748b;
  font-size: 12px;
  margin-left: 4px;
}
.sep {
  color: #64748b;
}
.inline-check {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #cbd5e1;
  margin-left: 6px;
}
.ind-check {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  color: #cbd5e1;
}
.scope-actions {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}
.link-btn {
  background: transparent;
  border: none;
  color: var(--accent);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
.range-btn {
  background: transparent;
  color: var(--text-muted);
  border: 1px solid transparent;
  padding: 3px 8px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
}
.range-btn.active {
  background: rgba(59, 130, 246, 0.15);
  color: var(--accent);
}
.error-banner {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.35);
  color: #fca5a5;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  margin-bottom: 10px;
}
.issue-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}
.issue-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  border-radius: var(--radius-sm);
  padding: 6px 10px;
  font-size: 12px;
}
.issue-item.warn {
  background: rgba(245, 158, 11, 0.1);
  border: 1px solid rgba(245, 158, 11, 0.3);
  color: #fbbf24;
}
.issue-item.error {
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.35);
  color: #fca5a5;
}
.issue-item.info {
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid rgba(59, 130, 246, 0.25);
  color: #93c5fd;
}
.issue-code {
  font-family: monospace;
  font-size: 11px;
  background: rgba(255, 255, 255, 0.06);
  padding: 2px 6px;
  border-radius: 3px;
}
.issue-count,
.issue-samples {
  color: inherit;
  opacity: 0.85;
}
.empty {
  padding: 50px 0;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}
.meta-line {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  font-size: 12px;
  color: var(--text-muted);
  padding: 8px 10px;
  background: var(--surface-2, rgba(255, 255, 255, 0.03));
  border-radius: var(--radius-sm);
  margin-bottom: 10px;
}
.chart-area {
  height: 460px;
  width: 100%;
}
.chart-area.small {
  height: 300px;
}
.chart-note {
  color: #64748b;
  font-size: 12px;
  padding: 6px 0;
}
.sub-block {
  margin-top: 14px;
  border-top: 1px solid var(--border);
  padding-top: 12px;
}
.sub-title {
  font-size: 13px;
  color: #cbd5e1;
  margin-bottom: 8px;
  font-weight: 600;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}
.chip {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 12px;
  border-radius: 20px;
  padding: 3px 10px;
  cursor: pointer;
}
.chip.active {
  background: rgba(59, 130, 246, 0.15);
  border-color: var(--accent);
  color: var(--accent);
}
.chip:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.data-table th,
.data-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  color: #cbd5e1;
}
.data-table th {
  color: var(--text-muted);
  font-weight: 500;
  white-space: nowrap;
}
.data-table tr:hover td {
  background: var(--surface-2, rgba(255, 255, 255, 0.03));
}
.mono {
  font-family: monospace;
}
.text-warn {
  color: #fbbf24;
}
.text-ok {
  color: #34d399;
}
.fold {
  margin-top: 12px;
}
.fold summary {
  cursor: pointer;
  color: var(--accent);
  font-size: 13px;
  margin-bottom: 8px;
}
.rule-note {
  margin-top: 10px;
  padding: 8px 12px;
  background: var(--surface-2, rgba(255, 255, 255, 0.03));
  border-radius: var(--radius-sm);
}
.rule-item {
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.8;
}
.method-body section {
  margin-bottom: 14px;
}
.method-body h3 {
  color: #e2e8f0;
  font-size: 14px;
  margin-bottom: 6px;
}
.method-body li {
  color: #94a3b8;
  font-size: 12.5px;
  line-height: 1.9;
}
</style>
