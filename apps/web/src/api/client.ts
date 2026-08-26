import axios, { AxiosError } from 'axios'

import { toast } from '../stores/toast'

declare module 'axios' {
  export interface AxiosRequestConfig {
    /** 主动操作请求传 false 时，跳过拦截器的自动错误弹窗（页面已有自有提示） */
    toast?: boolean
  }
}

/**
 * API 客户端配置
 * - 生产环境：使用相对路径 /api（由 Nginx 反向代理至后端）
 * - 开发环境：使用相对路径 /api（由 Vite dev server 代理至后端）
 */
export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

/**
 * 从 Axios 错误中提取用户可读的提示文案。
 *
 * 优先读取 FastAPI 的 detail 字段（string 或 422 校验数组），
 * 其次区分网络错误与超时，最后回退到 HTTP 状态描述。
 */
function extractErrorMessage(error: AxiosError<{ detail?: unknown }>): string {
  if (!error.response) {
    if (error.code === 'ECONNABORTED') return '请求超时，请稍后重试'
    return '网络错误或服务不可达，请检查服务是否启动'
  }
  const { status, data } = error.response
  const detail = data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((item) => {
        const loc = (item?.loc as unknown[] | undefined) ?? []
        const msg = typeof item?.msg === 'string' ? item.msg : '参数不合法'
        return loc.length > 0 ? `${loc.join('.')}: ${msg}` : msg
      })
      .join('；')
  }
  return `请求失败（HTTP ${status}）`
}

// 响应拦截器：统一解析后端错误。
// 弹窗策略：仅对用户主动操作（POST/PUT/PATCH/DELETE）自动弹出错误；
// GET 只读请求保持页面内联状态，避免后台加载/轮询噪音。
// 单个请求可通过配置 { toast: false } 关闭自动弹窗（页面已有自有提示时）。
apiClient.interceptors.response.use(
  (response) => response,
  (error: unknown) => {
    const err = error as AxiosError<{ detail?: unknown }>
    const method = (err.config?.method ?? 'get').toUpperCase()
    const isMutation = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)
    const silent = (err.config as { toast?: boolean } | undefined)?.toast === false
    if (isMutation && !silent) {
      toast.error(extractErrorMessage(err), {
        key: `${method}:${err.config?.url ?? 'unknown'}`,
      })
    }
    return Promise.reject(error)
  },
)
