import { useEffect, useState } from "react"
import { api, type Session } from "@/lib/api"
import { copyText } from "@/lib/utils"
import { enablePush, pushSupported } from "@/lib/push"
import { DEFAULT_PERSONA_KEY, PERSONA_PRESETS, personaLabel } from "@/lib/persona"

/** 设置页：本圈邀请码（可复制）+ 圈子人格设置（自顶栏弹层迁移而来） */
export default function Settings({ session }: { session: Session }) {
  const [copied, setCopied] = useState(false)
  // 圈子人格
  const [preset, setPreset] = useState(DEFAULT_PERSONA_KEY)
  const [custom, setCustom] = useState("")
  const [loaded, setLoaded] = useState(false)
  const [loadError, setLoadError] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [saved, setSaved] = useState(false)
  // 推送入口（第 5 期）：仅在支持 PushManager 且权限未决时显示；拒绝/开启后自然消失
  const [notifyVisible, setNotifyVisible] = useState(
    () => pushSupported() && Notification.permission === "default",
  )
  const [notifyBusy, setNotifyBusy] = useState(false)

  useEffect(() => {
    api
      .getCircle(session.circle_id)
      .then((c) => {
        setPreset(c.persona_preset || DEFAULT_PERSONA_KEY)
        setCustom(c.persona_custom || "")
        setLoaded(true)
      })
      .catch(() => setLoadError(true))
  }, [session.circle_id])

  async function copyInvite() {
    if (await copyText(session.invite_code)) setCopied(true)
  }

  async function savePersona() {
    setBusy(true)
    setError("")
    setSaved(false)
    try {
      await api.updatePersona(session.circle_id, session.user_id, preset, custom.trim())
      setSaved(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，再试一次")
    } finally {
      setBusy(false)
    }
  }

  async function enableNotifications() {
    setNotifyBusy(true)
    try {
      await enablePush(session.user_id)
    } catch {
      /* 网络/SW 异常时入口保留，可重试 */
    } finally {
      setNotifyBusy(false)
      setNotifyVisible(pushSupported() && Notification.permission === "default")
    }
  }

  const customActive = custom.trim().length > 0

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">设置</h2>
      <p className="text-xs text-stone-500 mb-6">「{session.circle_name}」的圈子设置，只影响本圈</p>

      {/* 本圈邀请码 */}
      <section className="us-panel rounded-2xl p-5 mb-6">
        <p className="us-serif text-base mb-1">本圈邀请码</p>
        <p className="text-xs text-stone-500 mb-4">发给朋友，他们凭码在「个人 → 加入新圈子」进来</p>
        <div className="flex items-center gap-4">
          <p className="us-serif text-3xl tracking-[0.3em] text-[#264653] select-all">
            {session.invite_code}
          </p>
          <button className="us-btn-ghost text-xs border border-[#264653]/15" onClick={copyInvite}>
            {copied ? "已复制 ✓" : "复制"}
          </button>
        </div>
      </section>

      {/* 圈子人格 */}
      <section className="us-panel rounded-2xl p-5">
        <p className="us-serif text-base mb-1">圈子人格</p>
        <p className="text-xs text-stone-500 mb-4">
          周报会用这个人格的口吻写。圈里任何人都能换，只影响本圈。
        </p>
        {loaded ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap gap-2">
              {PERSONA_PRESETS.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  title={p.desc}
                  onClick={() => {
                    setPreset(p.key)
                    setSaved(false)
                  }}
                  className={`rounded-full px-3.5 py-1.5 text-sm transition-colors duration-200 ${
                    preset === p.key && !customActive
                      ? "bg-[#161616] text-white"
                      : "text-[#264653] border border-[#264653]/20 hover:bg-[#264653]/8"
                  }`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div>
              <label className="text-xs text-stone-500">
                自定义人格
                {customActive ? `（生效中，已盖过「${personaLabel(preset)}」）` : "（留空则用上面选的预设）"}
              </label>
              <textarea
                className="us-input mt-1.5 min-h-[72px]"
                placeholder="比如：像佟掌柜一样，精明又热乎"
                value={custom}
                onChange={(e) => {
                  setCustom(e.target.value)
                  setSaved(false)
                }}
              />
            </div>
            {error && <p className="text-sm text-red-700">{error}</p>}
            <div className="flex items-center justify-end gap-3">
              {saved && <span className="text-xs text-stone-400">已保存 ✓</span>}
              <button className="us-btn" disabled={busy} onClick={savePersona}>
                {busy ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        ) : loadError ? (
          <p className="text-sm text-red-700">没拿到圈子信息，检查网络后刷新试试</p>
        ) : (
          <p className="text-sm text-stone-400">加载中…</p>
        )}
      </section>

      {/* 通知：仅在权限未决时展示 */}
      {notifyVisible && (
        <section className="us-panel rounded-2xl p-5 mt-6">
          <p className="us-serif text-base mb-1">通知</p>
          <p className="text-xs text-stone-500 mb-4">有新评论/点赞/鞭策时推送通知到这台设备</p>
          <button
            className="us-btn-ghost text-xs border border-[#264653]/15 disabled:opacity-50"
            disabled={notifyBusy}
            onClick={enableNotifications}
          >
            {notifyBusy ? "开启中…" : "开启通知"}
          </button>
        </section>
      )}
    </div>
  )
}
