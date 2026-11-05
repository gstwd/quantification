/** 回测日期快捷选取预设。

提供研究期与验证期的日期区间预设。系统把 2016-01-01 ~ 2025-12-31 固定为
研究期、2026-01-01 起为验证期：研究类回测不得越过研究期末端，验证期数据
只能用于验收与监控（且会留痕）。
 */

/** 研究期起始日（用户约定：所有研究类回测的区间下限） */
export const RESEARCH_START = '2016-01-01'

/** 研究期截止日（研究类回测的区间上限） */
export const RESEARCH_END = '2025-12-31'

/** 验证期起始日（验收与监控可用，且会留痕） */
export const VALIDATION_START = '2026-01-01'

/** 日期预设选项 */
export interface DatePreset {
  label: string
  start: string
  end: string
}

/**
 * 按回测用途筛选可用的日期预设：研究用途只给研究期内的区间，验证 / 监控只给验证期区间，
 * 避免选到后端必然拒绝的组合。
 *
 * @param purpose - 回测用途（research / validation / monitor）
 * @returns 可用预设
 */
export function getDatePresetsForPurpose(purpose: string): DatePreset[] {
  return getDatePresets().filter(p =>
    purpose === 'research' ? p.end <= RESEARCH_END : p.start >= VALIDATION_START,
  )
}

/**
 * 回测区间与用途的期间规则（与后端 domain/research/periods.py 一致）。
 *
 * @returns 违规说明；合规时返回空串
 */
export function periodRuleError(purpose: string, start: string, end: string): string {
  if (!start || !end) return ''
  if (start > end) return '起始日期不能晚于结束日期'
  if (purpose === 'research') {
    if (start < RESEARCH_START) return `研究类回测不得早于研究期起点 ${RESEARCH_START}`
    if (end > RESEARCH_END) return `研究类回测不得越过研究期末端 ${RESEARCH_END}（验证期数据只能用于验收与监控）`
    return ''
  }
  if (start < VALIDATION_START) return `验证 / 监控回测必须从验证期起点 ${VALIDATION_START} 或之后开始`
  return ''
}

/** 获取日期预设选项列表 */
export function getDatePresets(): DatePreset[] {
  const today = todayCn()
  // 研究类预设一律落在研究期内，避免用户选到验证期后提交被后端拒绝
  const end = RESEARCH_END

  /** 偏移年份（粗略，后端会精确对齐到最近交易日） */
  const yearsAgo = (n: number): string => {
    const [year, month, day] = end.split('-').map(Number)
    return `${year - n}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
  }

  const currentYear = Number(end.slice(0, 4))
  // 研究期内最近 6 个完整年度（含研究期最后一年）
  const completedYearPresets: DatePreset[] = Array.from({ length: 6 }, (_, index) => {
    const year = currentYear - index
    return { label: `${year}年`, start: `${year}-01-01`, end: `${year}-12-31` }
  })

  return [
    { label: '研究期全段', start: RESEARCH_START, end: RESEARCH_END },
    { label: '近1年', start: yearsAgo(1), end },
    { label: '近3年', start: yearsAgo(3), end },
    { label: '近5年', start: yearsAgo(5), end },
    { label: '验证期至今', start: VALIDATION_START, end: today },
    ...completedYearPresets,
  ]
}

import { todayCn } from './date'
