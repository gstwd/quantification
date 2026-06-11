import { defineStore } from 'pinia'

import {
  createBacktest,
  fetchBacktest,
  fetchBacktestDaily,
  fetchBacktestIndexResults,
  fetchBacktests,
} from '../api/backtests'
import type {
  BacktestCreateRequest,
  BacktestDetail,
  BacktestDailyResult,
  BacktestIndexResult,
  BacktestSummary,
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
    /** 每 2 秒轮询一次，直到回测状态不再是 pending 或 running */
    async pollUntilDone(backtestId: string): Promise<void> {
      const poll = (): Promise<void> =>
        new Promise((resolve) => {
          const timer = setInterval(async () => {
            try {
              const detail = await fetchBacktest(backtestId)
              // 每次轮询都更新 current，确保进度条等 UI 实时刷新
              this.current = detail
              const idx = this.items.findIndex((i) => i.backtest_id === backtestId)
              if (idx !== -1) this.items[idx] = detail
              if (detail.status !== 'pending' && detail.status !== 'running') {
                clearInterval(timer)
                resolve()
              }
            } catch {
              clearInterval(timer)
              resolve()
            }
          }, 2000)
        })
      await poll()
    },
  },
})
