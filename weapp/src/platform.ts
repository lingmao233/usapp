/**
 * 小程序平台层：注入 Taro 实现的 storage / request，复用 @core（与 web 端同一套
 * 类型、API 路径、错误语义、storage key）。页面只从这里拿 api 与 storage 函数。
 *
 * 后续接入微信登录（openid ↔ account 一对多绑定）时，在此追加：
 *   Taro.login → code → POST /api/auth/wechat → 绑定/创建 account
 * 页面无感知；网页端老用户在小程序用「恢复码找回身份」（onboarding 已有）认领同一 account。
 */
import Taro from '@tarojs/taro'
import { createApi } from '@core/api'
import { extractDetail, type RequestFn } from '@core/http'
import {
  clearAccountId as coreClearAccountId,
  clearSession as coreClearSession,
  loadAccountId as coreLoadAccountId,
  loadSession as coreLoadSession,
  saveAccountId as coreSaveAccountId,
  saveSession as coreSaveSession,
  type StorageLike,
} from '@core/storage'
import type { Session } from '@core/types'
import { API_BASE } from './config'

const taroStorage: StorageLike = {
  getItem: (key) => {
    const v = Taro.getStorageSync(key)
    return v === '' || v === undefined || v === null ? null : String(v)
  },
  setItem: (key, value) => Taro.setStorageSync(key, value),
  removeItem: (key) => Taro.removeStorageSync(key),
}

const request: RequestFn = async (path, opts) => {
  const res = await Taro.request({
    url: `${API_BASE}${path}`,
    method: opts?.method ?? 'GET',
    data: (opts?.body ?? undefined) as Record<string, unknown> | undefined,
    header: { 'Content-Type': 'application/json' },
  })
  if (res.statusCode < 200 || res.statusCode >= 300) {
    throw new Error(extractDetail(res.statusCode, res.data))
  }
  return res.data
}

export const api = createApi(request)

export const loadSession = () => coreLoadSession(taroStorage)
export const saveSession = (s: Session) => coreSaveSession(taroStorage, s)
export const clearSession = () => coreClearSession(taroStorage)
export const loadAccountId = () => coreLoadAccountId(taroStorage)
export const saveAccountId = (id: string) => coreSaveAccountId(taroStorage, id)
export const clearAccountId = () => coreClearAccountId(taroStorage)

/** 复制文本到剪贴板（小程序统一入口，替代 navigator.clipboard） */
export async function copyText(text: string): Promise<boolean> {
  try {
    await Taro.setClipboardData({ data: text })
    return true
  } catch {
    return false
  }
}

export type {
  AccountCircle,
  AccountCirclesResp,
  CommonWish,
  Fragment,
  KnowledgeItem,
  RelatedFragment,
  Report,
  ReportMeta,
  Session,
  Wish,
  WishPlan,
} from '@core/types'
