import { useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { Text, View } from '@tarojs/components'
import {
  api,
  clearAccountId,
  clearSession,
  copyText,
  loadAccountId,
  loadSession,
  type Session,
} from '@/platform'
import CodeCustomizer from '@/components/CodeCustomizer'
import Wall from './Wall'
import Knowledge from './Knowledge'
import Wishes from './Wishes'

type Tab = 'wall' | 'knowledge' | 'wishes'

const TABS: { key: Tab; label: string }[] = [
  { key: 'wall', label: '碎片墙' },
  { key: 'knowledge', label: '知识库' },
  { key: 'wishes', label: '愿望清单' },
]

/** 顶栏"我的身份码"弹出层 */
function IdentityCode({ onClose }: { onClose: () => void }) {
  const [code, setCode] = useState<string | null>(null)
  const [nickname, setNickname] = useState('')
  const [copied, setCopied] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const accountId = loadAccountId()

  useEffect(() => {
    if (!accountId) {
      setLoadError(true)
      return
    }
    api
      .getAccount(accountId)
      .then((res) => {
        setCode(res.recovery_code)
        setNickname(res.nickname)
      })
      .catch(() => setLoadError(true))
  }, [accountId])

  async function copy() {
    if (!code) return
    setCopied(await copyText(code))
  }

  return (
    <View
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/20 px-6"
      onClick={onClose}
    >
      <View
        className="us-rise us-panel rounded-2xl p-6 w-full max-w-sm text-center"
        onClick={(e) => e.stopPropagation()}
      >
        <Text className="us-serif text-lg mb-1 block">
          {nickname ? `${nickname} 的` : ''}身份恢复码
        </Text>
        <Text className="text-xs text-stone-500 mb-4 block">换设备登录就靠它，别发给圈外人</Text>
        {code ? (
          <Text className="us-serif text-3xl tracking-[0.3em] text-[#264653] mb-5 block">
            {code}
          </Text>
        ) : loadError ? (
          <Text className="text-sm text-red-700 mb-5 block">
            没拿到身份码，检查网络后重开这个弹层试试
          </Text>
        ) : (
          <Text className="text-sm text-stone-400 mb-5 block">加载中…</Text>
        )}
        <View className="flex justify-center gap-3">
          <View className={`us-btn ${!code ? 'us-btn-disabled' : ''}`} onClick={code ? copy : undefined}>
            {copied ? '已复制 ✓' : '复制'}
          </View>
          <View className="us-btn-ghost" onClick={onClose}>
            关闭
          </View>
        </View>
        {code && accountId && (
          <CodeCustomizer
            accountId={accountId}
            currentCode={code}
            onChanged={(c) => {
              setCode(c)
              setCopied(false)
            }}
          />
        )}
      </View>
    </View>
  )
}

export default function Index() {
  const [session, setSession] = useState<Session | null>(() => loadSession())
  const [tab, setTab] = useState<Tab>('wall')
  const [showIdentity, setShowIdentity] = useState(false)
  const [showMenu, setShowMenu] = useState(false)

  // 无会话 → 去入圈页（对应 web 端渲染 Onboarding 的分支）
  useEffect(() => {
    if (!session) {
      Taro.reLaunch({ url: '/pages/onboarding/index' })
    }
  }, [session])

  function switchCircle() {
    clearSession()
    setSession(null)
  }

  function switchAccount() {
    clearSession()
    clearAccountId()
    setSession(null)
  }

  if (!session) {
    return <View className="min-h-screen" />
  }

  const menuItem = (label: string, onTap: () => void, key: string) => (
    <View
      key={key}
      className="py-2.5 px-3 text-sm text-[#264653]"
      onClick={() => {
        setShowMenu(false)
        onTap()
      }}
    >
      {label}
    </View>
  )

  return (
    <View className="min-h-screen">
      {/* 顶栏：非 fixed（小程序导航栏已占位），结构与 web 端一致 */}
      <View className="z-10 bg-[#F5F0E1] border-b border-[#264653]/10">
        <View className="max-w-2xl mx-auto px-5 py-3 flex items-center justify-between gap-4">
          <View className="flex items-baseline gap-3 min-w-0 flex-1">
            <Text className="text-xs text-stone-400 truncate">
              {session.circle_name} · 邀请码 {session.invite_code}
            </Text>
          </View>
          <View className="flex items-center gap-3 shrink-0">
            <View className="flex gap-1">
              {TABS.map((t) => (
                <View
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  className={`rounded-full px-3.5 py-1.5 text-sm transition-colors duration-200 ${
                    tab === t.key ? 'bg-[#161616] text-white' : 'text-[#264653]'
                  }`}
                >
                  {t.label}
                </View>
              ))}
            </View>
            {/* 更多操作（对应 web 移动端折叠菜单） */}
            <View className="relative">
              <View className="p-2 -mr-2" onClick={() => setShowMenu((v) => !v)}>
                <Text className="text-[#264653] text-lg leading-none">≡</Text>
              </View>
              {showMenu && (
                <>
                  <View className="fixed inset-0 z-30" onClick={() => setShowMenu(false)} />
                  <View className="absolute right-0 top-full mt-1 z-40 min-w-[9rem] bg-[#F9F5EB] rounded-xl border border-[#264653]/10 shadow-lg p-1">
                    <Text className="px-3 py-2 text-sm text-[#264653] font-medium block">
                      {session.nickname}
                    </Text>
                    <View className="h-px bg-[#264653]/10 mx-1" />
                    {menuItem('我的身份码', () => setShowIdentity(true), 'identity')}
                    {menuItem('切换圈子', switchCircle, 'circle')}
                    {menuItem('换个身份', switchAccount, 'account')}
                  </View>
                </>
              )}
            </View>
          </View>
        </View>
      </View>

      {/* 页面 */}
      {tab === 'wall' && <Wall session={session} />}
      {tab === 'knowledge' && <Knowledge session={session} />}
      {tab === 'wishes' && <Wishes session={session} />}

      {showIdentity && <IdentityCode onClose={() => setShowIdentity(false)} />}
    </View>
  )
}
