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
  /** 去重键：同一 key 会话内只弹一次 */
  key?: string
  title?: string
  /** 自定义显示时长（毫秒），默认按级别：error=8000 / warning=6000 / info=4000 */
  duration?: number
}

let nextId = 1

/** 已弹过的 key（会话级），避免轮询/后台任务场景刷屏 */
const seenKeys = new Set<string>()

export const useToastStore = defineStore('toast', {
  state: () => ({
    items: [] as ToastItem[],
  }),
  actions: {
    /**
     * 弹出一条提示；带 key 时同一 key 会话内只弹一次（避免轮询刷屏）。
     */
    show(level: ToastLevel, message: string, options: ToastOptions = {}) {
      if (options.key) {
        if (seenKeys.has(options.key)) return
        seenKeys.add(options.key)
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
