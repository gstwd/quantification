/**
 * 用户主动触发运行任务后，轮询该任务直到结束；若结果为 skipped
 * （非交易日 / 并发冲突），按 reason 弹信息或警告提示，避免"点了没反应"。
 */

import { fetchRunDetail } from '../api/runs'
import { toast } from '../stores/toast'

/** 轮询上限（次），2 秒间隔 × 60 = 最长 2 分钟 */
const MAX_ATTEMPTS = 60
const POLL_INTERVAL_MS = 2000

/**
 * 监听一个运行任务是否以 skipped 结束，并在结束时弹提示。
 *
 * @param runId 触发刷新/重试后返回的 run_id
 */
export function notifySkippedRun(runId: string): void {
  let attempts = 0
  const timer = setInterval(async () => {
    attempts += 1
    if (attempts > MAX_ATTEMPTS) {
      clearInterval(timer)
      return
    }
    try {
      const detail = await fetchRunDetail(runId)
      if (detail.status === 'pending' || detail.status === 'running') return
      clearInterval(timer)
      if (detail.status !== 'skipped') return

      const reason = (detail.metrics as { reason?: string } | null)?.reason
      const key = `run-skip:${runId}`
      if (reason === 'holiday') {
        toast.info('今日为非交易日，本次数据刷新已跳过（未执行）', { key })
      } else if (reason === 'concurrent_skip') {
        toast.warning('已有同类任务正在执行，本次刷新已跳过（并发冲突）', { key })
      } else {
        toast.warning('本次运行任务被跳过，未执行', { key })
      }
    } catch {
      // 单次查询失败静默，等待下一轮重试
    }
  }, POLL_INTERVAL_MS)
}
