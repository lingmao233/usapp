/** 平台无关的本地存储抽象：web 注入 localStorage，小程序注入 Taro storage。
 *
 * key 名保持 us.session / us_account_id 不变，存量 web 用户不掉登录。
 * 账号系统重构新增 us_account（登录态账号信息 JSON）。
 */
import type { AccountInfo, Session } from "./types"

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

const SESSION_KEY = "us.session"
const ACCOUNT_KEY = "us_account_id"

export function loadSession(st: StorageLike): Session | null {
  try {
    const raw = st.getItem(SESSION_KEY)
    return raw ? (JSON.parse(raw) as Session) : null
  } catch {
    return null
  }
}

export function saveSession(st: StorageLike, s: Session) {
  st.setItem(SESSION_KEY, JSON.stringify(s))
}

export function clearSession(st: StorageLike) {
  st.removeItem(SESSION_KEY)
}

export function loadAccountId(st: StorageLike): string | null {
  return st.getItem(ACCOUNT_KEY)
}

export function saveAccountId(st: StorageLike, id: string) {
  st.setItem(ACCOUNT_KEY, id)
}

export function clearAccountId(st: StorageLike) {
  st.removeItem(ACCOUNT_KEY)
}

const ACCOUNT_INFO_KEY = "us_account"

const DEVICE_TOKEN_KEY = "us_device_token"

/** 设备令牌（服务端签发，树洞等隐私接口做 Bearer 校验；拿不到时旧会话仍可用--过渡期） */
export function loadDeviceToken(st: StorageLike): string | null {
  return st.getItem(DEVICE_TOKEN_KEY)
}

export function saveDeviceToken(st: StorageLike, token: string | undefined | null) {
  if (token) st.setItem(DEVICE_TOKEN_KEY, token)
}

export function clearDeviceToken(st: StorageLike) {
  st.removeItem(DEVICE_TOKEN_KEY)
}

/** 登录态账号信息（auth 响应去掉一次性 recovery_code 后落盘） */
export function loadAccount(st: StorageLike): AccountInfo | null {
  try {
    const raw = st.getItem(ACCOUNT_INFO_KEY)
    return raw ? (JSON.parse(raw) as AccountInfo) : null
  } catch {
    return null
  }
}

export function saveAccount(st: StorageLike, a: AccountInfo) {
  st.setItem(ACCOUNT_INFO_KEY, JSON.stringify(a))
}

export function clearAccount(st: StorageLike) {
  st.removeItem(ACCOUNT_INFO_KEY)
}
