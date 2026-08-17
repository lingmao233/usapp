import { useEffect, useState } from "react"
import { Link } from "react-router"
import {
  api,
  type AccountCircle,
  type AccountInfo,
  type Session,
  type SharingCategory,
  type SharingItem,
} from "@/lib/api"
import { copyText } from "@/lib/utils"
import CodeCustomizer from "@/components/CodeCustomizer"
import {
  CreateCircleForm,
  InviteCard,
  JoinCircleForm,
  toSession,
} from "@/components/CircleForms"

const CATEGORY_LABEL: Record<SharingCategory, string> = {
  goal: "目标",
  plan: "每日计划",
  ledger: "记账",
  calorie: "热量",
}

const CATEGORY_DESC: Record<SharingCategory, string> = {
  goal: "进度档只给百分比和打卡；明细档含体重/金额等具体数字",
  plan: "进度档只给完成计数；明细档给出条目内容",
  ledger: "本期不开放共享（无展示出口），先预留",
  calorie: "本期不开放共享（无展示出口），先预留",
}

/** 有档位的类别（goal/plan）；ledger/calorie 只有开关且本期禁用 */
const LEVELED: SharingCategory[] = ["goal", "plan"]

/** 共享设置矩阵：类别 × 圈子。goal/plan 三档（关/仅进度/明细），ledger/calorie 禁用态预留 */
function SharingMatrix({ accountId, circles }: { accountId: string; circles: AccountCircle[] }) {
  const [items, setItems] = useState<SharingItem[] | null>(null)
  const [busyKey, setBusyKey] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    api
      .listSharing(accountId)
      .then((res) => setItems(res.items))
      .catch(() => setError("共享设置没拉到，检查网络后刷新试试"))
  }, [accountId])

  function currentLevel(circleId: string, category: SharingCategory): string | null {
    const hit = items?.find((i) => i.circle_id === circleId && i.category === category)
    return hit ? hit.level || "progress" : null
  }

  async function setLevel(circleId: string, category: SharingCategory, level: string | null) {
    const key = `${circleId}:${category}`
    setBusyKey(key)
    setError("")
    try {
      if (level === null) {
        await api.deleteSharing(accountId, circleId, category)
        setItems((xs) =>
          (xs ?? []).filter((i) => !(i.circle_id === circleId && i.category === category)),
        )
      } else {
        const res = await api.putSharing(accountId, circleId, category, level)
        setItems((xs) => [
          ...(xs ?? []).filter((i) => !(i.circle_id === circleId && i.category === category)),
          { circle_id: circleId, circle_name: "", category, level: res.level },
        ])
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，再试一次")
    } finally {
      setBusyKey("")
    }
  }

  if (items === null && !error) return <p className="text-sm text-stone-400">加载中…</p>

  return (
    <div className="flex flex-col gap-5">
      {(["goal", "plan", "ledger", "calorie"] as SharingCategory[]).map((cat) => {
        const enabled = LEVELED.includes(cat)
        return (
          <div key={cat} className={enabled ? "" : "opacity-60"}>
            <div className="flex items-baseline gap-2 mb-1">
              <p className="text-sm font-medium">{CATEGORY_LABEL[cat]}</p>
              {!enabled && (
                <span className="us-chip text-xs">暂未开放</span>
              )}
            </div>
            <p className="text-xs text-stone-400 mb-2.5">{CATEGORY_DESC[cat]}</p>
            <div className="flex flex-col gap-2">
              {circles.map((c) => {
                const cur = currentLevel(c.circle_id, cat)
                return (
                  <div
                    key={c.circle_id}
                    className="flex items-center justify-between gap-3 bg-white/50 rounded-xl px-4 py-2.5"
                  >
                    <span className="text-sm truncate">{c.circle_name}</span>
                    <span className="flex gap-1.5 shrink-0">
                      {(enabled ? (["off", "progress", "detail"] as const) : (["off"] as const)).map(
                        (opt) => {
                          const active = opt === "off" ? cur === null : cur === opt
                          const label =
                            opt === "off" ? "关" : opt === "progress" ? "仅进度" : "明细"
                          return (
                            <button
                              key={opt}
                              disabled={!enabled || busyKey === `${c.circle_id}:${cat}`}
                              className={`rounded-full px-3 py-1 text-xs transition-colors duration-200 disabled:opacity-50 ${
                                active
                                  ? "bg-[#161616] text-white"
                                  : "text-[#264653] border border-[#264653]/20 hover:bg-[#264653]/8"
                              }`}
                              onClick={() =>
                                setLevel(c.circle_id, cat, opt === "off" ? null : opt)
                              }
                            >
                              {label}
                            </button>
                          )
                        },
                      )}
                    </span>
                  </div>
                )
              })}
              {circles.length === 0 && (
                <p className="text-xs text-stone-400">还没有圈子，先建一个或加入一个</p>
              )}
            </div>
          </div>
        )
      })}
      {error && <p className="text-sm text-red-700">{error}</p>}
    </div>
  )
}

/** 个人页：账号信息、我的圈子（点击跳圈）、建圈/入圈、共享设置、找回凭证 */
export default function Account({
  session,
  account,
  onEnterCircle,
  onLogout,
}: {
  session: Session
  account: AccountInfo
  onEnterCircle: (s: Session) => void
  onLogout: () => void
}) {
  const [circles, setCircles] = useState<AccountCircle[]>([])
  const [recoveryCode, setRecoveryCode] = useState<string | null>(null)
  const [codeCopied, setCodeCopied] = useState(false)
  const [formMode, setFormMode] = useState<"none" | "create" | "join">("none")
  const [createdCode, setCreatedCode] = useState<string | null>(null)
  const [createdSession, setCreatedSession] = useState<Session | null>(null)

  function reloadCircles() {
    api
      .accountCircles(account.account_id)
      .then((res) => setCircles(res.circles))
      .catch(() => {})
  }

  useEffect(() => {
    reloadCircles()
    // 找回凭证查看：走账号详情接口（含 recovery_code）
    api
      .getAccount(account.account_id)
      .then((res) => setRecoveryCode(res.recovery_code))
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.account_id])

  function enterCircle(c: AccountCircle) {
    const s = toSession(
      {
        circle_id: c.circle_id,
        user_id: c.user_id,
        nickname: c.my_nickname,
        circle_name: c.circle_name,
        invite_code: c.invite_code,
      },
      account,
    )
    onEnterCircle(s)
  }

  // 建圈成功：邀请码常驻卡，确认后跳进新圈
  if (createdCode && createdSession) {
    return (
      <div className="max-w-2xl mx-auto px-5 py-16">
        <InviteCard code={createdCode} onContinue={() => onEnterCircle(createdSession)} />
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">个人</h2>
      <p className="text-xs text-stone-500 mb-6">账号、圈子与共享，都在这一页</p>

      {/* 账号信息 */}
      <section className="us-panel rounded-2xl p-5 mb-6">
        <p className="us-serif text-base mb-3">账号信息</p>
        <div className="flex flex-col gap-1.5 text-sm">
          <div className="flex justify-between gap-4">
            <span className="text-stone-500 shrink-0">账号名</span>
            <span>{account.username}</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-stone-500 shrink-0">昵称</span>
            <span>{account.nickname}</span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-stone-500 shrink-0">本圈昵称</span>
            <span>
              {session.nickname}
              <span className="text-xs text-stone-400">（{session.circle_name}）</span>
            </span>
          </div>
          <div className="flex justify-between gap-4">
            <span className="text-stone-500 shrink-0">密码</span>
            <span>{account.has_password ? "已设置" : "未设置（只凭账号名登录）"}</span>
          </div>
        </div>
      </section>

      {/* 我的圈子：点击跳圈 */}
      <section className="mb-6">
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="us-serif text-lg">我的圈子</h3>
          <span className="flex gap-2">
            <button
              className="us-btn-ghost text-xs"
              onClick={() => setFormMode(formMode === "create" ? "none" : "create")}
            >
              + 新建圈子
            </button>
            <button
              className="us-btn-ghost text-xs"
              onClick={() => setFormMode(formMode === "join" ? "none" : "join")}
            >
              + 加入新圈子
            </button>
          </span>
        </div>

        {formMode === "create" && (
          <div className="us-panel rounded-2xl p-5 mb-4">
            <CreateCircleForm
              accountId={account.account_id}
              defaultNickname={account.nickname}
              onCreated={(c) => {
                const s = toSession(c, account)
                setCreatedSession(s)
                setCreatedCode(c.invite_code)
              }}
            />
          </div>
        )}
        {formMode === "join" && (
          <div className="us-panel rounded-2xl p-5 mb-4">
            <JoinCircleForm
              accountId={account.account_id}
              onJoined={(c) => onEnterCircle(toSession(c, account))}
            />
          </div>
        )}

        <div className="flex flex-col gap-3">
          {circles.map((c) => (
            <button
              key={c.circle_id}
              className="us-rise text-left bg-white/60 rounded-2xl px-5 py-4 transition-all duration-200 hover:bg-white/90"
              onClick={() => enterCircle(c)}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="us-serif text-base">
                  {c.circle_name}
                  {c.circle_id === session.circle_id && (
                    <span className="text-xs text-[#F4A261] ml-2">当前</span>
                  )}
                </span>
                <span className="text-xs text-stone-400 shrink-0">{c.member_count} 位成员</span>
              </div>
              <p className="text-xs text-stone-500 mt-1">我在圈里叫「{c.my_nickname}」</p>
            </button>
          ))}
          {circles.length === 0 && (
            <p className="text-sm text-stone-400">只有当前这一个圈子</p>
          )}
        </div>
      </section>

      {/* Self 数据入口 */}
      <section className="mb-6">
        <h3 className="us-serif text-lg mb-3">Self</h3>
        <Link
          to="/me"
          className="us-rise us-panel rounded-2xl p-5 block transition-all duration-200 hover:translate-y-[-1px]"
        >
          <p className="us-serif text-base">今日计划与目标</p>
          <p className="text-xs text-stone-500 mt-1 leading-relaxed">
            目标、每日计划、记账、热量——账号级独一份，与圈子无关
          </p>
        </Link>
      </section>

      {/* 共享设置：类别 × 圈子矩阵 */}
      <section className="us-panel rounded-2xl p-5 mb-6">
        <p className="us-serif text-base mb-1">共享设置</p>
        <p className="text-xs text-stone-500 mb-4 leading-relaxed">
          按圈子决定把 Self 的哪类数据共享出去；朋友在「朋友任务」页按档位看到。没开的类别，谁都看不到。
        </p>
        <SharingMatrix accountId={account.account_id} circles={circles} />
      </section>

      {/* 找回凭证：查看 / 自设 */}
      <section className="us-panel rounded-2xl p-5 mb-6 text-center">
        <p className="us-serif text-base mb-1">找回凭证</p>
        <p className="text-xs text-stone-500 mb-4">忘了密码就靠它重置，别发给圈外人</p>
        {recoveryCode ? (
          <p className="us-serif text-2xl tracking-[0.3em] text-[#264653] mb-4 select-all">
            {recoveryCode}
          </p>
        ) : (
          <p className="text-sm text-stone-400 mb-4">加载中…</p>
        )}
        <button
          className="us-btn-ghost text-xs border border-[#264653]/15"
          disabled={!recoveryCode}
          onClick={async () => recoveryCode && setCodeCopied(await copyText(recoveryCode))}
        >
          {codeCopied ? "已复制 ✓" : "复制凭证"}
        </button>
        <CodeCustomizer
          accountId={account.account_id}
          currentCode={recoveryCode ?? undefined}
          onChanged={(c) => {
            setRecoveryCode(c)
            setCodeCopied(false)
          }}
        />
      </section>

      <div className="text-center">
        <button
          className="text-sm text-stone-400 hover:text-red-700 transition-colors"
          onClick={onLogout}
        >
          退出登录
        </button>
      </div>
    </div>
  )
}
