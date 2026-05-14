import { defineStore } from 'pinia'

import { fetchStrategies, fetchStrategyDetail } from '../api/strategies'
import type { StrategyDetail } from '../types/api'

export const useStrategyStore = defineStore('strategies', {
  state: () => ({
    items: [] as StrategyDetail[],
    current: null as StrategyDetail | null,
    loading: false,
  }),
  actions: {
    async loadAll() {
      this.loading = true
      try {
        this.items = await fetchStrategies()
      } finally {
        this.loading = false
      }
    },
    async loadOne(strategyId: string) {
      this.loading = true
      try {
        this.current = await fetchStrategyDetail(strategyId)
      } finally {
        this.loading = false
      }
    },
  },
})
