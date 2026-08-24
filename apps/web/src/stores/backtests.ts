import { defineStore } from 'pinia'

import {
  createBacktest,
  createComparison,
  fetchBacktest,
  fetchBacktestDaily,
  fetchBacktestIndexResults,
  fetchBacktests,
  fetchComparison,
  fetchComparisons,
  fetchComparisonDaily,
} from '../api/backtests'
import type {
  BacktestCreateRequest,
  BacktestDetail,
  BacktestDailyResult,
  BacktestIndexResult,
  BacktestSummary,
  ComparisonCreateRequest,
  ComparisonDetail,
  ComparisonDailyResponse,
  ComparisonSummary,
} from '../types/api'

export const useBacktestStore = defineStore('backtests', {
  state: () => ({
    items: [] as BacktestSummary[],
    total: 0,
    current: null as BacktestDetail | null,
    dailyResults: [] as BacktestDailyResult[],
    indexResults: [] as BacktestIndexResult[],
    loading: false,
    submitting: false,
    // ── 策略对比回测 ──
    comparisons: [] as ComparisonSummary[],
    comparisonsTotal: 0,
    currentComparison: null as ComparisonDetail | null,
    comparisonDaily: null as ComparisonDailyResponse | null,
  }),
  actions: {
    async loadAll(offset = 0, limit = 50) {
      this.loading = true
      try {
        const res = await fetchBacktests(offset, limit)
        this.items = res.items
        this.total = res.total
      } finally {
        this.loading = false
      }
    },
    async loadOne(backtestId: string) {
      this.loading = true
      try {
        this.current = await fetchBacktest(backtestId)
      } finally {
        this.loading = false
      }
    },
    async loadDailyResults(backtestId: string) {
      this.dailyResults = await fetchBacktestDaily(backtestId)
    },
    async loadIndexResults(backtestId: string, indexCode?: string) {
      this.indexResults = await fetchBacktestIndexResults(backtestId, indexCode)
    },
    /** 拉取单个回测详情并同步到 current 与 items（轮询由 usePolling 驱动） */
    async refreshOne(backtestId: string): Promise<BacktestDetail> {
      const detail = await fetchBacktest(backtestId)
      // 每次轮询都更新 current，确保进度条等 UI 实时刷新
      this.current = detail
      const idx = this.items.findIndex((i) => i.backtest_id === backtestId)
      if (idx !== -1) this.items[idx] = detail
      return detail
    },
    async submit(req: BacktestCreateRequest): Promise<BacktestSummary> {
      this.submitting = true
      try {
        const summary = await createBacktest(req)
        this.items.unshift(summary)
        return summary
      } finally {
        this.submitting = false
      }
    },

    // ── 策略对比回测 ──

    async loadAllComparisons(offset = 0, limit = 50) {
      this.loading = true
      try {
        const res = await fetchComparisons(offset, limit)
        this.comparisons = res.items
        this.comparisonsTotal = res.total
      } finally {
        this.loading = false
      }
    },

    async loadOneComparison(comparisonId: string) {
      this.loading = true
      try {
        this.currentComparison = await fetchComparison(comparisonId)
      } finally {
        this.loading = false
      }
    },

    /** 拉取单个对比回测详情并同步到 currentComparison 与 comparisons（轮询由 usePolling 驱动） */
    async refreshOneComparison(comparisonId: string): Promise<ComparisonDetail> {
      const detail = await fetchComparison(comparisonId)
      this.currentComparison = detail
      const idx = this.comparisons.findIndex(
        (c) => c.comparison_id === comparisonId,
      )
      if (idx !== -1) this.comparisons[idx] = detail
      return detail
    },

    async loadComparisonDaily(comparisonId: string) {
      this.comparisonDaily = await fetchComparisonDaily(comparisonId)
    },

    async submitComparison(
      req: ComparisonCreateRequest,
    ): Promise<ComparisonSummary> {
      this.submitting = true
      try {
        const summary = await createComparison(req)
        this.comparisons.unshift(summary)
        return summary
      } finally {
        this.submitting = false
      }
    },
  },
})
