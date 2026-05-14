import { defineStore } from 'pinia'

import { fetchLatestSignals } from '../api/signals'
import type { SignalRow } from '../types/api'

export const useSignalStore = defineStore('signals', {
  state: () => ({
    items: [] as SignalRow[],
    loading: false,
  }),
  actions: {
    async loadLatest(strategyId: string) {
      this.loading = true
      try {
        this.items = await fetchLatestSignals(strategyId)
      } finally {
        this.loading = false
      }
    },
  },
})
