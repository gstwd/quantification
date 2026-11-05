/**
 * 用户主动触发运行任务后，轮询该任务直到结束；若结果为 skipped
 * （如同类任务已在执行），弹出后端记录的跳过原因，避免"点了没反应"。
 *
 * 必须在组件 setup 中调用（内部使用 usePolling，组件卸载时自动停止轮询）。
 */

import { ref } from 'vue'

import { fetchRunDetail } from '../api/runs'
import { toast } from '../stores/toast'
import type { ResearchRunDetail } from '../types/api'
import { usePolling } from './usePolling'

/** 轮询间隔（毫秒） */
const POLL_INTERVAL_MS = 2000
/** 最长跟踪时长（毫秒）：超过后停止，不再提示 */
const MAX_TRACK_MS = 2 * 60 * 1000

/**
 * 创建「运行任务被跳过」提示器。
 *
 * @returns notify(runId)：开始跟踪该运行记录，结束为 skipped 时弹出原因
 */
export function useRunSkipToast() {
  const runId = ref<string | null>(null)
  let startedAt = 0

  const polling = usePolling<ResearchRunDetail>({
    fetcher: () => fetchRunDetail(runId.value ?? ''),
    intervalMs: POLL_INTERVAL_MS,
    isDone: (detail) =>
      (detail.status !== 'pending' && detail.status !== 'running') || Date.now() - startedAt > MAX_TRACK_MS,
    onData: (detail) => {
      if (detail.status !== 'skipped') return
      // 后端把跳过原因写成中文（如「同范围数据管理任务已在执行」），直接展示
      const reason = (detail.metrics as { reason?: unknown } | null)?.reason
      const text = typeof reason === 'string' && reason.trim() ? reason.trim() : '未记录原因'
      toast.warning(`本次运行任务已跳过（未执行）：${text}`, { key: `run-skip:${detail.run_id}` })
    },
  })

  /**
   * 开始跟踪一个运行记录；已在跟踪其他记录时先停止。
   *
   * @param id - 触发刷新 / 重试后返回的 run_id
   */
  function notify(id: string): void {
    polling.stop()
    runId.value = id
    startedAt = Date.now()
    void polling.start()
  }

  return { notify }
}
