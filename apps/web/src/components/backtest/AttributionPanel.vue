<template>
  <div class="attribution">
    <div class="header">
      <h3 class="title">收益归因<span class="unit-note">（单位：百分点 pp，毛口径）</span></h3>
      <span v-if="data?.benchmark_label" class="tag">基准：{{ data.benchmark_label }}</span>
      <span v-if="data?.approximate" class="tag warn">近似（开盘执行）</span>
    </div>
    <div v-if="loading" class="muted">加载中...</div>
    <div v-else-if="error" class="muted">{{ error }}</div>
    <template v-else-if="data">
      <table class="table">
        <thead>
          <tr>
            <th>区间</th><th>组合</th><th>基准</th><th>择时（仓位）</th><th>选择（成分）</th><th>残差</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in [data.total, ...data.yearly]" :key="row.label" :class="{ total: row.label === data.total.label }">
            <td>{{ row.label }}</td>
            <td>{{ pct(row.portfolio_pct) }}</td>
            <td>{{ pct(row.benchmark_pct) }}</td>
            <td>{{ pct(row.timing_pct) }}</td>
            <td>{{ pct(row.selection_pct) }}</td>
            <td>{{ pct(row.residual_pct) }}</td>
          </tr>
        </tbody>
      </table>
      <div class="sub-title">资产贡献（Σ 权重 × 资产收益，前 10）</div>
      <table class="table">
        <thead>
          <tr><th>指数</th><th>贡献</th><th>持有天数</th><th>平均权重</th><th>胜率</th></tr>
        </thead>
        <tbody>
          <tr v-for="asset in data.assets.slice(0, 10)" :key="asset.index_code">
            <td class="mono">{{ asset.index_code }}</td>
            <td>{{ pct(asset.contribution_pct) }}</td>
            <td>{{ asset.held_days }}</td>
            <td>{{ (asset.average_weight * 100).toFixed(1) }}%</td>
            <td>{{ (asset.win_rate * 100).toFixed(0) }}%</td>
          </tr>
        </tbody>
      </table>
      <p class="muted">
        组合 − 基准 = 择时（(总仓位 − 1) × 基准收益）+ 选择（Σ 权重 × (资产 − 基准)）+ 残差。{{ data.note }}
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * 回测详情页的收益归因面板：读取路径现算，只读。
 */
import { onMounted, ref, watch } from 'vue'
import { fetchBacktestAttribution } from '../../api/backtests'
import { getErrorMessage } from '../../api/client'
import { formatCnTime } from '../../utils/date'
import type { BacktestAttribution } from '../../types/api'

const props = defineProps<{
  backtestId: string
  /** 回测状态：只有 success 才请求归因 */
  status: string
  /** 逐指数明细被研究数据瘦身删除的时间；有值时不再请求归因 */
  prunedAt?: string | null
}>()

const data = ref<BacktestAttribution | null>(null)
const loading = ref(false)
const error = ref('')

/** 百分点展示（缺失为 —） */
/** 百分点（pp）格式化 */
function pct(value: number | null | undefined): string {
  return value == null ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}pp`
}

async function load(): Promise<void> {
  if (props.status !== 'success') {
    data.value = null
    error.value = '回测完成后可查看归因'
    return
  }
  if (props.prunedAt) {
    data.value = null
    error.value = `逐指数明细已于 ${formatCnTime(props.prunedAt)} 瘦身清理，无法归因；需要时用同一配置重跑回测`
    return
  }
  loading.value = true
  error.value = ''
  try {
    data.value = await fetchBacktestAttribution(props.backtestId)
  } catch (e) {
    error.value = getErrorMessage(e, '归因加载失败')
  } finally {
    loading.value = false
  }
}

watch(() => [props.backtestId, props.status, props.prunedAt], load)
onMounted(load)
</script>

<style scoped>
.attribution { display: flex; flex-direction: column; gap: 10px; }
.header { display: flex; align-items: center; gap: 8px; }
.title { font-size: 15px; font-weight: 600; margin: 0; }
.sub-title { font-size: 13px; font-weight: 600; margin-top: 6px; }
.tag { font-size: 11px; padding: 2px 6px; border-radius: 4px; background: var(--surface-2); color: var(--text-muted); }
.tag.warn { color: #f59e0b; }
.table { width: 100%; border-collapse: collapse; font-size: 12px; }
.table th, .table td { padding: 4px 8px; border-bottom: 1px solid var(--border); text-align: right; }
.table th:first-child, .table td:first-child { text-align: left; }
.table tr.total td { font-weight: 600; }
.mono { font-family: var(--font-mono); }
.muted { color: var(--text-muted); font-size: 12px; }
.unit-note { margin-left: 6px; font-size: 12px; font-weight: 400; color: var(--text-muted); }
</style>
