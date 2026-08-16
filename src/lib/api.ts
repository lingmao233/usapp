/** API 客户端（web 组装壳）：平台无关核心在 @/core，这里注入 fetch 与 localStorage。
 *
 * 只走相对路径 /api，由 vite proxy（开发）或同源后端（生产）转发到 FastAPI。
 * 导出签名与重构前完全一致，页面文件无需改动。
 */
import { createApi } from "@/core/api"
import { extractDetail, type RequestFn } from "@/core/http"
import {
  clearAccountId as coreClearAccountId,
  clearSession as coreClearSession,
  loadAccountId as coreLoadAccountId,
  loadSession as coreLoadSession,
  saveAccountId as coreSaveAccountId,
  saveSession as coreSaveSession,
  type StorageLike,
} from "@/core/storage"
import type { Session } from "@/core/types"

const webStorage: StorageLike = {
  getItem: (key) => localStorage.getItem(key),
  setItem: (key, value) => localStorage.setItem(key, value),
  removeItem: (key) => localStorage.removeItem(key),
}

const request: RequestFn = async (path, opts) => {
  // 图片上传：{file, display?} 或裸 Blob 包成 multipart（浏览器自动带 boundary）；其余照常 JSON
  const raw = opts?.body as { file?: Blob; display?: Blob } | Blob | undefined
  const isUpload =
    raw instanceof Blob || (typeof raw === "object" && raw !== null && raw.file instanceof Blob)
  let body: BodyInit | undefined
  if (isUpload) {
    const form = new FormData()
    if (raw instanceof Blob) {
      form.append("file", raw, "image.jpg")
    } else {
      const upload = raw as { file: Blob; display?: Blob }
      form.append("file", upload.file, "image.jpg")
      if (upload.display) form.append("display", upload.display, "display.jpg")
    }
    body = form
  } else if (opts?.body !== undefined) {
    body = JSON.stringify(opts.body)
  }
  const res = await fetch(path, {
    method: opts?.method ?? "GET",
    headers: isUpload ? undefined : { "Content-Type": "application/json" },
    body,
  })
  if (!res.ok) {
    let data: unknown = null
    try {
      data = await res.json()
    } catch {
      /* 非 JSON 响应体，忽略 */
    }
    throw new Error(extractDetail(res.status, data))
  }
  return res.json()
}

export const api = createApi(request)

export const loadSession = () => coreLoadSession(webStorage)
export const saveSession = (s: Session) => coreSaveSession(webStorage, s)
export const clearSession = () => coreClearSession(webStorage)
export const loadAccountId = () => coreLoadAccountId(webStorage)
export const saveAccountId = (id: string) => coreSaveAccountId(webStorage, id)
export const clearAccountId = () => coreClearAccountId(webStorage)

export type {
  AccountCircle,
  AccountCirclesResp,
  CalorieDay,
  CalorieEntry,
  CalorieItem,
  ChatMessage,
  Circle,
  Comment,
  CommonWish,
  ExerciseEquiv,
  Expense,
  Fragment,
  Goal,
  GraphEdge,
  GraphNode,
  KnowledgeItem,
  LedgerMonth,
  Nudge,
  PairGraph,
  PlanItem,
  RelatedFragment,
  Report,
  ReportMeta,
  Session,
  TodayPlan,
  Wish,
  WishPlan,
} from "@/core/types"
