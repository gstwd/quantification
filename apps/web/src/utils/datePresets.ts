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
  const completedYearPresets: DatePreset[] = Array.from({ length: 5 }, (_, index) => {
    const year = currentYear - index - 1
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
