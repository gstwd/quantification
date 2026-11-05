/**
 * ECharts 按需加载与暗色主题注册。
 *
 * 各页面统一通过 loadEcharts() 动态加载 echarts（保持分包），首次加载时注册
 * 暗色主题 CHART_THEME：图例、坐标轴、坐标轴名称、分隔线、tooltip、缩放条
 * 等默认配色取自 styles/theme.css，避免未显式配色的图表沿用 ECharts 默认的
 * 浅色主题（深灰文字、白底 tooltip）在暗色背景上不可读。
 */

import type * as EchartsModule from 'echarts'

/** 暗色主题名称 */
export const CHART_THEME = 'quant-dark'

const TEXT = '#f1f5f9'
const TEXT_MUTED = '#94a3b8'
const BORDER = '#334155'
const SURFACE = '#1e293b'
const ACCENT = '#3b82f6'

/** 坐标轴通用暗色样式 */
const axisStyle = {
  axisLine: { lineStyle: { color: BORDER } },
  axisTick: { lineStyle: { color: BORDER } },
  axisLabel: { color: TEXT_MUTED },
  splitLine: { lineStyle: { color: BORDER, type: 'dashed' } },
  splitArea: { areaStyle: { color: ['rgba(255,255,255,0.02)', 'rgba(255,255,255,0)'] } },
  nameTextStyle: { color: TEXT_MUTED },
}

const darkTheme = {
  backgroundColor: 'transparent',
  color: ['#3b82f6', '#f59e0b', '#22c55e', '#a855f7', '#ef4444', '#06b6d4', '#ec4899', '#84cc16', '#94a3b8'],
  textStyle: { color: TEXT_MUTED },
  title: { textStyle: { color: TEXT }, subtextStyle: { color: TEXT_MUTED } },
  legend: { textStyle: { color: TEXT_MUTED }, inactiveColor: '#475569' },
  tooltip: {
    backgroundColor: SURFACE,
    borderColor: BORDER,
    textStyle: { color: TEXT },
    axisPointer: { lineStyle: { color: TEXT_MUTED }, crossStyle: { color: TEXT_MUTED } },
  },
  categoryAxis: axisStyle,
  valueAxis: axisStyle,
  logAxis: axisStyle,
  timeAxis: axisStyle,
  dataZoom: {
    borderColor: BORDER,
    fillerColor: 'rgba(59,130,246,0.1)',
    handleColor: ACCENT,
    handleStyle: { color: ACCENT },
    textStyle: { color: TEXT_MUTED },
    dataBackgroundColor: BORDER,
  },
  visualMap: { textStyle: { color: TEXT_MUTED } },
  markPoint: { label: { color: TEXT } },
}

let loading: Promise<typeof EchartsModule> | null = null

/**
 * 动态加载 echarts，并在首次加载时注册暗色主题。
 *
 * @returns echarts 模块
 */
export function loadEcharts(): Promise<typeof EchartsModule> {
  if (!loading) {
    loading = import('echarts').then((mod) => {
      mod.registerTheme(CHART_THEME, darkTheme)
      return mod
    })
  }
  return loading
}
