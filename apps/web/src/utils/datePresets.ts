/** 回测日期快捷选取预设。

提供"近1年 / 近3年 / 近5年 / 全部"的日期区间预设，
all_start 为数据起始参考日期（会被后端实际数据自动裁剪）。
 */

const ALL_START = '2010-01-01'

/** 日期预设选项 */
export interface DatePreset {
  label: string
  start: string
  end: string
}

/** 获取日期预设选项列表 */
export function getDatePresets(): DatePreset[] {
  const end = todayCn()

  /** 偏移年份（粗略，后端会精确对齐到最近交易日） */
  const yearsAgo = (n: number): string => {
    const [year, month, day] = end.split('-').map(Number)
    return `${year - n}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
  }

  return [
    { label: '近1年', start: yearsAgo(1), end },
    { label: '近3年', start: yearsAgo(3), end },
    { label: '近5年', start: yearsAgo(5), end },
    { label: '全部', start: ALL_START, end },
  ]
}

import { todayCn } from './date'
