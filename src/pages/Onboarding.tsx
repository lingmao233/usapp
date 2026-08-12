import { useEffect, useState } from "react"
import {
  api,
  loadAccountId,
  saveAccountId,
  saveSession,
  clearAccountId,
  type AccountCircle,
  type Session,
} from "@/lib/api"
import CodeCustomizer from "@/components/CodeCustomizer"
import { DEFAULT_PERSONA_KEY, PERSONA_PRESETS } from "@/lib/persona"

function activeLabel(c: AccountCircle): string {
  if (c.fragment_count === 0) return "还没有碎片，等你来丢第一条"
  if (!c.last_active) return `${c.fragment_count} 条碎片`
  const diff = Date.now() - new Date(c.last_active).getTime()
  if (diff < 3_600_000) return "刚刚有人丢碎片"
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前活跃`
  return `${Math.floor(diff / 86_400_000)} 天前活跃`
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    const ta = document.createElement("textarea")
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand("copy")
    document.body.removeChild(ta)
    return ok
  }
}

/** 恢复码醒目展示卡：新建身份后必看，支持当场自定义 */
function RecoveryCard({ code, onContinue }: { code: string; onContinue: () => void }) {
  const [currentCode, setCurrentCode] = useState(code)
  const [copied, setCopied] = useState(false)
  const accountId = loadAccountId()
  return (
    <div className="us-rise us-panel rounded-2xl p-6 text-center">
      <p className="us-serif text-xl mb-1">你的身份恢复码</p>
      <p className="text-xs text-stone-500 mb-5">
        换个设备就靠它找回你的圈子，截图存好
      </p>
      <p className="us-serif text-3xl sm:text-4xl tracking-[0.35em] text-[#264653] mb-5 select-all">
        {currentCode}
      </p>
      <div className="flex justify-center gap-3">
        <button
          className="us-btn"
          onClick={async () => setCopied(await copyText(currentCode))}
        >
          {copied ? "已复制 ✓" : "复制恢复码"}
        </button>
        <button className="us-btn-ghost border border-[#264653]/20" onClick={onContinue}>
          存好了，进圈子 →
        </button>
      </div>
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
    </div>
  )
}

/** 建圈成功卡：邀请码常驻展示，复制或确认后才进圈（不再自动跳转） */
function InviteCard({ code, onContinue }: { code: string; onContinue: () => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="us-rise us-panel rounded-2xl p-6 text-center">
      <p className="us-serif text-xl mb-1">圈子建好啦</p>
      <p className="text-xs text-stone-500 mb-5">邀请码发给朋友，他们凭码加入</p>
      <p className="us-serif text-3xl sm:text-4xl tracking-[0.35em] text-[#264653] mb-5 select-all">
        {code}
      </p>
      <div className="flex justify-center gap-3">
        <button className="us-btn" onClick={async () => setCopied(await copyText(code))}>
          {copied ? "已复制 ✓" : "复制邀请码"}
        </button>
        <button className="us-btn-ghost border border-[#264653]/20" onClick={onContinue}>
          进圈子 →
        </button>
      </div>
    </div>
  )
}

export default function Onboarding({
  onDone,
  onCancel,
}: {
  onDone: (s: Session) => void
  onCancel?: () => void
}) {
  const [accountId, setAccountId] = useState<string | null>(() => loadAccountId())
  const [accountNickname, setAccountNickname] = useState("")
  const [myCircles, setMyCircles] = useState<AccountCircle[] | null>(null)

  const [mode, setMode] = useState<"choose" | "create" | "join" | "claim">("choose")
  const [circleName, setCircleName] = useState("")
  const [inviteCode, setInviteCode] = useState("")
  const [nickname, setNickname] = useState("")
  const [recoveryInput, setRecoveryInput] = useState("")
  const [error, setError] = useState("")
  const [nickError, setNickError] = useState("")
  const [busy, setBusy] = useState(false)
  // 圈子人格：五个预设 chips + 可展开的自定义输入
  const [personaPreset, setPersonaPreset] = useState(DEFAULT_PERSONA_KEY)
  const [personaCustomOpen, setPersonaCustomOpen] = useState(false)
  const [personaCustom, setPersonaCustom] = useState("")
  const [createdCode, setCreatedCode] = useState<string | null>(null)
  const [createdSession, setCreatedSession] = useState<Session | null>(null)
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

  function enterCircle(c: AccountCircle) {
    const session: Session = {
      circle_id: c.circle_id,
      user_id: c.user_id,
      nickname: c.my_nickname,
      circle_name: c.circle_name,
      invite_code: c.invite_code,
    }
    saveSession(session)
    onDone(session)
  }

  /** 新身份先亮恢复码，否则直接进 */
  function afterAuth(recoveryCode: string | null | undefined, session: Session) {
    if (recoveryCode) {
      setRecoveryShow({ code: recoveryCode, session })
    } else {
      onDone(session)
    }
  }

  function handle409(e: unknown): boolean {
    if (e instanceof Error && e.message.includes("已经有人在用")) {
      setNickError(e.message)
      return true
    }
    return false
  }

  async function handleCreate() {
    if (!circleName.trim()) {
      setError("给圈子起个名字吧")
      return
    }
    setBusy(true)
    setError("")
    setNickError("")
    try {
      const custom = personaCustomOpen ? personaCustom.trim() : ""
      const circle = await api.createCircle(circleName.trim(), accountId, nickname.trim() || undefined, {
        persona_preset: personaPreset,
        persona_custom: custom,
      })
      if (circle.account_id) saveAccountId(circle.account_id)
      const session: Session = {
        circle_id: circle.id,
        user_id: circle.user_id,
        nickname: circle.nickname,
        circle_name: circle.name,
        invite_code: circle.invite_code,
      }
      saveSession(session)
      // 邀请码常驻成功卡，用户确认后才进圈（不再 1.2s 自动跳转）
      setCreatedSession(session)
      setCreatedCode(circle.invite_code)
      if (circle.recovery_code) {
        // 新身份：先看恢复码，再看邀请码
        setRecoveryShow({ code: circle.recovery_code, session })
      }
    } catch (e) {
      if (!handle409(e)) setError(e instanceof Error ? e.message : "出了点问题，再试一次")
    } finally {
      setBusy(false)
    }
  }

  async function handleJoin() {
    if (!inviteCode.trim()) {
      setError("填一下邀请码")
      return
    }
    setBusy(true)
    setError("")
    setNickError("")
    try {
      const res = await api.joinCircle(inviteCode.trim(), nickname.trim() || undefined, accountId)
      if (res.account_id) saveAccountId(res.account_id)
      const session: Session = {
        circle_id: res.circle_id,
        user_id: res.user_id,
        nickname: res.nickname,
        circle_name: res.circle_name,
        invite_code: res.invite_code,
      }
      saveSession(session)
      afterAuth(res.recovery_code, session)
    } catch (e) {
      if (!handle409(e)) setError(e instanceof Error ? e.message : "出了点问题，再试一次")
    } finally {
      setBusy(false)
    }
  }

  async function handleClaim() {
    if (!recoveryInput.trim()) {
      setError("填一下恢复码")
      return
    }
    setBusy(true)
    setError("")
    try {
      const res = await api.claimAccount(recoveryInput.trim())
      saveAccountId(res.account_id)
      setAccountId(res.account_id)
      setMode("choose")
      setRecoveryInput("")
    } catch (e) {
      setError(e instanceof Error ? e.message : "出了点问题，再试一次")
    } finally {
      setBusy(false)
    }
  }

  const hasCircles = accountId !== null && (myCircles?.length ?? 0) > 0

  const nicknameField = (
    <div>
      <label className="text-xs text-stone-500">你的昵称</label>
      <input
        className="us-input"
        placeholder="朋友们怎么叫你"
        value={nickname}
        onChange={(e) => {
          setNickname(e.target.value)
          setNickError("")
        }}
      />
      {nickError && <p className="text-xs text-red-700 mt-1.5">{nickError}</p>}
    </div>
  )

  // 人格选择：五个预设 chips + 展开"自定义"输入框（自定义非空时优先于预设）
  const personaField = (
    <div>
      <label className="text-xs text-stone-500">圈子人格（周报的口吻）</label>
      <div className="flex flex-wrap gap-2 mt-1.5">
        {PERSONA_PRESETS.map((p) => (
          <button
            key={p.key}
            type="button"
            title={p.desc}
            onClick={() => setPersonaPreset(p.key)}
            className={`rounded-full px-3.5 py-1.5 text-sm transition-colors duration-200 ${
              personaPreset === p.key && !personaCustomOpen
                ? "bg-[#161616] text-white"
                : "text-[#264653] border border-[#264653]/20 hover:bg-[#264653]/8"
            }`}
          >
            {p.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setPersonaCustomOpen((v) => !v)}
          className={`rounded-full px-3.5 py-1.5 text-sm transition-colors duration-200 ${
            personaCustomOpen
              ? "bg-[#161616] text-white"
              : "text-[#264653] border border-dashed border-[#264653]/20 hover:bg-[#264653]/8"
          }`}
        >
          自定义…
        </button>
      </div>
      {personaCustomOpen && (
        <input
          className="us-input mt-2"
          placeholder="比如：像佟掌柜一样，精明又热乎"
          value={personaCustom}
          onChange={(e) => setPersonaCustom(e.target.value)}
        />
      )}
    </div>
  )

  // ---------- 创建/加入表单 ----------
  const createForm = (
    <div className="flex flex-col gap-5">
      <div>
        <label className="text-xs text-stone-500">圈子名字</label>
        <input
          className="us-input"
          placeholder="比如：周末小队"
          value={circleName}
          onChange={(e) => setCircleName(e.target.value)}
        />
      </div>
      {nicknameField}
      {personaField}
      <button className="us-btn" disabled={busy} onClick={handleCreate}>
        {busy ? "创建中…" : "创建圈子"}
      </button>
    </div>
  )

  const joinForm = (
    <div className="flex flex-col gap-5">
      <div>
        <label className="text-xs text-stone-500">邀请码</label>
        <input
          className="us-input tracking-[0.3em]"
          placeholder="6 位邀请码"
          value={inviteCode}
          onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
          maxLength={6}
        />
      </div>
      <div>
        <label className="text-xs text-stone-500">在这个圈子里的昵称（可和别处不同）</label>
        <input
          className="us-input"
          placeholder="朋友们怎么叫你"
          value={nickname}
          onChange={(e) => {
            setNickname(e.target.value)
            setNickError("")
          }}
        />
        {nickError && <p className="text-xs text-red-700 mt-1.5">{nickError}</p>}
      </div>
      <button className="us-btn" disabled={busy} onClick={handleJoin}>
        {busy ? "加入中…" : "加入圈子"}
      </button>
    </div>
  )

  // ---------- 新身份：先展示恢复码 ----------
  if (recoveryShow) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="w-full max-w-md">
          <RecoveryCard
            code={recoveryShow.code}
            onContinue={() => {
              const s = recoveryShow.session
              setRecoveryShow(null)
              // 建圈流程：恢复码之后落到邀请码卡；加入流程直接进圈
              if (!createdCode) onDone(s)
            }}
          />
        </div>
      </div>
    )
  }

  // ---------- 建圈成功：邀请码常驻，确认后才进圈 ----------
  if (createdCode && createdSession) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="w-full max-w-md">
          <InviteCard code={createdCode} onContinue={() => onDone(createdSession)} />
        </div>
      </div>
    )
  }

  // ---------- 有圈子：列表为主视觉 ----------
  if (hasCircles) {
    return (
      <div className="min-h-screen px-6 py-12">
        <div className="max-w-2xl mx-auto us-rise">
          <h1 className="us-serif text-4xl mb-2">我们</h1>
          <p className="text-stone-500 mb-10">
            {accountNickname}，欢迎回来。去哪个圈子坐坐？
          </p>

          {onCancel && (
            <button
              className="us-btn-ghost text-sm mb-6 border border-[#264653]/20"
              onClick={onCancel}
            >
              ← 返回当前圈子
            </button>
          )}

          <div className="flex flex-col gap-4 mb-12">
            {myCircles!.map((c, i) => (
              <button
                key={c.circle_id}
                onClick={() => enterCircle(c)}
                className="us-rise text-left bg-white/60 rounded-2xl px-6 py-5 transition-all duration-200 hover:bg-white/90 hover:translate-y-[-1px]"
                style={{ animationDelay: `${i * 80}ms` }}
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="us-serif text-xl">{c.circle_name}</span>
                  <span className="text-xs text-stone-400 shrink-0">
                    邀请码 {c.invite_code} · {c.member_count} 位成员
                  </span>
                </div>
                <div className="flex items-baseline justify-between gap-3 mt-2">
                  <span className="text-sm text-stone-500">
                    我在圈里叫「{c.my_nickname}」
                  </span>
                  <span className="text-xs text-[#264653]/70 shrink-0">{activeLabel(c)}</span>
                </div>
              </button>
            ))}
          </div>

          <div className="grid sm:grid-cols-2 gap-8">
            <section>
              <h3 className="us-serif text-lg mb-4 pb-2 border-b border-[#264653]/10">
                建一个新圈子
              </h3>
              {createForm}
            </section>
            <section>
              <h3 className="us-serif text-lg mb-4 pb-2 border-b border-[#264653]/10">
                加入新圈子
              </h3>
              {joinForm}
            </section>
          </div>
          {error && <p className="text-sm text-red-700 mt-4">{error}</p>}
        </div>
      </div>
    )
  }

  // ---------- 首次访问：创建 / 加入 / 找回身份 ----------
  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="w-full max-w-md us-rise">
        <h1 className="us-serif text-5xl mb-3">我们</h1>
        <p className="text-stone-500 mb-10 leading-relaxed">
          各自随手丢碎片，AI 帮你们发现没意识到的连接。
          <br />
          3-10 个人的小圈子，刚好。
        </p>

        {mode === "choose" && (
          <div className="flex flex-col gap-3">
            <button className="us-btn w-full py-3" onClick={() => setMode("create")}>
              建一个新圈子
            </button>
            <button
              className="us-btn-ghost w-full py-3 border border-[#264653]/20"
              onClick={() => setMode("join")}
            >
              我有邀请码，加入圈子
            </button>
            <button
              className="text-sm text-stone-400 hover:text-[#264653] transition-colors mt-3"
              onClick={() => setMode("claim")}
            >
              换了设备？用恢复码找回身份
            </button>
          </div>
        )}

        {mode === "create" && (
          <div className="flex flex-col gap-6">
            {createForm}
            {error && <p className="text-sm text-red-700">{error}</p>}
            <button className="us-btn-ghost self-start" onClick={() => setMode("choose")}>
              返回
            </button>
          </div>
        )}

        {mode === "join" && (
          <div className="flex flex-col gap-6">
            {joinForm}
            {error && <p className="text-sm text-red-700">{error}</p>}
            <button className="us-btn-ghost self-start" onClick={() => setMode("choose")}>
              返回
            </button>
          </div>
        )}

        {mode === "claim" && (
          <div className="flex flex-col gap-6">
            <div>
              <label className="text-xs text-stone-500">身份恢复码</label>
              <input
                className="us-input tracking-[0.3em]"
                placeholder="6 位恢复码"
                value={recoveryInput}
                onChange={(e) => setRecoveryInput(e.target.value.toUpperCase())}
                maxLength={6}
              />
              <p className="text-xs text-stone-400 mt-2">
                第一次创建身份时展示过的那串字符
              </p>
            </div>
            {error && <p className="text-sm text-red-700">{error}</p>}
            <div className="flex gap-3">
              <button className="us-btn flex-1" disabled={busy} onClick={handleClaim}>
                {busy ? "找回中…" : "找回我的圈子"}
              </button>
              <button className="us-btn-ghost" onClick={() => setMode("choose")}>
                返回
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
