import { defineStore } from 'pinia'

import { fetchEtfDetail, fetchEtfs } from '../api/etfs'
import type { EtfDetail } from '../types/api'

export const useEtfStore = defineStore('etfs', {
  state: () => ({
    items: [] as EtfDetail[],
    current: null as EtfDetail | null,
    loading: false,
  }),
  actions: {
    async loadAll() {
      this.loading = true
      try {
        this.items = await fetchEtfs()
      } finally {
        this.loading = false
      }
    },
    async loadOne(etfCode: string) {
      this.loading = true
      try {
        this.current = await fetchEtfDetail(etfCode)
      } finally {
        this.loading = false
      }
    },
  },
})
