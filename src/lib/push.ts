/** Web Push（第 5 期）：权限请求 + PushManager 订阅 + 上报后端。
 *
 * iOS 需先把 PWA 安装到主屏幕才有 PushManager；不支持的环境 pushSupported() 为 false，
 * 调用方据此隐藏入口，不做任何提示打扰。
 */

// 后端下发的 VAPID 公钥（base64url）转成 PushManager 需要的 Uint8Array
function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4)
  const padded = (base64 + padding).replace(/-/g, "+").replace(/_/g, "/")
  const raw = atob(padded)
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
  return bytes
}

export function pushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window
}

/** 上报订阅到后端（endpoint 唯一，重复调用等于换绑当前用户/刷新密钥）。 */
async function reportSubscription(userId: string, sub: PushSubscription): Promise<void> {
  await fetch("/api/push/subscribe", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId, endpoint: sub.endpoint, keys: sub.toJSON().keys }),
  })
}

/** 已授权时静默把本机订阅绑到当前用户（换身份后自动换绑），失败下次启动再试。 */
export async function syncPushSubscription(userId: string): Promise<void> {
  if (!pushSupported() || Notification.permission !== "granted") return
  try {
    const reg = await navigator.serviceWorker.ready
    const sub = await reg.pushManager.getSubscription()
    if (sub) await reportSubscription(userId, sub)
  } catch {
    /* 静默失败，不打断使用 */
  }
}

/** 请求通知权限 → 订阅 → 上报；用户拒绝或任一步失败返回 false。 */
export async function enablePush(userId: string): Promise<boolean> {
  if (!pushSupported()) return false
  const permission = await Notification.requestPermission()
  if (permission !== "granted") return false
  const reg = await navigator.serviceWorker.ready
  const res = await fetch("/api/push/vapid-key")
  const { public_key } = (await res.json()) as { public_key: string }
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: urlBase64ToUint8Array(public_key),
  })
  await reportSubscription(userId, sub)
  return true
}
