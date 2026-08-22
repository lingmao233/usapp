/** API 客户端（web 组装壳）：平台无关核心在 @/core，这里注入 fetch 与 localStorage。
 *
 * 只走相对路径 /api，由 vite proxy（开发）或同源后端（生产）转发到 FastAPI。
 * 登录态：圈子 session（us.session）+ 账号信息（us_account）+ account_id（us_account_id）。
 */
import { createApi } from "@/core/api"
import { extractDetail, type RequestFn } from "@/core/http"
import {
  clearAccount as coreClearAccount,
  clearAccountId as coreClearAccountId,
  clearSession as coreClearSession,
  loadAccount as coreLoadAccount,
  loadAccountId as coreLoadAccountId,
  loadSession as coreLoadSession,
  saveAccount as coreSaveAccount,
  saveAccountId as coreSaveAccountId,
  saveSession as coreSaveSession,
  type StorageLike,
} from "@/core/storage"
import type { AccountInfo, Session } from "@/core/types"

const webStorage: StorageLike = {
  getItem: (key) => localStorage.getItem(key),
  setItem: (key, value) => localStorage.setItem(key, value),
  removeItem: (key) => localStorage.removeItem(key),
}

const request: RequestFn = async (path, opts) => {
  // 图片上传：{file, display?} 或裸 Blob 包成 multipart（浏览器自动带 boundary）；其余照常 JSON
  const raw = opts?.body as { file?: Blob; display?: Blob; vision?: Blob } | Blob | undefined
  const isUpload =
    raw instanceof Blob || (typeof raw === "object" && raw !== null && raw.file instanceof Blob)
  let body: BodyInit | undefined
  if (isUpload) {
    const form = new FormData()
    if (raw instanceof Blob) {
      form.append("file", raw, "image.jpg")
    } else {
      const upload = raw as { file: Blob; display?: Blob; vision?: Blob }
      form.append("file", upload.file, "image.jpg")
      if (upload.display) form.append("display", upload.display, "display.jpg")
      if (upload.vision) form.append("vision", upload.vision, "vision.jpg")
    }
    body = form
  } else if (opts?.body !== undefined) {
    body = JSON.stringify(opts.body)
  }
  // 设备令牌：树洞等隐私接口的 Bearer 校验（拿不到时省略头，服务端过渡期放行）
  const deviceToken = localStorage.getItem("us_device_token")
  const res = await fetch(path, {
    method: opts?.method ?? "GET",
    headers: isUpload
      ? (deviceToken ? { Authorization: `Bearer ${deviceToken}` } : undefined)
      : {
          "Content-Type": "application/json",
          ...(deviceToken ? { Authorization: `Bearer ${deviceToken}` } : {}),
        },
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

/** 服务端签发设备令牌后统一落盘（auth 登录/注册/找回、建圈/入圈响应都带 device_token） */
export function saveDeviceToken(token: string | undefined | null) {
  if (token) localStorage.setItem("us_device_token", token)
}

/** 登出时清设备令牌（重新登录会签发新的；过渡期无令牌请求由服务端放行） */
export function clearDeviceToken() {
  localStorage.removeItem("us_device_token")
}

/** 树洞流式对话（web 专用，不走 createApi 工厂）：SSE 逐段回调 onDelta，
 * done 事件带最终整包（reply 为权威全文，调用方以此对齐展示与落库口径）。
 * 任何阶段失败抛 Error--调用方可回退到整包 treeholeChat。 */
export async function treeholeChatStream(
  account_id: string,
  message: string,
  image_url: string | undefined,
  onDelta: (text: string) => void,
): Promise<{ reply: string; citations: unknown[]; tools_used: string[] }> {
  const deviceToken = localStorage.getItem("us_device_token")
  const res = await fetch("/api/treehole/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(deviceToken ? { Authorization: `Bearer ${deviceToken}` } : {}),
    },
    body: JSON.stringify({ account_id, message, image_url: image_url || undefined }),
  })
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ""
  let final: { reply: string; citations: unknown[]; tools_used: string[] } | null = null
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const events = buf.split("\n\n")
    buf = events.pop() ?? ""
    for (const ev of events) {
      const line = ev.split("\n").find((l) => l.startsWith("data:"))
      if (!line) continue
      let data: { type?: string; text?: string; result?: typeof final; error?: string }
      try {
        data = JSON.parse(line.slice(5).trim())
      } catch {
        continue // 半截行，等下一段
      }
      if (data.type === "delta" && data.text) onDelta(data.text)
      else if (data.type === "done" && data.result) final = data.result
      else if (data.type === "error") throw new Error(data.error || "树洞暂时不在")
    }
  }
  if (!final) throw new Error("流式响应中断")
  return final
}

export const loadSession = () => coreLoadSession(webStorage)
export const saveSession = (s: Session) => coreSaveSession(webStorage, s)
export const clearSession = () => coreClearSession(webStorage)
export const loadAccountId = () => coreLoadAccountId(webStorage)
export const saveAccountId = (id: string) => coreSaveAccountId(webStorage, id)
export const clearAccountId = () => coreClearAccountId(webStorage)
export const loadAccount = () => coreLoadAccount(webStorage)
export const saveAccount = (a: AccountInfo) => coreSaveAccount(webStorage, a)
export const clearAccount = () => coreClearAccount(webStorage)

export type {
  AccountCircle,
  AccountCirclesResp,
  AccountInfo,
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
  FriendGoal,
  FriendMember,
  FriendPlan,
  FriendTasksResp,
  Goal,
  GraphEdge,
  GraphNode,
  KnowledgeItem,
  LedgerMonth,
  Nudge,
  PairGraph,
  PlanItem,
  PlanNudge,
  RelatedFragment,
  Report,
  ReportMeta,
  Session,
  SharingCategory,
  SharingItem,
  StagingRow,
  TodayPlan,
  TreeholeChatResp,
  TreeholeCitation,
  TreeholeMessage,
  TreeholePersona,
  Wish,
  WishPlan,
} from "@/core/types"
