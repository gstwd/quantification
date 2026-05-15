import { defineStore } from 'pinia'

import { createEtf, deleteEtf, fetchEtfDetail, fetchEtfs } from '../api/etfs'
import type { EtfCreatePayload, EtfDetail } from '../types/api'

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
    async addEtf(payload: EtfCreatePayload) {
      const newEtf = await createEtf(payload)
      this.items.push(newEtf)
      this.items.sort((a, b) => a.etf_code.localeCompare(b.etf_code))
    },
    async removeEtf(etfCode: string) {
      await deleteEtf(etfCode)
      this.items = this.items.filter(e => e.etf_code !== etfCode)
    },
  },
})
