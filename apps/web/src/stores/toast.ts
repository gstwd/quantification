import { defineStore } from 'pinia'

/** 弹窗级别：info=信息 / warning=警告 / error=错误 */
export type ToastLevel = 'info' | 'warning' | 'error'

/** 单条弹窗数据 */
export interface ToastItem {
  id: number
  level: ToastLevel
  title?: string
  message: string
  /** 自动关闭时长（毫秒） */
  duration: number
}

/** show() 的可选参数 */
export interface ToastOptions {
  /** 去重键：默认同一 key 会话内只弹一次；配合 dedupeWindowMs 改为时间窗内去重 */
  key?: string
  /** 去重时间窗（毫秒）：设置后同一 key 只在该时间窗内去重，过后再次出现仍会弹出 */
  dedupeWindowMs?: number
  title?: string
  /** 自定义显示时长（毫秒），默认按级别：error=8000 / warning=6000 / info=4000 */
  duration?: number
}

let nextId = 1

/** 已弹过的 key → 最近一次弹出时间（毫秒），避免轮询/后台任务场景刷屏 */
const seenKeys = new Map<string, number>()

export const useToastStore = defineStore('toast', {
  state: () => ({
    items: [] as ToastItem[],
  }),
  actions: {
    /**
     * 弹出一条提示；带 key 时去重：默认会话内只弹一次，设置 dedupeWindowMs 时
     * 仅在时间窗内去重（用于操作失败提示：连点不刷屏，但之后再失败仍能看到）。
     */
    show(level: ToastLevel, message: string, options: ToastOptions = {}) {
      if (options.key) {
        const now = Date.now()
        const last = seenKeys.get(options.key)
        if (last !== undefined) {
          const window = options.dedupeWindowMs
          if (window === undefined || now - last < window) return
        }
        seenKeys.set(options.key, now)
      }
      const defaultDuration = level === 'error' ? 8000 : level === 'warning' ? 6000 : 4000
      this.items.push({
        id: nextId++,
        level,
        title: options.title,
        message,
        duration: options.duration ?? defaultDuration,
      })
    },
    /** 移除指定弹窗 */
    remove(id: number) {
      this.items = this.items.filter((item) => item.id !== id)
    },
  },
})

/** 全局便捷入口：toast.info / toast.warning / toast.error */
export const toast = {
  info(message: string, options?: ToastOptions) {
    useToastStore().show('info', message, options)
  },
  warning(message: string, options?: ToastOptions) {
    useToastStore().show('warning', message, options)
  },
  error(message: string, options?: ToastOptions) {
    useToastStore().show('error', message, options)
  },
}
