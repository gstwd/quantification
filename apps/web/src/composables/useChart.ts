/**
 * ECharts 实例生命周期 composable。
 *
 * 统一处理：
 * - 按模板 ref 初始化实例（使用暗色主题 CHART_THEME）；
 * - 容器 DOM 被 v-if/v-else 重建时自动丢弃旧实例，下次 render 绑定到新节点
 *   （避免旧实例画到已脱离文档的节点上、图表永久空白）；
 * - ResizeObserver 跟随容器尺寸变化 resize；
 * - 组件卸载时 dispose 并断开观察器。
 */

import { onUnmounted, watch, type Ref } from 'vue'
import type { ECharts, EChartsCoreOption } from 'echarts'

import { CHART_THEME, loadEcharts } from '../utils/echarts'

/** render 选项 */
export interface ChartRenderOptions {
  /** 是否整体替换旧配置，默认 true（避免上一次的系列残留） */
  notMerge?: boolean
}

/**
 * 绑定一个图表容器。
 *
 * @param elRef - 图表容器的模板 ref
 * @returns render 渲染配置并返回实例（容器不存在时返回 null）；instance 取当前实例；dispose 手动释放
 */
export function useChart(elRef: Ref<HTMLElement | null | undefined>) {
  let chart: ECharts | null = null
  let boundEl: HTMLElement | null = null
  let observer: ResizeObserver | null = null

  /** 释放实例与观察器 */
  function dispose(): void {
    observer?.disconnect()
    observer = null
    if (chart && !chart.isDisposed()) chart.dispose()
    chart = null
    boundEl = null
  }

  /**
   * 渲染配置；容器变更或实例已释放时重新初始化。
   *
   * @param option - ECharts 配置
   * @param options - 渲染选项
   * @returns 当前实例；容器未挂载时为 null
   */
  async function render(option: EChartsCoreOption, options: ChartRenderOptions = {}): Promise<ECharts | null> {
    const el = elRef.value
    if (!el) return null
    const echarts = await loadEcharts()
    // 异步加载期间容器可能已被替换
    if (elRef.value !== el) return null
    if (!chart || chart.isDisposed() || boundEl !== el) {
      dispose()
      chart = echarts.init(el, CHART_THEME, { renderer: 'canvas' })
      boundEl = el
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => chart?.resize())
        observer.observe(el)
      }
    }
    chart.setOption(option, { notMerge: options.notMerge ?? true })
    return chart
  }

  /** 当前实例（可能为 null） */
  function instance(): ECharts | null {
    return chart
  }

  // 容器被移除或替换时丢弃旧实例
  watch(elRef, (el) => {
    if (el !== boundEl && chart) dispose()
  })

  onUnmounted(dispose)

  return { render, instance, dispose }
}
