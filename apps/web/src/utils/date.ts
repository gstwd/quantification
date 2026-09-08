/** 时间与业务日期工具，统一使用北京时间处理交易日。 */

/**
 * 将当前时间转换为北京时间下的日期字符串。
 *
 * @returns YYYY-MM-DD 格式的北京时间日期
 */
export function todayCn(): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date())
}

/**
 * 按北京时间对日期字符串进行自然日偏移。
 *
 * @param value - YYYY-MM-DD 日期字符串
 * @param days - 偏移天数
 * @returns 偏移后的 YYYY-MM-DD 日期字符串
 */
export function shiftDateCn(value: string, days: number): string {
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(Date.UTC(year, month - 1, day + days, 0, 0, 0))
  return date.toISOString().slice(0, 10)
}

/**
 * 将日期字符串格式化为本地日期，避免浏览器按 UTC 解析日期。
 *
 * @param value - YYYY-MM-DD 日期字符串
 * @returns 本地日期对象
 */
export function parseDateCn(value: string): Date {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day)
}

/**
 * 格式化后端 UTC 时间戳为北京时间显示文本。
 *
 * @param value - ISO 时间戳
 * @returns 中文本地时间文本
 */
export function formatCnTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}
