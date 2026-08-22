import { useState } from "react"
import {
  api,
  loadAccountId,
  saveAccount,
  saveAccountId,
  saveDeviceToken,
  type AccountInfo,
} from "@/lib/api"
import CodeCustomizer from "@/components/CodeCustomizer"
import { copyText } from "@/lib/utils"

/** 找回凭证醒目展示卡：注册/找回成功后必看，可当场自设，点「我已保存」才放行 */
function RecoveryCard({ code, onContinue }: { code: string; onContinue: () => void }) {
  const [currentCode, setCurrentCode] = useState(code)
  const [copied, setCopied] = useState(false)
  const accountId = loadAccountId()
  return (
    <div className="us-rise us-panel rounded-2xl p-6 text-center">
      <p className="us-serif text-xl mb-1">你的找回凭证</p>
      <p className="text-xs text-stone-500 mb-5">
        忘了密码就靠它重置，只此一次展示，截图或抄写存好
      </p>
      <p className="us-serif text-3xl sm:text-4xl tracking-[0.35em] text-[#264653] mb-5 select-all">
        {currentCode}
      </p>
      <div className="flex justify-center gap-3">
        <button
          className="us-btn"
          onClick={async () => setCopied(await copyText(currentCode))}
        >
          {copied ? "已复制 ✓" : "复制凭证"}
        </button>
        <button className="us-btn-ghost border border-[#264653]/20" onClick={onContinue}>
          我已保存，继续 →
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

/** 账号登录/注册入口：账号名全局唯一 + 可选密码；忘记密码走找回凭证重置 */
export default function Onboarding({ onDone }: { onDone: (a: AccountInfo) => void }) {
  const [mode, setMode] = useState<"login" | "register" | "reset">("login")
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [nickname, setNickname] = useState("")
  const [recoveryInput, setRecoveryInput] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [forced, setForced] = useState<{ code: string; account: AccountInfo } | null>(null)

  /** 登录态落盘（recovery_code 是一次性的，不持久化；device_token 是树洞等隐私接口的凭证） */
  function persist(a: AccountInfo) {
    saveDeviceToken(a.device_token)
    saveAccountId(a.account_id)
    saveAccount({
      account_id: a.account_id,
      username: a.username,
      nickname: a.nickname,
      has_password: a.has_password,
    })
  }

  /** 注册/找回响应带找回凭证：强制展示一次，确认后才进 App */
  function afterAuth(a: AccountInfo) {
    persist(a)
    if (a.recovery_code) setForced({ code: a.recovery_code, account: a })
    else onDone(a)
  }

  async function run(fn: () => Promise<void>) {
    setBusy(true)
    setError("")
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : "出了点问题，再试一次")
    } finally {
      setBusy(false)
    }
  }

  const handleLogin = () =>
    run(async () => {
      if (!username.trim()) {
        setError("填一下账号名")
        return
      }
      afterAuth(await api.login(username.trim(), password))
    })

  const handleRegister = () =>
    run(async () => {
      if (!username.trim()) {
        setError("给账号起个名字吧")
        return
      }
      afterAuth(await api.register(username.trim(), password, nickname.trim() || undefined))
    })

  const handleReset = () =>
    run(async () => {
      if (!username.trim() || !recoveryInput.trim()) {
        setError("账号名和找回凭证都要填")
        return
      }
      // new_password 原样传：空串 = 清空成无密码账号
      afterAuth(await api.reset(username.trim(), recoveryInput.trim(), newPassword))
    })

  // ---------- 注册/找回成功：强制展示找回凭证 ----------
  if (forced) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="w-full max-w-md">
          <RecoveryCard
            code={forced.code}
            onContinue={() => {
              const a = forced.account
              setForced(null)
              onDone(a)
            }}
          />
        </div>
      </div>
    )
  }

  const switchMode = (m: "login" | "register" | "reset") => {
    setMode(m)
    setError("")
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-6">
      <div className="w-full max-w-md us-rise">
        <h1 className="us-serif text-5xl mb-3">我们</h1>
        <p className="text-stone-500 mb-10 leading-relaxed">
          各自随手丢碎片，AI 帮你们发现没意识到的连接。
          <br />
          3-10 个人的小圈子，刚好。
        </p>

        <div className="flex flex-col gap-5">
          <div>
            <label className="text-xs text-stone-500">账号名</label>
            <input
              className="us-input"
              placeholder="全局唯一，朋友们靠它找不到你——只靠邀请码"
              value={username}
              maxLength={32}
              onChange={(e) => {
                setUsername(e.target.value)
                setError("")
              }}
            />
          </div>

          {mode !== "reset" && (
            <div>
              <label className="text-xs text-stone-500">
                密码{mode === "register" && "（可空，不填以后只凭账号名登录）"}
              </label>
              <input
                className="us-input"
                type="password"
                placeholder={mode === "login" ? "没设过密码就留空" : "随便设，没有格式要求"}
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value)
                  setError("")
                }}
                onKeyDown={(e) =>
                  e.key === "Enter" &&
                  !e.nativeEvent.isComposing &&
                  (mode === "login" ? handleLogin() : handleRegister())
                }
              />
            </div>
          )}

          {mode === "register" && (
            <div>
              <label className="text-xs text-stone-500">昵称（可空，默认用账号名）</label>
              <input
                className="us-input"
                placeholder="怎么称呼你"
                value={nickname}
                onChange={(e) => setNickname(e.target.value)}
              />
            </div>
          )}

          {mode === "reset" && (
            <>
              <div>
                <label className="text-xs text-stone-500">找回凭证</label>
                <input
                  className="us-input"
                  placeholder="注册时展示过的那串字符"
                  value={recoveryInput}
                  onChange={(e) => {
                    setRecoveryInput(e.target.value)
                    setError("")
                  }}
                />
              </div>
              <div>
                <label className="text-xs text-stone-500">新密码（留空 = 以后只凭账号名登录）</label>
                <input
                  className="us-input"
                  type="password"
                  placeholder="随便设，没有格式要求"
                  value={newPassword}
                  onChange={(e) => {
                    setNewPassword(e.target.value)
                    setError("")
                  }}
                  onKeyDown={(e) =>
                    e.key === "Enter" && !e.nativeEvent.isComposing && handleReset()
                  }
                />
              </div>
            </>
          )}

          {error && <p className="text-sm text-red-700">{error}</p>}

          {mode === "login" && (
            <button className="us-btn w-full py-3" disabled={busy} onClick={handleLogin}>
              {busy ? "登录中…" : "登录"}
            </button>
          )}
          {mode === "register" && (
            <button className="us-btn w-full py-3" disabled={busy} onClick={handleRegister}>
              {busy ? "注册中…" : "注册"}
            </button>
          )}
          {mode === "reset" && (
            <button className="us-btn w-full py-3" disabled={busy} onClick={handleReset}>
              {busy ? "重置中…" : "重置密码"}
            </button>
          )}

          <div className="flex flex-col items-center gap-2 pt-2">
            {mode !== "login" && (
              <button
                className="text-sm text-stone-400 hover:text-[#264653] transition-colors"
                onClick={() => switchMode("login")}
              >
                已有账号？登录
              </button>
            )}
            {mode !== "register" && (
              <button
                className="text-sm text-stone-400 hover:text-[#264653] transition-colors"
                onClick={() => switchMode("register")}
              >
                没有账号？注册一个
              </button>
            )}
            {mode !== "reset" && (
              <button
                className="text-sm text-stone-400 hover:text-[#264653] transition-colors"
                onClick={() => switchMode("reset")}
              >
                忘记密码？用找回凭证重置
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
