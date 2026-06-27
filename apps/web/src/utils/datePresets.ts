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
  const today = new Date()
  const end = toDateStr(today)

  /** 偏移年份（粗略，后端会精确对齐到最近交易日） */
  const yearsAgo = (n: number): string => {
    const d = new Date(today)
    d.setFullYear(d.getFullYear() - n)
    return toDateStr(d)
  }

  return [
    { label: '近1年', start: yearsAgo(1), end },
    { label: '近3年', start: yearsAgo(3), end },
    { label: '近5年', start: yearsAgo(5), end },
    { label: '全部', start: ALL_START, end },
  ]
}

/** 将 Date 转为 yyyy-mm-dd 格式 */
function toDateStr(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
