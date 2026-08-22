/** 平台无关的 HTTP 抽象：web 用 fetch 实现，小程序用 Taro.request 实现。 */

export interface HttpOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE"
  body?: unknown
}

/**
 * 发 API 请求的函数类型。path 含 /api 前缀（web 相对路径走 vite proxy / 同源；
 * 小程序端实现时自行拼接 base URL）。非 2xx 时必须抛 Error(extractDetail(...))。
 */
export type RequestFn = <T>(path: string, opts?: HttpOptions) => Promise<T>

/** 统一错误语义：优先取 FastAPI 的 detail 字段，否则给通用文案。 */
export function extractDetail(status: number, data: unknown): string {
  const fallback = `请求失败（${status}）`
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail?: unknown }).detail
    if (typeof detail === "string") return detail
  }
  return fallback
}
