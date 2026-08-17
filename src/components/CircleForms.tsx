import { useState } from "react"
import { api, type Session } from "@/lib/api"
import { copyText } from "@/lib/utils"

/** 建圈/入圈表单（登录后进行）：只需圈名/圈码 + 本圈昵称。
 * Landing（无圈引导）与 Account（个人页）共用。 */

export interface CircleJoined {
  circle_id: string
  user_id: string
  nickname: string
  circle_name: string
  invite_code: string
}

export interface CircleCreated extends CircleJoined {
  /** 建圈成功返回的完整信息（邀请码给成功卡用） */
  invite_code: string
}

export function CreateCircleForm({
  accountId,
  defaultNickname,
  onCreated,
}: {
  accountId: string
  defaultNickname?: string
  onCreated: (c: CircleCreated) => void
}) {
  const [name, setName] = useState("")
  const [nickname, setNickname] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  async function submit() {
    if (!name.trim()) {
      setError("给圈子起个名字吧")
      return
    }
    setBusy(true)
    setError("")
    try {
      const circle = await api.createCircle(
        name.trim(),
        accountId,
        nickname.trim() || undefined,
      )
      onCreated({
        circle_id: circle.id,
        user_id: circle.user_id,
        nickname: circle.nickname,
        circle_name: circle.name,
        invite_code: circle.invite_code,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : "出了点问题，再试一次")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <label className="text-xs text-stone-500">圈子名字</label>
        <input
          className="us-input"
          placeholder="比如：周末小队"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            setError("")
          }}
        />
      </div>
      <div>
        <label className="text-xs text-stone-500">
          在这个圈子里的昵称（可空{defaultNickname ? `，默认「${defaultNickname}」` : ""}）
        </label>
        <input
          className="us-input"
          placeholder="可和别处不同"
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
        />
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <button className="us-btn" disabled={busy} onClick={submit}>
        {busy ? "创建中…" : "创建圈子"}
      </button>
    </div>
  )
}

export function JoinCircleForm({
  accountId,
  onJoined,
}: {
  accountId: string
  onJoined: (c: CircleJoined) => void
}) {
  const [inviteCode, setInviteCode] = useState("")
  const [nickname, setNickname] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  async function submit() {
    if (!inviteCode.trim()) {
      setError("填一下邀请码")
      return
    }
    setBusy(true)
    setError("")
    try {
      const res = await api.joinCircle(inviteCode.trim(), nickname.trim() || undefined, accountId)
      onJoined({
        circle_id: res.circle_id,
        user_id: res.user_id,
        nickname: res.nickname,
        circle_name: res.circle_name,
        invite_code: res.invite_code,
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : "出了点问题，再试一次")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <label className="text-xs text-stone-500">邀请码</label>
        <input
          className="us-input tracking-[0.3em]"
          placeholder="6 位邀请码"
          value={inviteCode}
          maxLength={6}
          onChange={(e) => {
            setInviteCode(e.target.value.toUpperCase())
            setError("")
          }}
        />
      </div>
      <div>
        <label className="text-xs text-stone-500">在这个圈子里的昵称（可空，可和别处不同）</label>
        <input
          className="us-input"
          placeholder="朋友们怎么叫你"
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
        />
      </div>
      {error && <p className="text-sm text-red-700">{error}</p>}
      <button className="us-btn" disabled={busy} onClick={submit}>
        {busy ? "加入中…" : "加入圈子"}
      </button>
    </div>
  )
}

/** 建圈成功卡：邀请码常驻展示，复制或确认后才进圈 */
export function InviteCard({ code, onContinue }: { code: string; onContinue: () => void }) {
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

/** 由圈子成员信息组装带账号字段的 Session */
export function toSession(c: CircleJoined, account: {
  account_id: string
  username: string
  has_password: boolean
}): Session {
  return {
    circle_id: c.circle_id,
    user_id: c.user_id,
    nickname: c.nickname,
    circle_name: c.circle_name,
    invite_code: c.invite_code,
    account_id: account.account_id,
    username: account.username,
    has_password: account.has_password,
  }
}
