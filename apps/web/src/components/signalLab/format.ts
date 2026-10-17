/** 研究台数值格式化工具。 */

/**
 * 格式化为百分比文本。
 *
 * @param value - 小数形式数值
 * @param digits - 小数位
 * @returns 百分比文本，空值返回 “—”
 */
export function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

/**
 * 格式化为定点数文本。
 *
 * @param value - 数值
 * @param digits - 小数位
 * @returns 数值文本，空值返回 “—”
 */
export function fmtNum(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  return value.toFixed(digits)
}

/**
 * 按正负返回语义色类名。
 *
 * @param value - 数值
 * @returns pos / neg / 空字符串
 */
export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value === 0) return ''
  return value > 0 ? 'pos' : 'neg'
}

/** 图表通用配色（与 theme.css 深色主题一致） */
export const CHART_COLORS = {
  axis: '#94a3b8',
  grid: '#334155',
  price: '#e2e8f0',
  signal: '#3b82f6',
  strategy: '#10b981',
  benchmark: '#94a3b8',
  holding: 'rgba(16, 185, 129, 0.14)',
  reference: '#f59e0b',
  palette: ['#f59e0b', '#ec4899', '#8b5cf6', '#14b8a6', '#f97316'],
}
