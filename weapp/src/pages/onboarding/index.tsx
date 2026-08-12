import { useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { Input, Text, View } from '@tarojs/components'
import {
  api,
  clearAccountId,
  copyText,
  loadAccountId,
  saveAccountId,
  saveSession,
  type AccountCircle,
  type Session,
} from '@/platform'
import CodeCustomizer from '@/components/CodeCustomizer'

function activeLabel(c: AccountCircle): string {
  if (c.fragment_count === 0) return '还没有碎片，等你来丢第一条'
  if (!c.last_active) return `${c.fragment_count} 条碎片`
  const diff = Date.now() - new Date(c.last_active).getTime()
  if (diff < 3_600_000) return '刚刚有人丢碎片'
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前活跃`
  return `${Math.floor(diff / 86_400_000)} 天前活跃`
}

/** 恢复码醒目展示卡：新建身份后必看，支持当场自定义 */
function RecoveryCard({ code, onContinue }: { code: string; onContinue: () => void }) {
  const [currentCode, setCurrentCode] = useState(code)
  const [copied, setCopied] = useState(false)
  const accountId = loadAccountId()
  return (
    <View className="us-rise us-panel rounded-2xl p-6 text-center">
      <Text className="us-serif text-xl mb-1 block">你的身份恢复码</Text>
      <Text className="text-xs text-stone-500 mb-5 block">换个设备就靠它找回你的圈子，截图存好</Text>
      <Text className="us-serif text-3xl tracking-[0.35em] text-[#264653] mb-5 block">
        {currentCode}
      </Text>
      <View className="flex justify-center gap-3">
        <View
          className="us-btn"
          onClick={async () => setCopied(await copyText(currentCode))}
        >
          {copied ? '已复制 ✓' : '复制恢复码'}
        </View>
        <View className="us-btn-ghost border border-[#264653]/20" onClick={onContinue}>
          存好了，进圈子 →
        </View>
      </View>
      {accountId && (
        <CodeCustomizer
          accountId={accountId}
          currentCode={currentCode}
          onChanged={(c) => {
            setCurrentCode(c)
            setCopied(false)
          }}
        />
      )}
    </View>
  )
}

export default function Onboarding() {
  const [accountId, setAccountId] = useState<string | null>(() => loadAccountId())
  const [accountNickname, setAccountNickname] = useState('')
  const [myCircles, setMyCircles] = useState<AccountCircle[] | null>(null)

  const [mode, setMode] = useState<'choose' | 'create' | 'join' | 'claim'>('choose')
  const [circleName, setCircleName] = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [nickname, setNickname] = useState('')
  const [recoveryInput, setRecoveryInput] = useState('')
  const [error, setError] = useState('')
  const [nickError, setNickError] = useState('')
  const [busy, setBusy] = useState(false)
  const [createdCode, setCreatedCode] = useState<string | null>(null)
  const [recoveryShow, setRecoveryShow] = useState<{ code: string; session: Session } | null>(null)

  // 有身份就拉"我的圈子"列表
  useEffect(() => {
    if (!accountId) {
      setMyCircles([])
      return
    }
    api
      .accountCircles(accountId)
      .then((res) => {
        setMyCircles(res.circles)
        setAccountNickname(res.account_nickname)
        setNickname((n) => n || res.account_nickname)
      })
      .catch(() => {
        clearAccountId()
        setAccountId(null)
        setMyCircles([])
      })
  }, [accountId])

  /** 进入主页面（小程序用 reLaunch 切换，对应 web 的 onDone(session)） */
  function enterApp(session: Session) {
    saveSession(session)
    Taro.reLaunch({ url: '/pages/index/index' })
  }

  function enterCircle(c: AccountCircle) {
    enterApp({
      circle_id: c.circle_id,
      user_id: c.user_id,
      nickname: c.my_nickname,
      circle_name: c.circle_name,
      invite_code: c.invite_code,
    })
  }

  /** 新身份先亮恢复码，否则直接进 */
  function afterAuth(recoveryCode: string | null | undefined, session: Session) {
    if (recoveryCode) {
      setRecoveryShow({ code: recoveryCode, session })
    } else {
      enterApp(session)
    }
  }

  function handle409(e: unknown): boolean {
    if (e instanceof Error && e.message.includes('已经有人在用')) {
      setNickError(e.message)
      return true
    }
    return false
  }

  async function handleCreate() {
    if (!circleName.trim()) {
      setError('给圈子起个名字吧')
      return
    }
    setBusy(true)
    setError('')
    setNickError('')
    try {
      const circle = await api.createCircle(circleName.trim(), accountId, nickname.trim() || undefined)
      if (circle.account_id) saveAccountId(circle.account_id)
      const session: Session = {
        circle_id: circle.id,
        user_id: circle.user_id,
        nickname: circle.nickname,
        circle_name: circle.name,
        invite_code: circle.invite_code,
      }
      if (circle.recovery_code) {
        setRecoveryShow({ code: circle.recovery_code, session })
      } else {
        setCreatedCode(circle.invite_code)
        setTimeout(() => enterApp(session), 1200)
      }
    } catch (e) {
      if (!handle409(e)) setError(e instanceof Error ? e.message : '出了点问题，再试一次')
    } finally {
      setBusy(false)
    }
  }

  async function handleJoin() {
    if (!inviteCode.trim()) {
      setError('填一下邀请码')
      return
    }
    setBusy(true)
    setError('')
    setNickError('')
    try {
      const res = await api.joinCircle(inviteCode.trim(), nickname.trim() || undefined, accountId)
      if (res.account_id) saveAccountId(res.account_id)
      afterAuth(res.recovery_code, {
        circle_id: res.circle_id,
        user_id: res.user_id,
        nickname: res.nickname,
        circle_name: res.circle_name,
        invite_code: res.invite_code,
      })
    } catch (e) {
      if (!handle409(e)) setError(e instanceof Error ? e.message : '出了点问题，再试一次')
    } finally {
      setBusy(false)
    }
  }

  /** 恢复码认领：网页端老用户在小程序输入恢复码，找回同一 account（双端打通入口） */
  async function handleClaim() {
    if (!recoveryInput.trim()) {
      setError('填一下恢复码')
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await api.claimAccount(recoveryInput.trim())
      saveAccountId(res.account_id)
      setAccountId(res.account_id)
      setMode('choose')
      setRecoveryInput('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '出了点问题，再试一次')
    } finally {
      setBusy(false)
    }
  }

  const hasCircles = accountId !== null && (myCircles?.length ?? 0) > 0

  const nicknameField = (
    <View>
      <Text className="text-xs text-stone-500 block">你的昵称</Text>
      <Input
        className="us-input"
        placeholder="朋友们怎么叫你"
        placeholderClass="us-input-ph"
        value={nickname}
        onInput={(e) => {
          setNickname(e.detail.value)
          setNickError('')
        }}
      />
      {nickError && <Text className="text-xs text-red-700 mt-1.5 block">{nickError}</Text>}
    </View>
  )

  // ---------- 创建/加入表单 ----------
  const createForm = createdCode ? (
    <View className="us-related p-5 text-center">
      <Text className="text-sm text-[#264653] mb-1 block">圈子建好啦，邀请码是</Text>
      <Text className="us-serif text-3xl tracking-[0.3em] block">{createdCode}</Text>
      <Text className="text-xs text-stone-500 mt-2 block">发给朋友，他们凭码加入</Text>
    </View>
  ) : (
    <View className="flex flex-col gap-5">
      <View>
        <Text className="text-xs text-stone-500 block">圈子名字</Text>
        <Input
          className="us-input"
          placeholder="比如：周末小队"
          placeholderClass="us-input-ph"
          value={circleName}
          onInput={(e) => setCircleName(e.detail.value)}
        />
      </View>
      {nicknameField}
      <View className={`us-btn ${busy ? 'us-btn-disabled' : ''}`} onClick={busy ? undefined : handleCreate}>
        {busy ? '创建中…' : '创建圈子'}
      </View>
    </View>
  )

  const joinForm = (
    <View className="flex flex-col gap-5">
      <View>
        <Text className="text-xs text-stone-500 block">邀请码</Text>
        <Input
          className="us-input tracking-[0.3em]"
          placeholder="6 位邀请码"
          placeholderClass="us-input-ph"
          value={inviteCode}
          onInput={(e) => setInviteCode(e.detail.value.toUpperCase())}
          maxlength={6}
        />
      </View>
      <View>
        <Text className="text-xs text-stone-500 block">在这个圈子里的昵称（可和别处不同）</Text>
        <Input
          className="us-input"
          placeholder="朋友们怎么叫你"
          placeholderClass="us-input-ph"
          value={nickname}
          onInput={(e) => {
            setNickname(e.detail.value)
            setNickError('')
          }}
        />
        {nickError && <Text className="text-xs text-red-700 mt-1.5 block">{nickError}</Text>}
      </View>
      <View className={`us-btn ${busy ? 'us-btn-disabled' : ''}`} onClick={busy ? undefined : handleJoin}>
        {busy ? '加入中…' : '加入圈子'}
      </View>
    </View>
  )

  // ---------- 新身份：先展示恢复码 ----------
  if (recoveryShow) {
    return (
      <View className="min-h-screen flex items-center justify-center px-6">
        <View className="w-full max-w-md">
          <RecoveryCard code={recoveryShow.code} onContinue={() => enterApp(recoveryShow.session)} />
        </View>
      </View>
    )
  }

  // ---------- 有圈子：列表为主视觉 ----------
  if (hasCircles) {
    return (
      <View className="min-h-screen px-6 py-12">
        <View className="max-w-2xl mx-auto us-rise">
          <Text className="us-serif text-4xl mb-2 block">我们</Text>
          <Text className="text-stone-500 mb-10 block">
            {accountNickname}，欢迎回来。去哪个圈子坐坐？
          </Text>

          <View className="flex flex-col gap-4 mb-12">
            {myCircles!.map((c, i) => (
              <View
                key={c.circle_id}
                onClick={() => enterCircle(c)}
                className="us-rise text-left bg-white/60 rounded-2xl px-6 py-5 transition-all duration-200"
                style={{ animationDelay: `${i * 80}ms` }}
              >
                <View className="flex items-baseline justify-between gap-3">
                  <Text className="us-serif text-xl">{c.circle_name}</Text>
                  <Text className="text-xs text-stone-400 shrink-0">{c.member_count} 位成员</Text>
                </View>
                <View className="flex items-baseline justify-between gap-3 mt-2">
                  <Text className="text-sm text-stone-500">我在圈里叫「{c.my_nickname}」</Text>
                  <Text className="text-xs text-[#264653]/70 shrink-0">{activeLabel(c)}</Text>
                </View>
              </View>
            ))}
          </View>

          <View className="flex flex-col gap-8">
            <View>
              <Text className="us-serif text-lg mb-4 pb-2 border-b border-[#264653]/10 block">
                建一个新圈子
              </Text>
              {createForm}
            </View>
            <View>
              <Text className="us-serif text-lg mb-4 pb-2 border-b border-[#264653]/10 block">
                加入新圈子
              </Text>
              {joinForm}
            </View>
          </View>
          {error && <Text className="text-sm text-red-700 mt-4 block">{error}</Text>}
        </View>
      </View>
    )
  }

  // ---------- 首次访问：创建 / 加入 / 找回身份 ----------
  return (
    <View className="min-h-screen flex items-center justify-center px-6">
      <View className="w-full max-w-md us-rise">
        <Text className="us-serif text-5xl mb-3 block">我们</Text>
        <Text className="text-stone-500 mb-10 leading-relaxed block">
          各自随手丢碎片，AI 帮你们发现没意识到的连接。
          {'\n'}
          3-10 个人的小圈子，刚好。
        </Text>

        {mode === 'choose' && (
          <View className="flex flex-col gap-3">
            <View className="us-btn w-full py-3" onClick={() => setMode('create')}>
              建一个新圈子
            </View>
            <View
              className="us-btn-ghost w-full py-3 border border-[#264653]/20"
              onClick={() => setMode('join')}
            >
              我有邀请码，加入圈子
            </View>
            <View
              className="text-sm text-stone-400 mt-3 text-center"
              onClick={() => setMode('claim')}
            >
              换了设备？用恢复码找回身份
            </View>
          </View>
        )}

        {mode === 'create' && (
          <View className="flex flex-col gap-6">
            {createForm}
            {error && <Text className="text-sm text-red-700 block">{error}</Text>}
            {!createdCode && (
              <View className="us-btn-ghost self-start" onClick={() => setMode('choose')}>
                返回
              </View>
            )}
          </View>
        )}

        {mode === 'join' && (
          <View className="flex flex-col gap-6">
            {joinForm}
            {error && <Text className="text-sm text-red-700 block">{error}</Text>}
            <View className="us-btn-ghost self-start" onClick={() => setMode('choose')}>
              返回
            </View>
          </View>
        )}

        {mode === 'claim' && (
          <View className="flex flex-col gap-6">
            <View>
              <Text className="text-xs text-stone-500 block">身份恢复码</Text>
              <Input
                className="us-input tracking-[0.3em]"
                placeholder="6 位恢复码"
                placeholderClass="us-input-ph"
                value={recoveryInput}
                onInput={(e) => setRecoveryInput(e.detail.value.toUpperCase())}
                maxlength={6}
              />
              <Text className="text-xs text-stone-400 mt-2 block">
                第一次创建身份时展示过的那串字符（网页端的身份也用它找回）
              </Text>
            </View>
            {error && <Text className="text-sm text-red-700 block">{error}</Text>}
            <View className="flex gap-3">
              <View
                className={`us-btn flex-1 ${busy ? 'us-btn-disabled' : ''}`}
                onClick={busy ? undefined : handleClaim}
              >
                {busy ? '找回中…' : '找回我的圈子'}
              </View>
              <View className="us-btn-ghost" onClick={() => setMode('choose')}>
                返回
              </View>
            </View>
          </View>
        )}
      </View>
    </View>
  )
}
