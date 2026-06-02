import { defineStore } from 'pinia'

import { fetchLatestSignals } from '../api/signals'
import type { SignalRow } from '../types/api'

export const useSignalStore = defineStore('signals', {
  state: () => ({
    items: [] as SignalRow[],
    total: 0,
    loading: false,
  }),
  actions: {
    async loadLatest(strategyId: string, offset = 0, limit = 50) {
      this.loading = true
      try {
        const res = await fetchLatestSignals(strategyId, offset, limit)
        this.items = res.items
        this.total = res.total
      } finally {
        this.loading = false
      }
    },
  },
})
