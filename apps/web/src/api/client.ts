import axios from 'axios'

/**
 * API 客户端配置
 * - 生产环境：使用相对路径 /api（由 Nginx 反向代理至后端）
 * - 开发环境：使用相对路径 /api（由 Vite dev server 代理至后端）
 */
export const apiClient = axios.create({
  baseURL: '/api',
  timeout: 30000,
})
