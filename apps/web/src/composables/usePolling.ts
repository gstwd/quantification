/**
 * 统一轮询 composable：自动绑定组件生命周期（组件卸载即停止轮询）。
 * 用于"后台任务执行中，页面定时刷新结果"的场景，替代散落在各 store/页面中的
 * setInterval 实现，避免页面切走后轮询仍在持续请求。
 */

import { onUnmounted, ref } from 'vue'

/** usePolling 的配置项 */
export interface UsePollingOptions<T> {
  /** 每次轮询执行的请求，返回最新数据 */
  fetcher: () => Promise<T>
  /** 判断请求结果是否表示任务已完成，完成后自动停止轮询 */
  isDone: (data: T) => boolean
  /** 轮询间隔（毫秒），默认 2000 */
  intervalMs?: number
  /** 启动时是否立即执行一次请求，默认 true */
  immediate?: boolean
  /** 每次成功轮询拿到数据后的回调（用于同步 store/页面状态） */
  onData?: (data: T) => void
  /** 连续请求失败多少次后停止轮询，默认 5；0 表示失败不停止 */
  maxConsecutiveErrors?: number
  /** 连续失败达到阈值、轮询停止时的回调（用于弹一次错误提示） */
  onMaxErrors?: () => void
}

/**
 * 创建轮询器。
 *
 * 返回的 start() 会启动轮询并返回一个 Promise，轮询结束（任务完成 / 手动停止 /
 * 组件卸载）时 resolve；stop() 手动停止；polling 表示是否正在轮询；done 表示
 * 是否已到达 isDone 判定完成。
 *
 * 单次请求失败不会终止轮询（避免瞬时网络错误打断状态刷新），连续失败超过
 * maxConsecutiveErrors 才停止，防止后端不可用时无限请求。
 */
export function usePolling<T>(options: UsePollingOptions<T>) {
  const {
    fetcher,
    isDone,
    intervalMs = 2000,
    immediate = true,
    onData,
    maxConsecutiveErrors = 5,
    onMaxErrors,
  } = options

  /** 是否正在轮询 */
  const polling = ref(false)
  /** 是否已到达终止条件（任务完成） */
  const done = ref(false)

  let timer: ReturnType<typeof setInterval> | null = null
  let consecutiveErrors = 0
  let resolveDone: (() => void) | null = null
  let inFlight = false

  /** 停止轮询并通知等待 start() 的调用方 */
  const stop = () => {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
    polling.value = false
    if (resolveDone) {
      resolveDone()
      resolveDone = null
    }
  }

  /** 执行一次轮询 */
  const tick = async () => {
    // 上一次请求尚未返回时跳过本次触发，避免慢请求叠加
    if (inFlight) return
    inFlight = true
    try {
      const data = await fetcher()
      consecutiveErrors = 0
      onData?.(data)
      if (isDone(data)) {
        done.value = true
        stop()
      }
    } catch {
      // 单次失败不终止，连续失败超过阈值才停止，避免死循环请求
      consecutiveErrors += 1
      if (maxConsecutiveErrors > 0 && consecutiveErrors >= maxConsecutiveErrors) {
        stop()
        onMaxErrors?.()
      }
    } finally {
      inFlight = false
    }
  }

  /** 启动轮询；已在轮询中时直接返回 */
  const start = (): Promise<void> => {
    if (timer !== null) return Promise.resolve()
    consecutiveErrors = 0
    polling.value = true
    done.value = false
    return new Promise<void>((resolve) => {
      resolveDone = resolve
      if (immediate) void tick()
      timer = setInterval(() => {
        void tick()
      }, intervalMs)
    })
  }

  // 组件卸载时自动停止，轮询不脱离页面生命周期
  onUnmounted(stop)

  return { start, stop, polling, done }
}
