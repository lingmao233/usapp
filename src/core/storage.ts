/** 平台无关的本地存储抽象：web 注入 localStorage，小程序注入 Taro storage。
 *
 * key 名保持 us.session / us_account_id 不变，存量 web 用户不掉登录。
 */
import type { Session } from "./types"

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
