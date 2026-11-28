<template>
  <main class="daily-report-page">
    <div v-if="initialLoading" class="page-state">
      <div class="loading-spinner"></div>
      <span>加载日报...</span>
    </div>
    <div v-else-if="loadError" class="page-state page-state-error">
      <strong>日报加载失败</strong>
      <span>{{ loadError }}</span>
      <button type="button" class="section-toggle" @click="loadInitial">重试</button>
    </div>
    <div v-else-if="reports.length === 0" class="page-state">
      <strong>暂无已发布日报</strong>
      <span>请先调用 market-daily-report skill 生成并发布日报。</span>
    </div>
    <div v-else-if="detailLoading" class="page-state">
      <div class="loading-spinner"></div>
      <span>加载 {{ selectedDate }} 日报...</span>
    </div>
    <template v-else-if="report">
    <div class="snapshot-bar">
      <span class="snapshot-mark">已发布</span>
      <span>
        第 {{ detail?.revision_no }} 版 · {{ report.readiness_status === 'partial' ? '部分数据降级' : '数据完整' }}
      </span>
      <span class="snapshot-time">发布于 {{ formatCnTime(detail?.published_at) }}</span>
    </div>

    <div class="date-toolbar">
      <div class="date-tabs" role="tablist" aria-label="选择日报日期">
        <button
          v-for="date in availableDates"
          :key="date"
          type="button"
          role="tab"
          :aria-selected="selectedDate === date"
          :class="{ active: selectedDate === date }"
          @click="selectDate(date)"
        >
          <span>{{ formatDateButton(date) }}</span>
          <small>{{ formatWeekday(date) }}</small>
        </button>
      </div>
      <div class="next-trade">
        <span>下一交易日</span>
        <strong>{{ report.next_trade_date }}</strong>
      </div>
    </div>

    <header class="report-masthead">
      <div class="masthead-line">
        <span>每日量化研究简报</span>
        <span class="market-domain">中国 A 股 · 日频</span>
        <span class="issue-status">{{ report.market_regime }}</span>
      </div>

      <div class="masthead-grid">
        <div class="headline-block">
          <div class="date-lockup" aria-label="报告日期">
            <span class="date-year">{{ reportYear }}</span>
            <span class="date-main">{{ reportMonthDay }}</span>
            <span class="date-weekday">{{ reportWeekday }}</span>
          </div>
          <h1>{{ report.title }}</h1>
          <p class="report-subtitle">{{ report.subtitle }}</p>
        </div>

        <dl class="edition-meta">
          <div>
            <dt>数据截止</dt>
            <dd>{{ report.data_cutoff }}</dd>
          </div>
          <div>
            <dt>上涨家数</dt>
            <dd>
              {{ report.market_snapshot.breadth.advances }}
              <span class="meta-muted">/ {{ report.market_snapshot.breadth.total }}</span>
            </dd>
          </div>
          <div>
            <dt>全市场成交</dt>
            <dd>{{ formatCompactAmount(report.market_snapshot.turnover_billion) }}</dd>
          </div>
        </dl>
      </div>
    </header>

    <div v-if="report.warnings.length" class="data-alert" role="status">
      <span class="alert-mark">口径提示</span>
      <div>
        <p v-for="warning in report.warnings" :key="warning">{{ warning }}</p>
      </div>
    </div>

    <div class="report-layout">
      <aside class="section-rail" aria-label="报告目录">
        <div class="rail-sticky">
          <p class="rail-title">本期目录</p>
          <a v-for="section in sections" :key="section.id" :href="`#${section.id}`">
            <span>{{ section.label }}</span>
            <small>{{ section.hint }}</small>
          </a>
          <div class="rail-evidence">
            <strong>{{ report.claim_checks.length }}</strong>
            <span>条交叉核验</span>
          </div>
        </div>
      </aside>

      <article class="report-body">
        <section id="summary" class="report-section summary-section">
          <div class="section-heading">
            <h2>当日结论</h2>
            <span>{{ report.report_date }}</span>
          </div>

          <div class="thesis-block">
            <span class="thesis-index">结论</span>
            <p>{{ report.executive_summary }}</p>
          </div>

          <div class="day-delta">
            <div class="delta-heading">
              <span>较上一交易日</span>
              <small>{{ previousTimeline?.label ?? '—' }} → {{ currentTimeline?.label }}</small>
            </div>
            <div class="delta-grid">
              <div>
                <span>上涨家数占比</span>
                <strong>{{ currentTimeline?.advance_ratio_pct.toFixed(2) }}%</strong>
                <small :class="changeClass(breadthDelta)">
                  {{ formatSignedPp(breadthDelta) }}
                </small>
              </div>
              <div>
                <span>全市场成交</span>
                <strong>{{ formatCompactAmount(currentTimeline?.turnover_billion ?? 0) }}</strong>
                <small :class="changeClass(turnoverDeltaPct)">
                  {{ formatSignedPct(turnoverDeltaPct) }}
                </small>
              </div>
              <div>
                <span>中证1000</span>
                <strong :class="changeClass(currentTimeline?.csi1000_pct ?? 0)">
                  {{ formatSignedPct(currentTimeline?.csi1000_pct ?? 0) }}
                </strong>
                <small :class="changeClass(csi1000Delta)">
                  {{ formatSignedPp(csi1000Delta) }}
                </small>
              </div>
            </div>
          </div>
        </section>

        <section id="continuity" class="report-section">
          <div class="section-heading">
            <h2>连续变化</h2>
            <span>最近六个交易日 · 当前日高亮</span>
          </div>

          <div class="continuity-charts">
            <figure class="line-chart main-line-chart">
              <figcaption class="chart-header">
                <div>
                  <h3>指数单日涨跌</h3>
                  <span>当前日竖线标记</span>
                </div>
                <div class="chart-legend">
                  <span class="legend-csi300">沪深300</span>
                  <span class="legend-csi1000">中证1000</span>
                </div>
              </figcaption>
              <svg
                :viewBox="`0 0 ${mainChartBox.width} ${mainChartBox.height}`"
                role="img"
                aria-label="最近六个交易日沪深300和中证1000单日涨跌折线图"
              >
                <g class="chart-grid">
                  <line
                    v-for="tick in indexTicks"
                    :key="tick.value"
                    :x1="mainChartBox.left"
                    :x2="mainChartBox.width - mainChartBox.right"
                    :y1="tick.y"
                    :y2="tick.y"
                  />
                </g>
                <line
                  v-if="indexZeroY"
                  class="chart-zero"
                  :x1="mainChartBox.left"
                  :x2="mainChartBox.width - mainChartBox.right"
                  :y1="indexZeroY"
                  :y2="indexZeroY"
                />
                <line
                  class="chart-current-line"
                  :x1="selectedIndexX"
                  :x2="selectedIndexX"
                  :y1="mainChartBox.top"
                  :y2="mainChartBox.height - mainChartBox.bottom"
                />
                <text
                  class="chart-axis-label chart-current-label"
                  :x="selectedIndexX"
                  :y="13"
                  text-anchor="middle"
                >{{ currentTimeline?.label }}</text>
                <polyline class="series-csi300" :points="csi300Points" />
                <polyline class="series-csi1000" :points="csi1000Points" />
                <circle
                  v-for="dot in csi300Dots"
                  :key="`csi300-${dot.index}`"
                  class="chart-dot series-csi300-dot"
                  :class="{ current: dot.index === selectedTimelineIndex }"
                  :cx="dot.x"
                  :cy="dot.y"
                  :r="dot.index === selectedTimelineIndex ? 4.2 : 2.8"
                />
                <circle
                  v-for="dot in csi1000Dots"
                  :key="`csi1000-${dot.index}`"
                  class="chart-dot series-csi1000-dot"
                  :class="{ current: dot.index === selectedTimelineIndex }"
                  :cx="dot.x"
                  :cy="dot.y"
                  :r="dot.index === selectedTimelineIndex ? 4.2 : 2.8"
                />
                <text
                  v-for="tick in indexTicks"
                  :key="`label-${tick.value}`"
                  class="chart-axis-label"
                  :x="mainChartBox.left - 8"
                  :y="tick.y + 3"
                  text-anchor="end"
                >{{ formatChartPct(tick.value) }}</text>
                <text
                  v-for="label in timelineXLabels"
                  :key="label.trade_date"
                  class="chart-axis-label chart-x-label"
                  :x="label.mainX"
                  :y="mainChartBox.height - 9"
                  text-anchor="middle"
                >{{ label.label }}</text>
              </svg>
            </figure>

            <div class="side-line-charts">
              <figure class="line-chart mini-line-chart">
                <figcaption class="chart-header">
                  <div>
                    <h3>上涨家数占比</h3>
                    <span>全市场广度</span>
                  </div>
                </figcaption>
                <svg
                  :viewBox="`0 0 ${miniChartBox.width} ${miniChartBox.height}`"
                  role="img"
                  aria-label="最近六个交易日上涨家数占比折线图"
                >
                  <g class="chart-grid">
                    <line
                      v-for="tick in breadthTicks"
                      :key="tick.value"
                      :x1="miniChartBox.left"
                      :x2="miniChartBox.width - miniChartBox.right"
                      :y1="tick.y"
                      :y2="tick.y"
                    />
                  </g>
                  <line
                    class="chart-current-line"
                    :x1="selectedMiniX"
                    :x2="selectedMiniX"
                    :y1="miniChartBox.top"
                    :y2="miniChartBox.height - miniChartBox.bottom"
                  />
                  <polygon class="series-area series-breadth-area" :points="breadthArea" />
                  <polyline class="series-breadth" :points="breadthPoints" />
                  <circle
                    v-for="dot in breadthDots"
                    :key="`breadth-${dot.index}`"
                    class="chart-dot series-breadth-dot"
                    :class="{ current: dot.index === selectedTimelineIndex }"
                    :cx="dot.x"
                    :cy="dot.y"
                    :r="dot.index === selectedTimelineIndex ? 3.8 : 2.4"
                  />
                  <text
                    v-for="tick in breadthTicks"
                    :key="`breadth-${tick.value}`"
                    class="chart-axis-label"
                    :x="miniChartBox.left - 7"
                    :y="tick.y + 3"
                    text-anchor="end"
                  >{{ tick.value.toFixed(0) }}%</text>
                  <text
                    v-for="label in timelineXLabels"
                    :key="`breadth-x-${label.trade_date}`"
                    class="chart-axis-label chart-x-label"
                    :x="label.miniX"
                    :y="miniChartBox.height - 8"
                    text-anchor="middle"
                  >{{ label.label }}</text>
                </svg>
              </figure>

              <figure class="line-chart mini-line-chart">
                <figcaption class="chart-header">
                  <div>
                    <h3>全市场成交额</h3>
                    <span>交易活跃度</span>
                  </div>
                </figcaption>
                <svg
                  :viewBox="`0 0 ${miniChartBox.width} ${miniChartBox.height}`"
                  role="img"
                  aria-label="最近六个交易日全市场成交额折线图"
                >
                  <g class="chart-grid">
                    <line
                      v-for="tick in turnoverTicks"
                      :key="tick.value"
                      :x1="miniChartBox.left"
                      :x2="miniChartBox.width - miniChartBox.right"
                      :y1="tick.y"
                      :y2="tick.y"
                    />
                  </g>
                  <line
                    class="chart-current-line"
                    :x1="selectedMiniX"
                    :x2="selectedMiniX"
                    :y1="miniChartBox.top"
                    :y2="miniChartBox.height - miniChartBox.bottom"
                  />
                  <polygon class="series-area series-turnover-area" :points="turnoverArea" />
                  <polyline class="series-turnover" :points="turnoverPoints" />
                  <circle
                    v-for="dot in turnoverDots"
                    :key="`turnover-${dot.index}`"
                    class="chart-dot series-turnover-dot"
                    :class="{ current: dot.index === selectedTimelineIndex }"
                    :cx="dot.x"
                    :cy="dot.y"
                    :r="dot.index === selectedTimelineIndex ? 3.8 : 2.4"
                  />
                  <text
                    v-for="tick in turnoverTicks"
                    :key="`turnover-${tick.value}`"
                    class="chart-axis-label"
                    :x="miniChartBox.left - 7"
                    :y="tick.y + 3"
                    text-anchor="end"
                  >{{ formatChartAmount(tick.value) }}</text>
                  <text
                    v-for="label in timelineXLabels"
                    :key="`turnover-x-${label.trade_date}`"
                    class="chart-axis-label chart-x-label"
                    :x="label.miniX"
                    :y="miniChartBox.height - 8"
                    text-anchor="middle"
                  >{{ label.label }}</text>
                </svg>
              </figure>
            </div>
          </div>
          <p class="timeline-reading">{{ continuityReading }}</p>
        </section>

        <section id="sentiment" class="report-section sentiment-section">
          <div class="section-heading">
            <h2>市场情绪</h2>
            <span>过去 252 个交易日分位 · 冰点到沸点</span>
          </div>

          <div class="sentiment-overview">
            <div class="sentiment-reading">
              <div class="sentiment-score-line">
                <span>情绪温度</span>
                <strong>{{ report.sentiment.score.toFixed(1) }}</strong>
                <em :class="`sentiment-state-${report.sentiment.state}`">{{ report.sentiment.state_label }}</em>
              </div>
              <p>{{ report.sentiment.summary }}</p>
            </div>

            <div class="sentiment-history" aria-label="最近六个交易日情绪温度">
              <div v-for="point in timelineData" :key="point.trade_date" class="history-item">
                <span>{{ point.label }}</span>
                <div class="history-column">
                  <i :style="{ height: `${Math.max(point.sentiment_score, 4)}%` }"></i>
                </div>
                <strong>{{ point.sentiment_score.toFixed(1) }}</strong>
              </div>
            </div>
          </div>

          <div class="thermometer" aria-label="情绪温度区间">
            <div class="thermal-track">
              <span class="thermal-marker" :style="{ left: `${report.sentiment.score}%` }"></span>
            </div>
            <div class="thermal-labels">
              <span>冰点 0–20</span>
              <span>偏冷 20–45</span>
              <span>中性 45–65</span>
              <span>偏热 65–85</span>
              <span>沸点 85–100</span>
            </div>
          </div>

          <div class="sentiment-components">
            <div
              v-for="component in report.sentiment.components"
              :key="component.key"
              class="sentiment-component"
            >
              <div class="component-heading">
                <span>{{ component.label }}</span>
                <strong>{{ component.display_value }}</strong>
              </div>
              <div class="component-track">
                <span :style="{ width: `${component.percentile}%` }"></span>
              </div>
              <div class="component-meta">
                <span>{{ component.percentile.toFixed(1) }} 分位</span>
                <span>权重 {{ component.weight_pct }}%</span>
              </div>
              <p>{{ component.note }}</p>
            </div>
          </div>
        </section>

        <section id="market" class="report-section">
          <div class="section-heading">
            <h2>市场快照</h2>
            <span>{{ report.market_snapshot.trade_date }} 收盘</span>
          </div>

          <div class="index-tape">
            <div
              v-for="index in report.market_snapshot.indices"
              :key="index.code"
              class="index-cell"
            >
              <div class="index-cell-top">
                <span>{{ index.name }}</span>
                <small>{{ index.code }}</small>
              </div>
              <strong>{{ formatNumber(index.close, 2) }}</strong>
              <div class="index-cell-bottom">
                <span :class="changeClass(index.change_pct)">{{ formatSignedPct(index.change_pct) }}</span>
                <svg viewBox="0 0 100 28" role="img" :aria-label="`${index.name}近五个交易日走势示意`">
                  <polyline
                    :points="sparkPoints(index.trend)"
                    fill="none"
                    :stroke="index.change_pct >= 0 ? 'var(--rise)' : 'var(--fall)'"
                    stroke-width="2"
                    vector-effect="non-scaling-stroke"
                  />
                </svg>
              </div>
            </div>
          </div>

          <div class="market-metrics">
            <div class="turnover-metric">
              <span class="metric-label">全市场成交额</span>
              <strong>{{ formatCompactAmount(report.market_snapshot.turnover_billion) }}</strong>
              <span :class="['metric-change', changeClass(report.market_snapshot.turnover_vs_20d_pct)]">
                较此前 20 日均值 {{ formatSignedPct(report.market_snapshot.turnover_vs_20d_pct) }}
              </span>
            </div>

            <div class="breadth-metric">
              <div class="breadth-title">
                <span class="metric-label">上涨家数占比</span>
                <strong>{{ report.market_snapshot.breadth.advance_ratio_pct.toFixed(2) }}%</strong>
              </div>
              <div class="breadth-bar" aria-label="上涨、平盘、下跌家数占比">
                <span class="breadth-up" :style="{ width: `${breadthWidths.up}%` }"></span>
                <span class="breadth-flat" :style="{ width: `${breadthWidths.flat}%` }"></span>
                <span class="breadth-down" :style="{ width: `${breadthWidths.down}%` }"></span>
              </div>
              <div class="breadth-counts">
                <span>上涨 {{ report.market_snapshot.breadth.advances }}</span>
                <span>平盘 {{ report.market_snapshot.breadth.unchanged }}</span>
                <span>下跌 {{ report.market_snapshot.breadth.declines }}</span>
              </div>
            </div>

            <div class="secondary-metrics">
              <div>
                <span>涨跌中位数</span>
                <strong :class="changeClass(report.market_snapshot.breadth.median_change_pct)">
                  {{ formatSignedPct(report.market_snapshot.breadth.median_change_pct) }}
                </strong>
              </div>
              <div>
                <span>涨停 / 跌停</span>
                <strong>{{ report.market_snapshot.breadth.limit_up }} / {{ report.market_snapshot.breadth.limit_down }}</strong>
              </div>
              <div>
                <span>主力净流入</span>
                <strong :class="changeClass(report.market_snapshot.main_net_inflow_billion)">
                  {{ formatSignedNumber(report.market_snapshot.main_net_inflow_billion, 2) }} 亿元
                </strong>
              </div>
            </div>
          </div>

          <div class="signal-strip">
            <div v-for="signal in report.market_signals" :key="signal.key" class="signal-item">
              <span>{{ signal.label }}</span>
              <strong :class="`signal-${signal.tone}`">{{ signal.value }}</strong>
              <small>{{ signal.delta }}</small>
              <p>{{ signal.note }}</p>
              <div class="impact-line">
                <span :class="['impact-badge', `impact-${signal.impact}`]">
                  {{ impactLabel(signal.impact) }} · {{ impact_strengthLabel(signal.impact_strength) }}
                </span>
                <span v-for="sector in signal.affected_sectors" :key="sector" class="sector-tag">
                  {{ sector }}
                </span>
              </div>
            </div>
          </div>
        </section>

        <section id="anomaly" class="report-section">
          <div class="section-heading">
            <h2>异动雷达</h2>
            <span>单日冲击 × 五日趋势 × 成交验证</span>
          </div>

          <div class="index-anomalies">
            <article
              v-for="item in report.index_anomalies"
              :key="item.code"
              class="index-anomaly"
            >
              <div class="anomaly-title-row">
                <div>
                  <span>{{ item.name }}</span>
                  <small>{{ item.code }}</small>
                </div>
                <em>{{ item.anomaly_label }}</em>
              </div>
              <div class="anomaly-returns">
                <div>
                  <span>单日</span>
                  <strong :class="changeClass(item.change_1d_pct)">{{ formatSignedPct(item.change_1d_pct) }}</strong>
                </div>
                <div>
                  <span>五日</span>
                  <strong :class="changeClass(item.change_5d_pct)">{{ formatSignedPct(item.change_5d_pct) }}</strong>
                </div>
              </div>
              <div class="anomaly-reference">
                相对{{ item.reference_name }}
                <strong :class="changeClass(item.relative_pct)">{{ formatSignedPp(item.relative_pct) }}</strong>
              </div>
              <p>{{ item.analysis }}</p>
            </article>
          </div>

          <div class="stock-anomaly-head">
            <span>个股</span>
            <span>行业与主题</span>
            <span>单日 / 五日</span>
            <span>成交验证</span>
            <span>异动判断</span>
          </div>
          <div class="stock-anomalies">
            <article v-for="item in visibleStockAnomalies" :key="item.code" class="stock-anomaly-row">
              <div class="stock-identity">
                <strong>{{ item.name }}</strong>
                <small>{{ item.code }}</small>
                <em>{{ item.anomaly_label }}</em>
              </div>
              <div class="stock-taxonomy">
                <span class="taxonomy-industry">{{ item.industry ?? '行业未映射' }}</span>
                <span
                  v-for="theme in item.themes"
                  :key="theme"
                  class="taxonomy-theme"
                >{{ theme }}</span>
                <span v-if="item.themes.length === 0" class="taxonomy-empty">主题未覆盖</span>
              </div>
              <div class="stock-returns">
                <strong :class="changeClass(item.change_1d_pct)">{{ formatSignedPct(item.change_1d_pct) }}</strong>
                <span :class="changeClass(item.change_5d_pct)">五日 {{ formatSignedPct(item.change_5d_pct) }}</span>
                <span :class="changeClass(item.relative_pct)">
                  相对{{ item.reference_name }} {{ formatSignedPp(item.relative_pct) }}
                </span>
              </div>
              <div class="stock-volume">
                <span>量比 {{ item.volume_ratio?.toFixed(2) ?? '—' }}</span>
                <span>换手 {{ item.turnover_rate_pct?.toFixed(2) ?? '—' }}%</span>
              </div>
              <p class="stock-analysis">{{ item.analysis }}</p>
            </article>
          </div>
          <button
            v-if="report.stock_anomalies.length > 3"
            type="button"
            class="section-toggle"
            @click="showAllAnomalies = !showAllAnomalies"
          >
            {{ showAllAnomalies ? '收起异动列表' : `查看全部 ${report.stock_anomalies.length} 只个股异动` }}
          </button>
        </section>

        <section id="structure" class="report-section">
          <div class="section-heading">
            <h2>行业与风格</h2>
            <span>{{ report.structure_basis }}</span>
          </div>

          <div class="structure-grid">
            <div class="industry-panel">
              <div class="subsection-heading">
                <h3>{{ report.report_date === '2026-11-27' ? '主题表现' : '行业表现' }}</h3>
                <span>当日涨跌幅</span>
              </div>
              <div class="industry-columns">
                <div>
                  <p class="column-label column-up">领涨</p>
                  <div v-for="item in report.industry_leaders" :key="item.code" class="industry-row">
                    <span>{{ item.name }}</span>
                    <small>近 5 日 {{ formatSignedPct(item.five_day_change_pct) }}</small>
                    <strong :class="changeClass(item.change_pct)">{{ formatSignedPct(item.change_pct) }}</strong>
                  </div>
                </div>
                <div class="industry-divider"></div>
                <div>
                  <p class="column-label column-down">相对落后</p>
                  <div v-for="item in report.industry_laggards" :key="item.code" class="industry-row">
                    <span>{{ item.name }}</span>
                    <small>近 5 日 {{ formatSignedPct(item.five_day_change_pct) }}</small>
                    <strong :class="changeClass(item.change_pct)">{{ formatSignedPct(item.change_pct) }}</strong>
                  </div>
                </div>
              </div>
            </div>

            <div class="style-panel">
              <div class="subsection-heading">
                <h3>风格相对强弱</h3>
                <span>两端为同日收益代理</span>
              </div>
              <div class="style-list">
                <div v-for="pair in report.style_pairs" :key="pair.key" class="style-row">
                  <div class="style-labels">
                    <span>{{ pair.left_label }} {{ formatSignedPct(pair.left_return_pct) }}</span>
                    <strong :class="pair.leader === 'right' ? 'text-rise' : 'text-fall'">
                      {{ pair.leader === 'right' ? pair.right_label : pair.left_label }}领先
                      {{ pair.spread_pct.toFixed(2) }}pp
                    </strong>
                    <span>{{ pair.right_label }} {{ formatSignedPct(pair.right_return_pct) }}</span>
                  </div>
                  <div class="style-axis">
                    <span class="axis-center"></span>
                    <span
                      class="axis-fill"
                      :class="pair.leader === 'right' ? 'axis-fill-right' : 'axis-fill-left'"
                      :style="{ width: `${styleBarWidth(pair.spread_pct)}%` }"
                    ></span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="events" class="report-section">
          <div class="section-heading">
            <h2>新闻与宏观</h2>
            <span>事件事实与市场走势分开核验</span>
          </div>

          <div class="event-grid">
            <div class="news-column">
              <div class="subsection-heading">
                <h3>影响当日的重要事件</h3>
                <span>按发布时间排序</span>
              </div>
              <article v-for="news in visibleNews" :key="news.id" class="news-item">
                <div class="news-meta">
                  <span class="news-category">{{ news.category }}</span>
                  <a :href="news.url" target="_blank" rel="noopener noreferrer">{{ news.publisher }}</a>
                  <time>{{ news.published_at }}</time>
                  <span :class="['verification-badge', `verification-${news.verification}`]">
                    {{ verificationLabel(news.verification) }}
                  </span>
                </div>
                <div class="impact-line news-impact">
                  <span :class="['impact-badge', `impact-${news.impact}`]">
                    {{ impactLabel(news.impact) }} · {{ impact_strengthLabel(news.impact_strength) }}
                  </span>
                  <span class="horizon-tag">{{ impactHorizonLabel(news.impact_horizon) }}</span>
                  <span v-for="sector in news.affected_sectors" :key="sector" class="sector-tag">
                    {{ sector }}
                  </span>
                </div>
                <h4>{{ news.title }}</h4>
                <p>{{ news.excerpt }}</p>
                <div class="verification-note">{{ news.verification_note }}</div>
              </article>
              <button
                v-if="report.news_items.length > 3"
                type="button"
                class="section-toggle"
                @click="showAllNews = !showAllNews"
              >
                {{ showAllNews ? '收起新闻列表' : `查看全部 ${report.news_items.length} 条事件` }}
              </button>
            </div>

            <div class="macro-column">
              <div class="subsection-heading">
                <h3>宏观背景</h3>
                <span>{{ report.macro_items.length }} 项 · 最近发布</span>
              </div>
              <div class="macro-summary">
                <div v-for="item in report.macro_items" :key="item.key">
                  <span>{{ item.label }}</span>
                  <strong>{{ item.value }}</strong>
                  <small :class="`macro-${item.tone}`">{{ item.delta }}</small>
                </div>
              </div>
              <button
                type="button"
                class="section-toggle macro-toggle"
                @click="showMacroDetails = !showMacroDetails"
              >
                {{ showMacroDetails ? '收起宏观明细' : '查看宏观明细与影响板块' }}
              </button>
              <div v-if="showMacroDetails" class="macro-table">
                <div class="macro-table-head">
                  <span>指标</span>
                  <span>最新</span>
                  <span>变化</span>
                </div>
                <div v-for="item in report.macro_items" :key="item.key" class="macro-row">
                  <div>
                    <span>{{ item.label }}</span>
                    <small>{{ item.period }}</small>
                  </div>
                  <strong>{{ item.value }}</strong>
                  <span :class="`macro-${item.tone}`">
                    {{ item.delta }}
                    <small>前值 {{ item.previous_value }}</small>
                  </span>
                  <div class="macro-impact">
                    <span :class="['impact-badge', `impact-${item.impact}`]">
                      {{ impactLabel(item.impact) }} · {{ impact_strengthLabel(item.impact_strength) }}
                    </span>
                    <span v-for="sector in item.affected_sectors" :key="sector" class="sector-tag">
                      {{ sector }}
                    </span>
                  </div>
                </div>
              </div>
              <p v-if="showMacroDetails" class="macro-caption">
                宏观数据按月发布，用于解释中期背景，不与单日行业涨跌作机械对应。
              </p>
            </div>
          </div>
        </section>

        <section id="verification" class="report-section">
          <div class="section-heading">
            <h2>交叉核验账本</h2>
            <span>数据事实与新闻事实逐条对齐</span>
          </div>

          <div class="claim-list">
            <article
              v-for="(check, index) in report.claim_checks"
              :key="check.claim"
              class="claim-row"
              :class="`claim-${check.status}`"
            >
              <div class="claim-number">{{ String(index + 1).padStart(2, '0') }}</div>
              <div class="claim-main">
                <div class="claim-title-row">
                  <h3>{{ check.claim }}</h3>
                  <span :class="['claim-badge', `claim-badge-${check.status}`]">
                    {{ claimStatusLabel(check.status) }}
                  </span>
                </div>
                <p class="claim-assessment">{{ check.assessment }}</p>
              </div>
              <div class="claim-evidence">
                <p><span>数据</span>{{ check.data_evidence }}</p>
                <p><span>新闻</span>{{ check.news_evidence }}</p>
              </div>
            </article>
          </div>
        </section>

        <section id="interpretation" class="report-section interpretation-section">
          <div class="section-heading">
            <h2>观察清单</h2>
            <span>判断、反证与未来日程</span>
          </div>

          <div class="interpretation-layout">
            <div class="insight-list">
              <article v-for="point in report.interpretation" :key="point.title">
                <h3>{{ point.title }}</h3>
                <p>{{ point.detail }}</p>
              </article>
            </div>
            <aside class="risk-panel">
              <h3>主要风险</h3>
              <ol>
                <li v-for="risk in report.risks" :key="risk">{{ risk }}</li>
              </ol>
            </aside>
          </div>

          <div class="watch-block">
            <div class="subsection-heading">
              <h3>下一交易日验证条件</h3>
              <span>用于确认或推翻当前判断</span>
            </div>
            <div class="watch-list">
              <article v-for="item in report.watch_items" :key="item.key" class="watch-row">
                <div class="watch-heading">
                  <h4>{{ item.label }}</h4>
                  <div>
                    <span v-for="sector in item.affected_sectors" :key="sector" class="sector-tag">
                      {{ sector }}
                    </span>
                  </div>
                </div>
                <p class="watch-current">{{ item.current_state }}</p>
                <div class="watch-conditions">
                  <p><span>确认</span>{{ item.confirm_condition }}</p>
                  <p><span>反证</span>{{ item.invalidate_condition }}</p>
                </div>
              </article>
            </div>
          </div>

          <div class="calendar-block">
            <div class="subsection-heading">
              <h3>事件与观察日历</h3>
              <span>只列已知日期或明确发布窗口</span>
            </div>
            <div class="calendar-list">
              <article v-for="item in report.calendar_items" :key="`${item.date}-${item.title}`" class="calendar-row">
                <div class="calendar-date">
                  <time>{{ item.date }}</time>
                  <small>{{ item.timing }}</small>
                </div>
                <span class="calendar-category">{{ item.category }}</span>
                <div class="calendar-event">
                  <h4>
                    <a v-if="item.source_url" :href="item.source_url" target="_blank" rel="noopener noreferrer">
                      {{ item.title }}
                    </a>
                    <template v-else>{{ item.title }}</template>
                  </h4>
                  <p>{{ item.implication }}</p>
                </div>
              </article>
            </div>
          </div>
        </section>
      </article>
    </div>
    </template>
  </main>
</template>

<script setup lang="ts">
/**
 * 每日 A 股量化研究简报页面。
 *
 * 从只读 API 读取已发布版本，在不同交易日之间切换并展示完整研究简报。
 */

import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchDailyReport, fetchDailyReports } from '../api/dailyReports'
import { getErrorMessage } from '../api/client'
import type {
  DailyReportClaimStatus,
  DailyReportDetail,
  DailyReportImpact,
  DailyReportImpactHorizon,
  DailyReportSummary,
  DailyReportTimelinePoint,
  DailyReportVerificationStatus,
} from '../types/api'
import { formatCnTime, parseDateCn } from '../utils/date'

interface ChartBox {
  width: number
  height: number
  left: number
  right: number
  top: number
  bottom: number
}

interface ChartTick {
  value: number
  y: number
}

interface ChartDot {
  index: number
  x: number
  y: number
}

const route = useRoute()
const router = useRouter()
const reports = ref<DailyReportSummary[]>([])
const availableDates = computed(() => reports.value.map(item => item.report_date))
const selectedDate = ref('')
const detail = ref<DailyReportDetail | null>(null)
const report = computed(() => detail.value?.content ?? null)
const initialLoading = ref(true)
const detailLoading = ref(false)
const loadError = ref<string | null>(null)
const timelineData = computed(() => report.value?.timeline ?? [])

const sections = [
  { id: 'summary', label: '结论', hint: '单日主线' },
  { id: 'continuity', label: '盘面', hint: '连续·情绪·快照' },
  { id: 'anomaly', label: '驱动', hint: '异动·结构·事件' },
  { id: 'verification', label: '证据', hint: '核验与边界' },
  { id: 'interpretation', label: '观察', hint: '条件·风险·日程' },
]

const showAllNews = ref(false)
const showAllAnomalies = ref(false)
const showMacroDetails = ref(false)

const visibleNews = computed(() =>
  report.value
    ? showAllNews.value
      ? report.value.news_items
      : report.value.news_items.slice(0, 3)
    : [],
)

const visibleStockAnomalies = computed(() =>
  report.value
    ? showAllAnomalies.value
      ? report.value.stock_anomalies
      : report.value.stock_anomalies.slice(0, 3)
    : [],
)

const parsedReportDate = computed(() =>
  report.value ? parseDateCn(report.value.report_date) : new Date(),
)
const reportYear = computed(() => report.value?.report_date.slice(0, 4) ?? '')
const reportMonthDay = computed(() => report.value?.report_date.slice(5).replace('-', ' / ') ?? '')
const reportWeekday = computed(() =>
  new Intl.DateTimeFormat('zh-CN', { weekday: 'long' }).format(parsedReportDate.value),
)

const currentTimeline = computed<DailyReportTimelinePoint | null>(() => {
  return timelineData.value.find(point => point.trade_date === report.value?.report_date) ?? null
})

const previousTimeline = computed<DailyReportTimelinePoint | null>(() => {
  const index = timelineData.value.findIndex(point => point.trade_date === report.value?.report_date)
  return index > 0 ? timelineData.value[index - 1] : null
})

const breadthDelta = computed(() => {
  if (!currentTimeline.value || !previousTimeline.value) return 0
  return currentTimeline.value.advance_ratio_pct - previousTimeline.value.advance_ratio_pct
})

const turnoverDeltaPct = computed(() => {
  if (!currentTimeline.value || !previousTimeline.value) return 0
  return (currentTimeline.value.turnover_billion / previousTimeline.value.turnover_billion - 1) * 100
})

const csi1000Delta = computed(() => {
  if (!currentTimeline.value || !previousTimeline.value) return 0
  return currentTimeline.value.csi1000_pct - previousTimeline.value.csi1000_pct
})

const mainChartBox: ChartBox = {
  width: 620,
  height: 220,
  left: 52,
  right: 20,
  top: 20,
  bottom: 30,
}

const miniChartBox: ChartBox = {
  width: 360,
  height: 136,
  left: 42,
  right: 12,
  top: 14,
  bottom: 24,
}

const selectedTimelineIndex = computed(() =>
  timelineData.value.findIndex(point => point.trade_date === report.value?.report_date),
)

const indexDomain = computed(() => {
  const values = timelineData.value.flatMap(point => [point.csi300_pct, point.csi1000_pct])
  if (values.length === 0) return { min: -2, max: 2 }
  return {
    min: Math.floor((Math.min(...values) - 1) / 2) * 2,
    max: Math.ceil((Math.max(...values) + 1) / 2) * 2,
  }
})

const indexTicks = computed(() => buildChartTicks(indexDomain.value.min, indexDomain.value.max, mainChartBox))
const breadthTicks = computed(() => buildChartTicks(0, 100, miniChartBox))
const turnoverTicks = computed(() => buildChartTicks(0, 20000, miniChartBox))

const csi300Points = computed(() =>
  buildLinePoints(
    timelineData.value.map(point => point.csi300_pct),
    indexDomain.value.min,
    indexDomain.value.max,
    mainChartBox,
  ),
)

const csi1000Points = computed(() =>
  buildLinePoints(
    timelineData.value.map(point => point.csi1000_pct),
    indexDomain.value.min,
    indexDomain.value.max,
    mainChartBox,
  ),
)

const csi300Dots = computed(() =>
  buildChartDots(
    timelineData.value.map(point => point.csi300_pct),
    indexDomain.value.min,
    indexDomain.value.max,
    mainChartBox,
  ),
)

const csi1000Dots = computed(() =>
  buildChartDots(
    timelineData.value.map(point => point.csi1000_pct),
    indexDomain.value.min,
    indexDomain.value.max,
    mainChartBox,
  ),
)

const breadthPoints = computed(() =>
  buildLinePoints(
    timelineData.value.map(point => point.advance_ratio_pct),
    0,
    100,
    miniChartBox,
  ),
)

const breadthArea = computed(() =>
  buildAreaPoints(
    timelineData.value.map(point => point.advance_ratio_pct),
    0,
    100,
    miniChartBox,
  ),
)

const breadthDots = computed(() =>
  buildChartDots(
    timelineData.value.map(point => point.advance_ratio_pct),
    0,
    100,
    miniChartBox,
  ),
)

const turnoverPoints = computed(() =>
  buildLinePoints(
    timelineData.value.map(point => point.turnover_billion),
    0,
    20000,
    miniChartBox,
  ),
)

const turnoverArea = computed(() =>
  buildAreaPoints(
    timelineData.value.map(point => point.turnover_billion),
    0,
    20000,
    miniChartBox,
  ),
)

const turnoverDots = computed(() =>
  buildChartDots(
    timelineData.value.map(point => point.turnover_billion),
    0,
    20000,
    miniChartBox,
  ),
)

const timelineXLabels = computed(() =>
  timelineData.value.map((point, index) => ({
    trade_date: point.trade_date,
    label: point.label,
    mainX: chartX(index, timelineData.value.length, mainChartBox),
    miniX: chartX(index, timelineData.value.length, miniChartBox),
  })),
)

const selectedIndexX = computed(() =>
  chartX(selectedTimelineIndex.value, timelineData.value.length, mainChartBox),
)

const selectedMiniX = computed(() =>
  chartX(selectedTimelineIndex.value, timelineData.value.length, miniChartBox),
)

const indexZeroY = computed(() =>
  chartY(0, indexDomain.value.min, indexDomain.value.max, mainChartBox),
)

const continuityReading = computed(() => {
  if (!currentTimeline.value || !previousTimeline.value) return ''
  return `${currentTimeline.value.label} 相较 ${previousTimeline.value.label}：上涨占比由 ${previousTimeline.value.advance_ratio_pct.toFixed(2)}% 变为 ${currentTimeline.value.advance_ratio_pct.toFixed(2)}%，成交额 ${formatSignedPct(turnoverDeltaPct.value)}，中证1000涨跌由 ${formatSignedPct(previousTimeline.value.csi1000_pct)} 变为 ${formatSignedPct(currentTimeline.value.csi1000_pct)}。`
})

const breadthWidths = computed(() => {
  if (!report.value) return { up: 0, flat: 0, down: 0 }
  const { total, advances, unchanged, declines } = report.value.market_snapshot.breadth
  return {
    up: (advances / total) * 100,
    flat: (unchanged / total) * 100,
    down: (declines / total) * 100,
  }
})

/**
 * 计算折线图横坐标。
 *
 * @param index - 数据点序号
 * @param count - 数据点总数
 * @param box - 图表绘图区
 * @returns SVG 横坐标
 */
function chartX(index: number, count: number, box: ChartBox): number {
  const width = box.width - box.left - box.right
  return box.left + (index / Math.max(count - 1, 1)) * width
}

/**
 * 计算折线图纵坐标。
 *
 * @param value - 指标值
 * @param min - 坐标轴最小值
 * @param max - 坐标轴最大值
 * @param box - 图表绘图区
 * @returns SVG 纵坐标
 */
function chartY(value: number, min: number, max: number, box: ChartBox): number {
  const height = box.height - box.top - box.bottom
  return box.top + ((max - value) / (max - min)) * height
}

/**
 * 生成折线 points 属性。
 *
 * @param values - 指标序列
 * @param min - 坐标轴最小值
 * @param max - 坐标轴最大值
 * @param box - 图表绘图区
 * @returns SVG points 字符串
 */
function buildLinePoints(values: number[], min: number, max: number, box: ChartBox): string {
  return values
    .map((value, index) => {
      const x = chartX(index, values.length, box)
      const y = chartY(value, min, max, box)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

/**
 * 生成折线下方的填充区域。
 *
 * @param values - 指标序列
 * @param min - 坐标轴最小值
 * @param max - 坐标轴最大值
 * @param box - 图表绘图区
 * @returns SVG polygon points 字符串
 */
function buildAreaPoints(values: number[], min: number, max: number, box: ChartBox): string {
  if (values.length === 0) return ''
  const baseline = box.height - box.bottom
  const firstX = chartX(0, values.length, box)
  const lastX = chartX(values.length - 1, values.length, box)
  return `${firstX.toFixed(1)},${baseline} ${buildLinePoints(values, min, max, box)} ${lastX.toFixed(1)},${baseline}`
}

/**
 * 生成折线图节点坐标。
 *
 * @param values - 指标序列
 * @param min - 坐标轴最小值
 * @param max - 坐标轴最大值
 * @param box - 图表绘图区
 * @returns 节点坐标数组
 */
function buildChartDots(values: number[], min: number, max: number, box: ChartBox): ChartDot[] {
  return values.map((value, index) => ({
    index,
    x: chartX(index, values.length, box),
    y: chartY(value, min, max, box),
  }))
}

/**
 * 生成图表纵向刻度和坐标。
 *
 * @param min - 坐标轴最小值
 * @param max - 坐标轴最大值
 * @param box - 图表绘图区
 * @param count - 刻度数量
 * @returns 刻度数组
 */
function buildChartTicks(
  min: number,
  max: number,
  box: ChartBox,
  count = 5,
): ChartTick[] {
  return Array.from({ length: count }, (_, index) => {
    const ratio = index / Math.max(count - 1, 1)
    const value = max - (max - min) * ratio
    return { value, y: chartY(value, min, max, box) }
  })
}

/**
 * 格式化折线图收益率刻度。
 *
 * @param value - 百分比数值
 * @returns 展示文本
 */
function formatChartPct(value: number): string {
  const rounded = Math.abs(value) < 0.05 ? 0 : value
  const sign = rounded > 0 ? '+' : ''
  return `${sign}${rounded.toFixed(0)}%`
}

/**
 * 格式化折线图成交额刻度。
 *
 * @param value - 亿元金额
 * @returns 展示文本
 */
function formatChartAmount(value: number): string {
  return value >= 10000 ? `${(value / 10000).toFixed(1)}万亿` : `${Math.round(value)}亿`
}

/**
 * 加载指定交易日日报并同步 URL 查询参数。
 *
 * @param date - YYYY-MM-DD 业务日期
 * @param syncQuery - 是否更新浏览器 URL
 */
async function loadReport(date: string, syncQuery = true): Promise<void> {
  detailLoading.value = true
  loadError.value = null
  try {
    detail.value = await fetchDailyReport(date)
    selectedDate.value = date
    showAllNews.value = false
    showAllAnomalies.value = false
    showMacroDetails.value = false
    if (syncQuery) {
      await router.replace({ query: { ...route.query, date } })
    }
  } catch (error) {
    detail.value = null
    loadError.value = getErrorMessage(error, '日报加载失败，请稍后重试')
  } finally {
    detailLoading.value = false
  }
}

/**
 * 加载已发布日期列表，并选择 URL 指定日期或最新日期。
 */
async function loadInitial(): Promise<void> {
  initialLoading.value = true
  loadError.value = null
  try {
    const response = await fetchDailyReports()
    reports.value = response.items
    if (response.items.length === 0) {
      detail.value = null
      return
    }
    const queryDate = typeof route.query.date === 'string' ? route.query.date : ''
    const initialDate = response.items.some(item => item.report_date === queryDate)
      ? queryDate
      : response.items[0].report_date
    await loadReport(initialDate, false)
  } catch (error) {
    reports.value = []
    detail.value = null
    loadError.value = getErrorMessage(error, '日报列表加载失败，请稍后重试')
  } finally {
    initialLoading.value = false
  }
}

/**
 * 切换日报日期。
 *
 * @param date - YYYY-MM-DD 业务日期
 */
function selectDate(date: string): void {
  void loadReport(date)
}

/**
 * 格式化日期切换按钮的主文案。
 *
 * @param value - YYYY-MM-DD 业务日期
 * @returns 月日文案
 */
function formatDateButton(value: string): string {
  const [, month, day] = value.split('-')
  return `${Number(month)} 月 ${Number(day)} 日`
}

/**
 * 格式化日期对应的星期。
 *
 * @param value - YYYY-MM-DD 业务日期
 * @returns 星期文案
 */
function formatWeekday(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', { weekday: 'long' }).format(parseDateCn(value))
}

/**
 * 将涨跌幅格式化为带正负号的百分比。
 *
 * @param value - 百分比数值
 * @returns 展示文本
 */
function formatSignedPct(value: number): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

/**
 * 将百分点差格式化为带正负号的文本。
 *
 * @param value - 百分点数值
 * @returns 展示文本
 */
function formatSignedPp(value: number): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}pp`
}

/**
 * 将数字格式化为带正负号且保留指定小数位。
 *
 * @param value - 数字
 * @param digits - 小数位
 * @returns 展示文本
 */
function formatSignedNumber(value: number, digits: number): string {
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(digits)}`
}

/**
 * 按指定小数位格式化普通数字。
 *
 * @param value - 数字
 * @param digits - 小数位
 * @returns 展示文本
 */
function formatNumber(value: number, digits: number): string {
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/**
 * 将亿元金额格式化为万亿或亿元。
 *
 * @param value - 亿元金额
 * @returns 展示文本
 */
function formatCompactAmount(value: number): string {
  return value >= 10000 ? `${(value / 10000).toFixed(2)} 万亿元` : `${value.toFixed(0)} 亿元`
}

/**
 * 根据数值返回涨跌语义颜色类名。
 *
 * @param value - 数值
 * @returns CSS 类名
 */
function changeClass(value: number): string {
  if (value > 0) return 'text-rise'
  if (value < 0) return 'text-fall'
  return 'text-flat'
}

/**
 * 生成微型趋势线坐标。
 *
 * @param values - 走势数值
 * @returns SVG polyline points
 */
function sparkPoints(values: number[]): string {
  if (values.length === 0) return ''
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  return values
    .map((value, index) => {
      const x = (index / Math.max(values.length - 1, 1)) * 100
      const y = 24 - ((value - min) / range) * 20
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

/**
 * 将风格收益差映射到半轴宽度。
 *
 * @param spread_pct - 两端收益差
 * @returns 半轴百分比宽度
 */
function styleBarWidth(spread_pct: number): number {
  return Math.min((spread_pct / 4) * 50, 50)
}

/**
 * 映射新闻交叉核验状态文案。
 *
 * @param status - 核验状态
 * @returns 中文文案
 */
function verificationLabel(status: DailyReportVerificationStatus): string {
  const labels: Record<DailyReportVerificationStatus, string> = {
    verified: '已核验',
    partial: '部分核验',
    unverified: '未核验',
    contradicted: '相矛盾',
  }
  return labels[status]
}

/**
 * 映射结论证据状态文案。
 *
 * @param status - 结论支持状态
 * @returns 中文文案
 */
function claimStatusLabel(status: DailyReportClaimStatus): string {
  const labels: Record<DailyReportClaimStatus, string> = {
    supported: '证据支持',
    partial: '部分支持',
    unsupported: '证据不足',
    conflicted: '存在冲突',
  }
  return labels[status]
}

/**
 * 映射利好利空方向文案。
 *
 * @param impact - 影响方向
 * @returns 中文文案
 */
function impactLabel(impact: DailyReportImpact): string {
  const labels: Record<DailyReportImpact, string> = {
    bullish: '利好',
    bearish: '利空',
    mixed: '多空交织',
    neutral: '中性',
  }
  return labels[impact]
}

/**
 * 映射影响强度文案。
 *
 * @param strength - 1 至 3 的强度等级
 * @returns 中文文案
 */
function impact_strengthLabel(strength: 1 | 2 | 3): string {
  return { 1: '轻度', 2: '中等', 3: '强' }[strength]
}

/**
 * 映射消息影响时间窗文案。
 *
 * @param horizon - 影响时间范围
 * @returns 中文文案
 */
function impactHorizonLabel(horizon: DailyReportImpactHorizon): string {
  const labels: Record<DailyReportImpactHorizon, string> = {
    immediate: '即时',
    short: '1–3 个交易日',
    medium: '中期',
  }
  return labels[horizon]
}

onMounted(() => {
  void loadInitial()
})
</script>

<style scoped>
.daily-report-page {
  --report-bg: #0b1220;
  --report-panel: #121b2b;
  --report-line: #2a3546;
  --report-line-soft: #202a3a;
  --report-text: #ece8de;
  --report-muted: #9ca8b8;
  --report-gold: #d5a64a;
  --report-display: "Source Han Serif SC", "Noto Serif CJK SC", "Songti SC", "STSong", Georgia, serif;
  max-width: 1440px;
  margin: 0 auto;
  color: var(--report-text);
  font-family: "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei", system-ui, sans-serif;
}

.page-state {
  display: flex;
  min-height: 420px;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 12px;
  border: 1px solid var(--report-line);
  background: var(--report-panel);
  color: var(--report-muted);
  font-size: 12px;
}

.page-state strong {
  color: var(--report-text);
  font-family: var(--report-display);
  font-size: 20px;
}

.page-state-error {
  border-color: rgba(242, 113, 113, 0.35);
}

.loading-spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--report-line);
  border-top-color: var(--report-gold);
  border-radius: 50%;
  animation: report-spin 0.8s linear infinite;
}

@keyframes report-spin {
  to {
    transform: rotate(360deg);
  }
}

.snapshot-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  min-height: 36px;
  padding: 8px 12px;
  border: 1px solid rgba(59, 130, 246, 0.28);
  background: rgba(59, 130, 246, 0.06);
  color: var(--report-muted);
  font-size: 11px;
}

.snapshot-mark {
  flex: 0 0 auto;
  padding: 2px 7px;
  border: 1px solid var(--accent);
  color: #93c5fd;
  font-weight: 700;
}

.snapshot-time {
  margin-left: auto;
  white-space: nowrap;
}

.date-toolbar {
  display: flex;
  align-items: stretch;
  justify-content: space-between;
  gap: 24px;
  margin-top: 18px;
}

.date-tabs {
  display: flex;
  gap: 6px;
}

.date-tabs button {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 126px;
  padding: 9px 12px;
  border: 1px solid var(--report-line);
  background: transparent;
  color: var(--report-muted);
  text-align: left;
}

.date-tabs button:hover,
.date-tabs button:focus-visible {
  border-color: var(--report-gold);
  color: var(--report-text);
  outline: none;
}

.date-tabs button.active {
  border-color: var(--report-gold);
  background: rgba(213, 166, 74, 0.08);
  color: var(--report-gold);
}

.date-tabs button span {
  font-size: 13px;
  font-weight: 700;
}

.date-tabs button small {
  font-size: 9px;
}

.next-trade {
  display: flex;
  align-items: baseline;
  gap: 10px;
  color: var(--report-muted);
  font-size: 10px;
}

.next-trade strong {
  color: var(--report-text);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.report-masthead {
  padding: 26px 0 30px;
  border-bottom: 1px solid var(--report-line);
}

.masthead-line {
  display: flex;
  align-items: center;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--report-line-soft);
  color: var(--report-muted);
  font-size: 12px;
}

.masthead-line > :first-child {
  color: var(--report-text);
  font-weight: 700;
  letter-spacing: 0.08em;
}

.market-domain {
  padding-left: 16px;
  border-left: 1px solid var(--report-line);
}

.issue-status {
  margin-left: auto;
  color: var(--report-gold);
}

.issue-status::before {
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: 7px;
  border-radius: 50%;
  background: currentColor;
  content: "";
  vertical-align: 1px;
}

.masthead-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 48px;
  padding-top: 32px;
}

.date-lockup {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 18px;
  color: var(--report-muted);
}

.date-year {
  font-size: 13px;
  letter-spacing: 0.16em;
}

.date-main {
  color: var(--report-gold);
  font-family: var(--report-display);
  font-size: 34px;
  line-height: 1;
}

.date-weekday {
  font-size: 13px;
}

.headline-block h1 {
  max-width: 760px;
  font-family: var(--report-display);
  font-size: clamp(34px, 4.2vw, 58px);
  font-weight: 700;
  line-height: 1.16;
  letter-spacing: -0.035em;
}

.report-subtitle {
  max-width: 720px;
  margin-top: 18px;
  color: #c1c7d0;
  font-family: var(--report-display);
  font-size: 18px;
  line-height: 1.75;
}

.edition-meta {
  align-self: end;
  border-top: 1px solid var(--report-line);
}

.edition-meta > div {
  display: grid;
  grid-template-columns: 86px 1fr;
  gap: 12px;
  padding: 12px 0;
  border-bottom: 1px solid var(--report-line-soft);
}

.edition-meta dt {
  color: var(--report-muted);
  font-size: 11px;
}

.edition-meta dd {
  margin: 0;
  text-align: right;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.meta-muted {
  color: var(--report-muted);
}

.data-alert {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr);
  gap: 14px;
  padding: 12px 14px;
  border-left: 2px solid var(--warning);
  background: rgba(245, 158, 11, 0.06);
  color: #cbb68d;
  font-size: 10px;
  line-height: 1.65;
}

.alert-mark {
  color: var(--warning);
  font-weight: 700;
}

.data-alert p + p {
  margin-top: 4px;
}

.report-layout {
  display: grid;
  grid-template-columns: 148px minmax(0, 1fr);
  gap: 44px;
}

.section-rail {
  position: relative;
  border-right: 1px solid var(--report-line-soft);
}

.rail-sticky {
  position: sticky;
  top: 24px;
  padding: 28px 26px 40px 0;
}

.rail-title {
  margin-bottom: 14px;
  color: var(--report-muted);
  font-size: 11px;
}

.section-rail a {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 0 8px 13px;
  border-left: 1px solid var(--report-line);
  color: var(--report-muted);
  transition: color 0.15s, border-color 0.15s;
}

.section-rail a:hover,
.section-rail a:focus-visible {
  border-left-color: var(--report-gold);
  color: var(--report-text);
  outline: none;
}

.section-rail a span {
  font-size: 13px;
  font-weight: 600;
}

.section-rail a small {
  font-size: 10px;
}

.rail-evidence {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-top: 24px;
  padding-top: 14px;
  border-top: 1px solid var(--report-line-soft);
  color: var(--report-muted);
  font-size: 11px;
}

.rail-evidence strong {
  color: var(--report-gold);
  font-family: var(--report-display);
  font-size: 24px;
}

.report-body {
  min-width: 0;
  padding-bottom: 48px;
}

.report-section {
  padding: 42px 0 46px;
  border-bottom: 1px solid var(--report-line);
  scroll-margin-top: 24px;
}

.interpretation-section {
  border-bottom: 0;
}

.section-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 24px;
}

.section-heading h2 {
  font-family: var(--report-display);
  font-size: 27px;
  line-height: 1.2;
}

.section-heading > span {
  color: var(--report-muted);
  font-size: 11px;
  text-align: right;
}

.summary-section {
  padding-top: 34px;
}

.thesis-block {
  display: grid;
  grid-template-columns: 54px minmax(0, 1fr);
  gap: 24px;
  padding: 22px 0 28px;
  border-top: 1px solid var(--report-gold);
  border-bottom: 1px solid var(--report-line);
}

.thesis-index {
  padding-top: 3px;
  color: var(--report-gold);
  font-size: 11px;
  font-weight: 700;
}

.thesis-block p {
  max-width: 880px;
  font-family: var(--report-display);
  font-size: 20px;
  line-height: 1.85;
}

.day-delta {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 24px;
  padding-top: 20px;
}

.delta-heading {
  display: flex;
  flex-direction: column;
  gap: 5px;
  color: var(--report-text);
  font-size: 11px;
}

.delta-heading small {
  color: var(--report-muted);
  font-size: 9px;
}

.delta-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 18px;
}

.delta-grid > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 3px 10px;
  padding-left: 14px;
  border-left: 1px solid var(--report-line);
}

.delta-grid span {
  grid-column: 1 / -1;
  color: var(--report-muted);
  font-size: 9px;
}

.delta-grid strong {
  font-size: 16px;
  font-variant-numeric: tabular-nums;
}

.delta-grid small {
  align-self: end;
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.continuity-charts {
  display: grid;
  grid-template-columns: minmax(0, 1.3fr) minmax(280px, 0.7fr);
  gap: 32px;
}

.line-chart {
  min-width: 0;
  margin: 0;
}

.chart-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 11px 0 9px;
  border-top: 1px solid var(--report-line);
}

.chart-header h3 {
  font-size: 13px;
}

.chart-header > div > span {
  display: block;
  margin-top: 2px;
  color: var(--report-muted);
  font-size: 9px;
}

.chart-legend {
  display: flex;
  gap: 14px;
  padding-top: 2px;
  color: var(--report-muted);
  font-size: 9px;
}

.chart-legend span::before {
  display: inline-block;
  width: 12px;
  height: 2px;
  margin-right: 6px;
  background: currentColor;
  content: "";
  vertical-align: 3px;
}

.legend-csi300 {
  color: #e7e4db;
}

.legend-csi1000 {
  color: #64a9df;
}

.line-chart svg {
  display: block;
  width: 100%;
  height: auto;
  overflow: visible;
}

.side-line-charts {
  display: grid;
  gap: 18px;
  padding-left: 28px;
  border-left: 1px solid var(--report-line-soft);
}

.chart-grid line {
  stroke: var(--report-line-soft);
  stroke-width: 1;
}

.chart-zero {
  stroke: #5d6879;
  stroke-width: 1;
  stroke-dasharray: 3 4;
}

.chart-current-line {
  stroke: var(--report-gold);
  stroke-width: 1;
  stroke-dasharray: 3 4;
}

.chart-axis-label {
  fill: var(--report-muted);
  font-family: "Source Han Sans SC", "Noto Sans CJK SC", "Microsoft YaHei", system-ui, sans-serif;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.chart-x-label {
  fill: #8794a6;
  font-size: 10px;
}

.chart-current-label {
  fill: var(--report-gold);
  font-weight: 700;
}

.series-csi300,
.series-csi1000,
.series-breadth,
.series-turnover {
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2;
}

.series-csi300 {
  stroke: #e7e4db;
}

.series-csi1000 {
  stroke: #64a9df;
}

.series-breadth {
  stroke: var(--report-gold);
}

.series-turnover {
  stroke: #7b9ed8;
}

.series-area {
  stroke: none;
}

.series-breadth-area {
  fill: rgba(213, 166, 74, 0.08);
}

.series-turnover-area {
  fill: rgba(100, 169, 223, 0.08);
}

.chart-dot {
  stroke: var(--report-bg);
  stroke-width: 1.5;
}

.chart-dot.current {
  stroke: var(--report-text);
  stroke-width: 2;
}

.series-csi300-dot {
  fill: #e7e4db;
}

.series-csi1000-dot {
  fill: #64a9df;
}

.series-breadth-dot {
  fill: var(--report-gold);
}

.series-turnover-dot {
  fill: #7b9ed8;
}

.timeline-reading {
  max-width: 850px;
  margin-top: 18px;
  color: #b5becc;
  font-family: var(--report-display);
  font-size: 14px;
  line-height: 1.75;
}

.index-tape {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border-top: 1px solid var(--report-line);
  border-bottom: 1px solid var(--report-line);
}

.index-cell {
  min-width: 0;
  padding: 18px 18px 16px 0;
}

.index-cell + .index-cell {
  padding-left: 18px;
  border-left: 1px solid var(--report-line-soft);
}

.index-cell-top,
.index-cell-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.index-cell-top {
  color: var(--report-muted);
  font-size: 11px;
}

.index-cell-top small {
  font-variant-numeric: tabular-nums;
}

.index-cell > strong {
  display: block;
  margin: 10px 0 8px;
  font-family: var(--report-display);
  font-size: 25px;
  font-variant-numeric: tabular-nums;
}

.index-cell-bottom > span {
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.index-cell svg {
  width: 76px;
  height: 26px;
  overflow: visible;
}

.market-metrics {
  display: grid;
  grid-template-columns: 0.85fr 1.2fr 1fr;
  gap: 34px;
  padding-top: 24px;
}

.metric-label {
  display: block;
  color: var(--report-muted);
  font-size: 11px;
}

.turnover-metric > strong {
  display: block;
  margin: 7px 0 3px;
  font-family: var(--report-display);
  font-size: 28px;
}

.metric-change {
  font-size: 11px;
}

.breadth-title {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.breadth-title strong {
  font-family: var(--report-display);
  font-size: 25px;
  font-variant-numeric: tabular-nums;
}

.breadth-bar {
  display: flex;
  height: 8px;
  overflow: hidden;
  background: var(--report-line-soft);
}

.breadth-up {
  background: var(--rise);
}

.breadth-flat {
  background: #6f7a8c;
}

.breadth-down {
  background: var(--fall);
}

.breadth-counts {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  margin-top: 8px;
  color: var(--report-muted);
  font-size: 10px;
}

.secondary-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.secondary-metrics > div {
  padding-left: 14px;
  border-left: 1px solid var(--report-line);
}

.secondary-metrics span {
  display: block;
  margin-bottom: 7px;
  color: var(--report-muted);
  font-size: 10px;
}

.secondary-metrics strong {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.signal-strip {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin-top: 28px;
  border-top: 1px solid var(--report-line);
  border-bottom: 1px solid var(--report-line);
}

.signal-item {
  min-width: 0;
  padding: 16px 18px 16px 0;
}

.signal-item + .signal-item {
  padding-left: 18px;
  border-left: 1px solid var(--report-line-soft);
}

.signal-item > span {
  display: block;
  color: var(--report-muted);
  font-size: 9px;
}

.signal-item > strong {
  display: block;
  margin-top: 5px;
  font-size: 15px;
  font-variant-numeric: tabular-nums;
}

.signal-item > small {
  display: block;
  margin-top: 2px;
  color: var(--report-muted);
  font-size: 9px;
}

.signal-item > p {
  margin-top: 8px;
  color: #aeb7c5;
  font-size: 9px;
  line-height: 1.55;
}

.signal-positive {
  color: var(--rise);
}

.signal-negative {
  color: var(--fall);
}

.signal-neutral {
  color: var(--report-text);
}

.impact-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  margin-top: 9px;
}

.impact-badge,
.sector-tag {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  padding: 1px 5px;
  border: 1px solid var(--report-line);
  font-size: 8px;
  font-weight: 500;
  line-height: 1.2;
}

.impact-bullish {
  border-color: rgba(53, 197, 139, 0.42);
  background: rgba(53, 197, 139, 0.06);
  color: var(--rise);
}

.impact-bearish {
  border-color: rgba(242, 113, 113, 0.42);
  background: rgba(242, 113, 113, 0.06);
  color: var(--fall);
}

.impact-mixed {
  border-color: rgba(213, 166, 74, 0.5);
  background: rgba(213, 166, 74, 0.06);
  color: var(--report-gold);
}

.impact-neutral,
.sector-tag {
  color: var(--report-muted);
}

.horizon-tag {
  display: inline-flex;
  min-height: 18px;
  align-items: center;
  padding: 1px 5px;
  border: 1px dashed var(--report-line);
  color: #aebed3;
  font-size: 8px;
}

.section-toggle {
  margin-top: 14px;
  padding: 6px 0;
  border: 0;
  border-bottom: 1px solid var(--report-line);
  background: transparent;
  color: var(--report-gold);
  font-size: 9px;
  text-align: left;
}

.section-toggle:hover,
.section-toggle:focus-visible {
  border-bottom-color: var(--report-gold);
  outline: none;
}

.sentiment-overview {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 40px;
  align-items: stretch;
}

.sentiment-reading {
  padding: 20px 0 18px;
  border-top: 1px solid var(--report-line);
}

.sentiment-score-line {
  display: flex;
  align-items: baseline;
  gap: 14px;
}

.sentiment-score-line > span {
  color: var(--report-muted);
  font-size: 11px;
}

.sentiment-score-line > strong {
  font-family: var(--report-display);
  font-size: 48px;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.sentiment-score-line > em {
  padding: 3px 8px;
  border: 1px solid currentColor;
  font-size: 11px;
  font-style: normal;
  font-weight: 700;
}

.sentiment-state-freezing {
  color: #78a6cf;
}

.sentiment-state-cold {
  color: #9fb8cf;
}

.sentiment-state-neutral {
  color: var(--report-muted);
}

.sentiment-state-hot {
  color: var(--report-gold);
}

.sentiment-state-boiling {
  color: #ff766b;
}

.sentiment-reading > p {
  max-width: 760px;
  margin-top: 16px;
  color: #b7c0cd;
  font-family: var(--report-display);
  font-size: 15px;
  line-height: 1.8;
}

.sentiment-history {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 9px;
  align-items: end;
  min-height: 150px;
  padding: 16px 0 0;
  border-top: 1px solid var(--report-line);
}

.history-item {
  display: grid;
  grid-template-rows: 18px 88px 19px;
  gap: 5px;
  justify-items: center;
  color: var(--report-muted);
  font-size: 8px;
}

.history-column {
  position: relative;
  align-self: stretch;
  width: 14px;
  background: var(--report-line-soft);
}

.history-column i {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  background: linear-gradient(to top, #5f7fa8, var(--report-gold) 62%, #f26b5b);
}

.history-item strong {
  color: var(--report-text);
  font-size: 8px;
  font-variant-numeric: tabular-nums;
}

.thermometer {
  margin-top: 22px;
}

.thermal-track {
  position: relative;
  height: 9px;
  background: linear-gradient(
    to right,
    #5f7fa8 0%,
    #8297ad 20%,
    #68768a 45%,
    #b99858 65%,
    #d5a64a 85%,
    #f26b5b 100%
  );
}

.thermal-marker {
  position: absolute;
  top: -5px;
  width: 3px;
  height: 19px;
  background: var(--report-text);
  box-shadow: 0 0 0 2px var(--report-bg);
  transform: translateX(-1px);
}

.thermal-labels {
  display: grid;
  grid-template-columns: 20fr 25fr 20fr 20fr 15fr;
  margin-top: 8px;
  color: var(--report-muted);
  font-size: 8px;
}

.thermal-labels span:nth-child(2),
.thermal-labels span:nth-child(3),
.thermal-labels span:nth-child(4) {
  text-align: center;
}

.thermal-labels span:last-child {
  text-align: right;
}

.sentiment-components {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 18px;
  margin-top: 24px;
  padding-top: 18px;
  border-top: 1px solid var(--report-line);
}

.sentiment-component {
  min-width: 0;
}

.component-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.component-heading span {
  color: var(--report-muted);
  font-size: 9px;
}

.component-heading strong {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.component-track {
  height: 5px;
  margin-top: 8px;
  background: var(--report-line-soft);
}

.component-track span {
  display: block;
  height: 100%;
  background: linear-gradient(to right, #6d88a6, var(--report-gold), #f26b5b);
}

.component-meta {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 6px;
  color: var(--report-muted);
  font-size: 8px;
}

.sentiment-component p {
  margin-top: 7px;
  color: #aeb7c5;
  font-size: 9px;
  line-height: 1.55;
}

.index-anomalies {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border-top: 1px solid var(--report-line);
  border-bottom: 1px solid var(--report-line);
}

.index-anomaly {
  min-width: 0;
  padding: 16px 20px 18px 0;
}

.index-anomaly + .index-anomaly {
  padding-left: 20px;
  border-left: 1px solid var(--report-line-soft);
}

.anomaly-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.anomaly-title-row > div span,
.anomaly-title-row > div small {
  display: block;
}

.anomaly-title-row > div span {
  font-family: var(--report-display);
  font-size: 17px;
  font-weight: 700;
}

.anomaly-title-row > div small {
  margin-top: 2px;
  color: var(--report-muted);
  font-size: 8px;
}

.anomaly-title-row em,
.stock-identity em {
  padding: 2px 6px;
  border: 1px solid rgba(213, 166, 74, 0.42);
  color: var(--report-gold);
  font-size: 8px;
  font-style: normal;
  white-space: nowrap;
}

.anomaly-returns {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-top: 14px;
}

.anomaly-returns > div {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  padding-bottom: 7px;
  border-bottom: 1px solid var(--report-line-soft);
}

.anomaly-returns span {
  color: var(--report-muted);
  font-size: 8px;
}

.anomaly-returns strong {
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.anomaly-reference {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 8px;
  color: var(--report-muted);
  font-size: 8px;
}

.anomaly-reference strong {
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.index-anomaly > p {
  margin-top: 12px;
  color: #aeb7c5;
  font-size: 10px;
  line-height: 1.65;
}

.stock-anomaly-head,
.stock-anomaly-row {
  display: grid;
  grid-template-columns: 130px 170px 92px 100px minmax(0, 1fr);
  gap: 18px;
}

.stock-anomaly-head {
  margin-top: 28px;
  padding: 9px 0;
  border-top: 1px solid var(--report-line);
  border-bottom: 1px solid var(--report-line);
  color: var(--report-muted);
  font-size: 9px;
}

.stock-anomaly-row {
  align-items: start;
  padding: 15px 0;
  border-bottom: 1px solid var(--report-line-soft);
}

.stock-identity {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 5px;
}

.stock-identity strong {
  width: 100%;
  font-family: var(--report-display);
  font-size: 16px;
}

.stock-identity small {
  color: var(--report-muted);
  font-size: 8px;
  font-variant-numeric: tabular-nums;
}

.stock-identity em {
  margin-left: auto;
}

.stock-taxonomy {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-content: flex-start;
}

.taxonomy-industry,
.taxonomy-theme,
.taxonomy-empty {
  display: inline-flex;
  min-height: 18px;
  align-items: center;
  padding: 1px 5px;
  border: 1px solid var(--report-line);
  font-size: 8px;
}

.taxonomy-industry {
  border-color: rgba(213, 166, 74, 0.35);
  color: var(--report-gold);
}

.taxonomy-theme {
  color: #aebed3;
}

.taxonomy-empty {
  color: var(--report-muted);
}

.stock-returns,
.stock-volume {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.stock-returns strong {
  font-size: 15px;
}

.stock-returns span,
.stock-volume span {
  color: var(--report-muted);
}

.stock-analysis {
  color: #b1bac8;
  font-size: 10px;
  line-height: 1.65;
}

.structure-grid {
  display: grid;
  grid-template-columns: 1.15fr 0.85fr;
  gap: 38px;
}

.industry-panel,
.style-panel {
  min-width: 0;
}

.style-panel {
  padding-left: 36px;
  border-left: 1px solid var(--report-line);
}

.subsection-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--report-line);
}

.subsection-heading h3 {
  font-size: 14px;
}

.subsection-heading span {
  color: var(--report-muted);
  font-size: 10px;
}

.industry-columns {
  display: grid;
  grid-template-columns: 1fr 1px 1fr;
  gap: 18px;
  padding-top: 14px;
}

.industry-divider {
  background: var(--report-line-soft);
}

.column-label {
  margin-bottom: 6px;
  font-size: 10px;
}

.column-up {
  color: var(--rise);
}

.column-down {
  color: var(--fall);
}

.industry-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto 64px;
  gap: 8px;
  align-items: baseline;
  min-height: 36px;
  padding: 7px 0;
  border-bottom: 1px solid var(--report-line-soft);
  font-size: 12px;
}

.industry-row small {
  color: var(--report-muted);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.industry-row strong {
  text-align: right;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.style-list {
  padding-top: 15px;
}

.style-row + .style-row {
  margin-top: 22px;
}

.style-labels {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
  font-size: 9px;
}

.style-labels > :last-child {
  text-align: right;
}

.style-labels strong {
  font-size: 9px;
  font-variant-numeric: tabular-nums;
}

.style-axis {
  position: relative;
  height: 5px;
  background: var(--report-line-soft);
}

.axis-center {
  position: absolute;
  top: -2px;
  bottom: -2px;
  left: 50%;
  width: 1px;
  background: var(--report-muted);
}

.axis-fill {
  position: absolute;
  top: 0;
  bottom: 0;
  background: var(--report-gold);
}

.axis-fill-right {
  left: 50%;
}

.axis-fill-left {
  right: 50%;
}

.event-grid {
  display: grid;
  grid-template-columns: 1.3fr 0.7fr;
  gap: 40px;
}

.news-column,
.macro-column {
  min-width: 0;
}

.macro-column {
  padding-left: 36px;
  border-left: 1px solid var(--report-line);
}

.news-item {
  padding: 16px 0;
  border-bottom: 1px solid var(--report-line-soft);
}

.news-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--report-muted);
  font-size: 9px;
}

.news-meta a {
  color: var(--report-muted);
  text-decoration: underline;
  text-decoration-color: var(--report-line);
  text-underline-offset: 3px;
}

.news-meta a:hover,
.news-meta a:focus-visible {
  color: var(--report-gold);
  outline: none;
}

.news-category {
  color: var(--report-gold);
  font-weight: 700;
}

.news-impact {
  margin-top: 7px;
}

.verification-badge {
  margin-left: auto;
  padding: 2px 6px;
  border: 1px solid var(--report-line);
}

.verification-verified {
  border-color: rgba(53, 197, 139, 0.5);
  color: var(--rise);
}

.verification-partial {
  border-color: rgba(245, 158, 11, 0.5);
  color: var(--warning);
}

.verification-unverified {
  color: var(--report-muted);
}

.verification-contradicted {
  border-color: rgba(242, 113, 113, 0.5);
  color: var(--fall);
}

.news-item h4 {
  margin: 9px 0 6px;
  font-family: var(--report-display);
  font-size: 16px;
  line-height: 1.55;
}

.news-item p {
  color: #b5becc;
  font-size: 11px;
  line-height: 1.7;
}

.verification-note {
  margin-top: 8px;
  color: var(--report-muted);
  font-size: 9px;
}

.macro-table-head,
.macro-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 64px 82px;
  gap: 10px;
  align-items: center;
}

.macro-table-head {
  min-height: 32px;
  border-bottom: 1px solid var(--report-line);
  color: var(--report-muted);
  font-size: 9px;
}

.macro-table-head > :not(:first-child) {
  text-align: right;
}

.macro-row {
  min-height: 54px;
  padding: 8px 0;
  border-bottom: 1px solid var(--report-line-soft);
  font-size: 11px;
}

.macro-row > div span,
.macro-row > div small,
.macro-row > span small {
  display: block;
}

.macro-row > div small,
.macro-row > span small {
  margin-top: 2px;
  color: var(--report-muted);
  font-size: 8px;
  font-weight: 400;
}

.macro-row strong,
.macro-row > span {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.macro-positive {
  color: var(--rise);
}

.macro-negative {
  color: var(--fall);
}

.macro-neutral {
  color: var(--report-text);
}

.macro-impact {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-top: 4px;
}

.macro-caption {
  margin-top: 14px;
  color: var(--report-muted);
  font-size: 10px;
  line-height: 1.65;
}

.macro-summary {
  display: grid;
  gap: 0;
  margin-top: 12px;
}

.macro-summary > div {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 58px 52px;
  gap: 8px;
  align-items: baseline;
  padding: 8px 0;
  border-bottom: 1px solid var(--report-line-soft);
  font-size: 10px;
}

.macro-summary span {
  color: var(--report-muted);
}

.macro-summary strong,
.macro-summary small {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.macro-summary small {
  font-size: 9px;
}

.macro-toggle {
  width: 100%;
}

.claim-list {
  border-top: 1px solid var(--report-line);
}

.claim-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 0.95fr) minmax(280px, 1.05fr);
  gap: 18px;
  padding: 18px 0;
  border-bottom: 1px solid var(--report-line-soft);
}

.claim-number {
  color: var(--report-muted);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}

.claim-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.claim-title-row h3 {
  font-size: 13px;
}

.claim-badge {
  flex: 0 0 auto;
  padding: 2px 6px;
  border: 1px solid var(--report-line);
  color: var(--report-muted);
  font-size: 8px;
}

.claim-badge-supported {
  border-color: rgba(53, 197, 139, 0.45);
  color: var(--rise);
}

.claim-badge-partial {
  border-color: rgba(245, 158, 11, 0.45);
  color: var(--warning);
}

.claim-badge-unsupported,
.claim-badge-conflicted {
  border-color: rgba(242, 113, 113, 0.45);
  color: var(--fall);
}

.claim-assessment {
  margin-top: 7px;
  color: #b5becc;
  font-size: 11px;
  line-height: 1.65;
}

.claim-evidence {
  display: grid;
  gap: 7px;
  color: var(--report-muted);
  font-size: 10px;
  line-height: 1.6;
}

.claim-evidence span {
  display: inline-block;
  width: 32px;
  color: var(--report-gold);
}

.interpretation-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 42px;
}

.insight-list {
  counter-reset: insight;
}

.insight-list article {
  position: relative;
  padding: 0 0 22px 34px;
  counter-increment: insight;
}

.insight-list article + article {
  padding-top: 22px;
  border-top: 1px solid var(--report-line-soft);
}

.insight-list article::before {
  position: absolute;
  top: 2px;
  left: 0;
  color: var(--report-gold);
  font-family: var(--report-display);
  font-size: 18px;
  content: counter(insight);
}

.insight-list h3 {
  font-size: 14px;
}

.insight-list p {
  margin-top: 7px;
  color: #b7c0cd;
  font-family: var(--report-display);
  font-size: 15px;
  line-height: 1.8;
}

.risk-panel {
  align-self: start;
  padding: 18px;
  border: 1px solid rgba(242, 113, 113, 0.3);
  background: rgba(242, 113, 113, 0.04);
}

.risk-panel h3 {
  color: #f4aaa2;
  font-size: 13px;
}

.risk-panel ol {
  margin: 12px 0 0 18px;
  color: #c3b5b4;
  font-size: 11px;
  line-height: 1.75;
}

.risk-panel li + li {
  margin-top: 9px;
}

.watch-block,
.calendar-block {
  margin-top: 34px;
}

.watch-list,
.calendar-list {
  border-top: 1px solid var(--report-line);
}

.watch-row {
  display: grid;
  grid-template-columns: minmax(180px, 0.7fr) minmax(210px, 0.85fr) minmax(0, 1.45fr);
  gap: 22px;
  padding: 16px 0;
  border-bottom: 1px solid var(--report-line-soft);
}

.watch-heading h4 {
  font-size: 12px;
}

.watch-heading > div {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 8px;
}

.watch-current {
  color: var(--report-muted);
  font-size: 10px;
  line-height: 1.6;
}

.watch-conditions {
  display: grid;
  gap: 7px;
}

.watch-conditions p {
  color: #b1bac8;
  font-size: 10px;
  line-height: 1.55;
}

.watch-conditions span {
  display: inline-block;
  width: 32px;
  color: var(--report-gold);
}

.watch-conditions p:last-child span {
  color: var(--fall);
}

.calendar-row {
  display: grid;
  grid-template-columns: 120px 66px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  padding: 13px 0;
  border-bottom: 1px solid var(--report-line-soft);
}

.calendar-date time,
.calendar-date small {
  display: block;
  font-variant-numeric: tabular-nums;
}

.calendar-date time {
  color: var(--report-text);
  font-size: 10px;
}

.calendar-date small {
  margin-top: 2px;
  color: var(--report-muted);
  font-size: 8px;
}

.calendar-category {
  color: var(--report-gold);
  font-size: 9px;
}

.calendar-event h4 {
  font-size: 11px;
  line-height: 1.5;
}

.calendar-event a {
  text-decoration: underline;
  text-decoration-color: var(--report-line);
  text-underline-offset: 3px;
}

.calendar-event a:hover,
.calendar-event a:focus-visible {
  color: var(--report-gold);
  outline: none;
}

.calendar-event p {
  margin-top: 4px;
  color: var(--report-muted);
  font-size: 9px;
  line-height: 1.55;
}

.text-rise {
  color: var(--rise);
}

.text-fall {
  color: var(--fall);
}

.text-flat {
  color: var(--report-muted);
}

@media (max-width: 1120px) {
  .report-layout {
    grid-template-columns: 112px minmax(0, 1fr);
    gap: 28px;
  }

  .masthead-grid {
    grid-template-columns: minmax(0, 1fr) 240px;
    gap: 30px;
  }

  .market-metrics {
    grid-template-columns: 0.8fr 1.1fr;
  }

  .secondary-metrics {
    grid-column: 1 / -1;
    padding-top: 4px;
  }

  .signal-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .sentiment-components {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .signal-item:nth-child(3) {
    border-left: 0;
  }

  .signal-item:nth-child(n + 3) {
    border-top: 1px solid var(--report-line-soft);
  }

  .event-grid {
    grid-template-columns: 1fr 0.75fr;
    gap: 28px;
  }
}

@media (max-width: 820px) {
  .snapshot-bar {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .snapshot-time {
    width: 100%;
    margin-left: 0;
    white-space: normal;
  }

  .date-toolbar {
    align-items: flex-start;
    flex-direction: column;
    gap: 10px;
  }

  .date-tabs {
    width: 100%;
  }

  .date-tabs button {
    flex: 1 1 0;
  }

  .masthead-line {
    flex-wrap: wrap;
  }

  .issue-status {
    margin-left: 0;
  }

  .masthead-grid {
    grid-template-columns: 1fr;
  }

  .edition-meta {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .edition-meta > div {
    display: block;
    padding-right: 12px;
  }

  .edition-meta dd {
    margin-top: 5px;
    text-align: left;
  }

  .report-layout {
    display: block;
  }

  .section-rail {
    position: sticky;
    top: 0;
    z-index: 5;
    margin: 0 -12px;
    padding: 0 12px;
    border-right: 0;
    border-bottom: 1px solid var(--report-line);
    background: rgba(11, 18, 32, 0.96);
    backdrop-filter: blur(12px);
    scrollbar-width: none;
  }

  .section-rail::-webkit-scrollbar {
    display: none;
  }

  .rail-sticky {
    position: static;
    display: flex;
    gap: 4px;
    overflow-x: auto;
    padding: 8px 0;
  }

  .rail-title,
  .section-rail a small,
  .rail-evidence {
    display: none;
  }

  .section-rail a {
    flex: 0 0 auto;
    padding: 5px 10px;
    border: 1px solid var(--report-line);
  }

  .section-rail a span {
    font-size: 11px;
  }

  .report-section {
    padding: 34px 0 38px;
  }

  .day-delta {
    grid-template-columns: 1fr;
  }

  .delta-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .continuity-charts {
    grid-template-columns: 1fr;
    gap: 24px;
  }

  .side-line-charts {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    padding-top: 20px;
    padding-left: 0;
    border-top: 1px solid var(--report-line);
    border-left: 0;
  }

  .index-tape {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .index-cell:nth-child(3) {
    border-left: 0;
  }

  .index-cell:nth-child(n + 3) {
    border-top: 1px solid var(--report-line-soft);
  }

  .market-metrics,
  .structure-grid,
  .event-grid,
  .interpretation-layout {
    grid-template-columns: 1fr;
  }

  .sentiment-overview {
    grid-template-columns: 1fr;
    gap: 24px;
  }

  .sentiment-history {
    max-width: 460px;
    min-height: 130px;
  }

  .sentiment-components {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .index-anomalies {
    grid-template-columns: 1fr;
  }

  .index-anomaly,
  .index-anomaly + .index-anomaly {
    padding: 16px 0;
    border-top: 1px solid var(--report-line-soft);
    border-left: 0;
  }

  .index-anomaly:first-child {
    border-top: 0;
  }

  .stock-anomaly-head {
    display: none;
  }

  .stock-anomaly-row {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    gap: 14px;
  }

  .stock-analysis {
    grid-column: 1 / -1;
  }

  .style-panel,
  .macro-column {
    padding-top: 28px;
    padding-left: 0;
    border-top: 1px solid var(--report-line);
    border-left: 0;
  }

  .claim-row {
    grid-template-columns: 26px minmax(0, 1fr);
  }

  .claim-evidence {
    grid-column: 2;
  }

  .risk-panel {
    max-width: none;
  }

  .watch-row {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .watch-conditions {
    grid-column: 1 / -1;
  }

  .calendar-row {
    grid-template-columns: 96px 58px minmax(0, 1fr);
    gap: 12px;
  }
}

@media (max-width: 560px) {
  .date-tabs button {
    min-width: 0;
    flex-direction: column;
    gap: 2px;
  }

  .report-masthead {
    padding-top: 22px;
  }

  .date-main {
    font-size: 28px;
  }

  .headline-block h1 {
    font-size: 34px;
  }

  .report-subtitle {
    font-size: 16px;
  }

  .edition-meta {
    grid-template-columns: 1fr;
  }

  .edition-meta > div {
    display: grid;
    grid-template-columns: 82px 1fr;
    padding-right: 0;
  }

  .edition-meta dd {
    margin-top: 0;
    text-align: right;
  }

  .data-alert {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .section-heading {
    align-items: flex-start;
    flex-direction: column;
    gap: 5px;
  }

  .section-heading > span {
    text-align: left;
  }

  .thesis-block {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .thesis-block p {
    font-size: 18px;
  }

  .delta-grid {
    grid-template-columns: 1fr;
  }

  .chart-header {
    align-items: flex-start;
    flex-direction: column;
    gap: 7px;
  }

  .side-line-charts {
    grid-template-columns: 1fr;
  }

  .main-line-chart svg,
  .mini-line-chart svg {
    min-height: 0;
  }

  .index-tape {
    grid-template-columns: 1fr;
  }

  .index-cell,
  .index-cell + .index-cell {
    padding: 14px 0;
    border-top: 1px solid var(--report-line-soft);
    border-left: 0;
  }

  .index-cell:first-child {
    border-top: 0;
  }

  .index-cell-bottom svg {
    width: 110px;
  }

  .secondary-metrics {
    grid-template-columns: 1fr;
  }

  .secondary-metrics > div {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
  }

  .signal-strip {
    grid-template-columns: 1fr;
  }

  .signal-item,
  .signal-item + .signal-item {
    padding: 14px 0;
    border-top: 1px solid var(--report-line-soft);
    border-left: 0;
  }

  .signal-item:first-child {
    border-top: 0;
  }

  .sentiment-components {
    grid-template-columns: 1fr;
  }

  .stock-anomaly-row {
    grid-template-columns: 1fr;
  }

  .stock-analysis {
    grid-column: auto;
  }

  .thermal-labels {
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 2px;
    font-size: 7px;
  }

  .industry-columns {
    grid-template-columns: 1fr;
  }

  .industry-divider {
    height: 1px;
  }

  .news-meta {
    align-items: flex-start;
  }

  .verification-badge {
    margin-left: 0;
  }

  .macro-table-head,
  .macro-row {
    grid-template-columns: minmax(0, 1fr) 58px 74px;
  }

  .claim-title-row {
    align-items: flex-start;
    flex-direction: column;
  }

  .claim-evidence span {
    width: 28px;
  }

  .watch-row {
    grid-template-columns: 1fr;
  }

  .watch-conditions {
    grid-column: auto;
  }

  .calendar-row {
    grid-template-columns: 1fr;
    gap: 5px;
  }

  .calendar-event {
    margin-top: 4px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .section-rail a {
    transition: none;
  }
}
</style>



