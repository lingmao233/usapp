import { useState } from 'react'
import { Input, Text, View } from '@tarojs/components'
import { api } from '@/platform'

const VALID_RE = /^[A-HJ-KM-NP-Z2-9]{6}$/

/** 身份码自定义：随机换一个（二次确认）+ 自己选一个（实时校验）
 *
 * 小程序端用 View 模拟按钮；二次确认态 3 秒未操作自动复原（替代 web 的 onBlur）。
 */
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
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const inputValid = VALID_RE.test(input.toUpperCase())

  async function handleReset() {
    if (!confirmReset) {
      setConfirmReset(true)
      setTimeout(() => setConfirmReset(false), 3000)
      return
    }
    setBusy(true)
    setError('')
    try {
      const res = await api.resetRecoveryCode(accountId)
      onChanged(res.recovery_code)
      setConfirmReset(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : '重置失败，再试一次')
    } finally {
      setBusy(false)
    }
  }

  async function handleCustom() {
    if (!inputValid || busy) return
    setBusy(true)
    setError('')
    try {
      const res = await api.setRecoveryCode(accountId, input)
      onChanged(res.recovery_code)
      setShowCustom(false)
      setInput('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '出了点问题，再试一次')
    } finally {
      setBusy(false)
    }
  }

  return (
    <View className="flex flex-col gap-3 mt-4 pt-4 border-t border-[#264653]/10">
      <View className="flex justify-center gap-3">
        <View
          className={`us-btn-ghost text-xs border ${
            confirmReset ? 'border-red-700/40 text-red-700' : 'border-[#264653]/15'
          }`}
          onClick={busy ? undefined : handleReset}
        >
          {confirmReset ? '旧码将立即失效，确定？' : '换一个随机身份码'}
        </View>
        <View
          className="us-btn-ghost text-xs border border-[#264653]/15"
          onClick={() => {
            setShowCustom((v) => !v)
            setError('')
          }}
        >
          自己选一个
        </View>
      </View>

      {showCustom && (
        <View className="us-rise">
          <View className="flex gap-2 items-end">
            <Input
              className="us-input flex-1 tracking-[0.25em] text-center"
              placeholder="6 位，字母+数字 2-9"
              placeholderClass="us-input-ph"
              value={input}
              maxlength={6}
              onInput={(e) => {
                setInput(e.detail.value.toUpperCase())
                setError('')
              }}
              onConfirm={handleCustom}
            />
            <View
              className={`us-btn text-xs px-4 ${!inputValid || busy ? 'us-btn-disabled' : ''}`}
              onClick={!inputValid || busy ? undefined : handleCustom}
            >
              {busy ? '…' : '用它'}
            </View>
          </View>
          {input.length > 0 && !inputValid && (
            <Text className="text-xs text-stone-500 mt-1.5 text-center block">
              需要 6 位，只能用字母（不含 I/L/O）和数字 2-9
            </Text>
          )}
          {error && <Text className="text-xs text-red-700 mt-1.5 text-center block">{error}</Text>}
        </View>
      )}
      {error && !showCustom && (
        <Text className="text-xs text-red-700 text-center block">{error}</Text>
      )}
    </View>
  )
}
