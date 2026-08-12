import { useState } from "react"
import { api } from "@/lib/api"

const VALID_RE = /^[A-HJ-KM-NP-Z2-9]{6}$/

/** 身份码自定义：随机换一个（二次确认）+ 自己选一个（实时校验） */
export default function CodeCustomizer({
  accountId,
  onChanged,
}: {
  accountId: string
  currentCode?: string
  onChanged: (code: string) => void
}) {
  const [confirmReset, setConfirmReset] = useState(false)
  const [showCustom, setShowCustom] = useState(false)
  const [input, setInput] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  const inputValid = VALID_RE.test(input.toUpperCase())

  async function handleReset() {
    if (!confirmReset) {
      setConfirmReset(true)
      return
    }
    setBusy(true)
    setError("")
    try {
      const res = await api.resetRecoveryCode(accountId)
      onChanged(res.recovery_code)
      setConfirmReset(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : "重置失败，再试一次")
    } finally {
      setBusy(false)
    }
  }

  async function handleCustom() {
    if (!inputValid || busy) return
    setBusy(true)
    setError("")
    try {
      const res = await api.setRecoveryCode(accountId, input)
      onChanged(res.recovery_code)
      setShowCustom(false)
      setInput("")
    } catch (e) {
      setError(e instanceof Error ? e.message : "出了点问题，再试一次")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 mt-4 pt-4 border-t border-[#264653]/10">
      <div className="flex justify-center gap-3">
        <button
          className={`us-btn-ghost text-xs border ${
            confirmReset ? "border-red-700/40 text-red-700" : "border-[#264653]/15"
          }`}
          disabled={busy}
          onClick={handleReset}
          onBlur={() => setConfirmReset(false)}
        >
          {confirmReset ? "旧码将立即失效，确定？" : "换一个随机身份码"}
        </button>
        <button
          className="us-btn-ghost text-xs border border-[#264653]/15"
          onClick={() => {
            setShowCustom((v) => !v)
            setError("")
          }}
        >
          自己选一个
        </button>
      </div>

      {showCustom && (
        <div className="us-rise">
          <div className="flex gap-2 items-end">
            <input
              className="us-input flex-1 tracking-[0.25em] text-center"
              placeholder="6 位，字母+数字 2-9"
              value={input}
              maxLength={6}
              onChange={(e) => {
                setInput(e.target.value.toUpperCase())
                setError("")
              }}
              onKeyDown={(e) => e.key === "Enter" && handleCustom()}
            />
            <button
              className="us-btn text-xs px-4"
              disabled={!inputValid || busy}
              onClick={handleCustom}
            >
              {busy ? "…" : "用它"}
            </button>
          </div>
          {input.length > 0 && !inputValid && (
            <p className="text-xs text-stone-500 mt-1.5 text-center">
              需要 6 位，只能用字母（不含 I/L/O）和数字 2-9
            </p>
          )}
          {error && <p className="text-xs text-red-700 mt-1.5 text-center">{error}</p>}
        </div>
      )}
      {error && !showCustom && <p className="text-xs text-red-700 text-center">{error}</p>}
    </div>
  )
}
