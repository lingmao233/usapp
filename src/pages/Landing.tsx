import { useEffect, useState } from "react"
import {
  api,
  saveSession,
  type AccountCircle,
  type AccountInfo,
  type Session,
} from "@/lib/api"
import {
  CreateCircleForm,
  InviteCard,
  JoinCircleForm,
  toSession,
} from "@/components/CircleForms"

function activeLabel(c: AccountCircle): string {
  if (c.fragment_count === 0) return "还没有碎片，等你来丢第一条"
  if (!c.last_active) return `${c.fragment_count} 条碎片`
  const diff = Date.now() - new Date(c.last_active).getTime()
  if (diff < 3_600_000) return "刚刚有人丢碎片"
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前活跃`
  return `${Math.floor(diff / 86_400_000)} 天前活跃`
}

/** 登录后的落地页：圈子 / Self 二选一。
 * 圈子入口：多圈先出选择页、单圈直进、无圈引导新建/加入。 */
export default function Landing({
  account,
  onEnterCircle,
  onEnterSelf,
  onLogout,
}: {
  account: AccountInfo
  onEnterCircle: (s: Session) => void
  onEnterSelf: () => void
  onLogout: () => void
}) {
  const [circles, setCircles] = useState<AccountCircle[] | null>(null)
  const [view, setView] = useState<"choose" | "circles" | "noCircle">("choose")
  const [createdCode, setCreatedCode] = useState<string | null>(null)
  const [createdSession, setCreatedSession] = useState<Session | null>(null)

  useEffect(() => {
    api
      .accountCircles(account.account_id)
      .then((res) => setCircles(res.circles))
      .catch(() => {
        // 账号失效（如服务端重建）：清登录态回 Onboarding
        onLogout()
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [account.account_id])

  function enterCircle(c: AccountCircle) {
    const session = toSession(
      {
        circle_id: c.circle_id,
        user_id: c.user_id,
        nickname: c.my_nickname,
        circle_name: c.circle_name,
        invite_code: c.invite_code,
      },
      account,
    )
    saveSession(session)
    onEnterCircle(session)
  }

  /** 圈子入口：单圈直进、多圈出选择页、无圈引导新建/加入 */
  function handleCircleEntrance() {
    if (!circles || circles.length === 0) setView("noCircle")
    else if (circles.length === 1) enterCircle(circles[0])
    else setView("circles")
  }

  // ---------- 建圈成功：邀请码常驻，确认后才进圈 ----------
  if (createdCode && createdSession) {
    return (
      <div className="min-h-screen flex items-center justify-center px-6">
        <div className="w-full max-w-md">
          <InviteCard code={createdCode} onContinue={() => onEnterCircle(createdSession)} />
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen px-6 py-12">
      <div className="max-w-2xl mx-auto us-rise">
        <h1 className="us-serif text-4xl mb-2">我们</h1>
        <p className="text-stone-500 mb-10">
          {account.nickname}（{account.username}），欢迎回来。
        </p>

        {view === "choose" && (
          <div className="grid sm:grid-cols-2 gap-4 mb-12">
            <button
              className="us-rise us-panel rounded-2xl p-6 text-left transition-all duration-200 hover:translate-y-[-1px]"
              onClick={handleCircleEntrance}
              disabled={circles === null}
            >
              <p className="us-serif text-xl mb-1.5">进圈子</p>
              <p className="text-sm text-stone-500 leading-relaxed">
                {circles === null
                  ? "加载圈子列表…"
                  : circles.length === 0
                    ? "还没有圈子，建一个或凭邀请码加入"
                    : `${circles.length} 个圈子在等你`}
              </p>
            </button>
            <button
              className="us-rise us-panel rounded-2xl p-6 text-left transition-all duration-200 hover:translate-y-[-1px]"
              style={{ animationDelay: "80ms" }}
              onClick={onEnterSelf}
            >
              <p className="us-serif text-xl mb-1.5">Self</p>
              <p className="text-sm text-stone-500 leading-relaxed">
                你的目标、每日计划、记账和热量，跨圈独一份
              </p>
            </button>
          </div>
        )}

        {/* 多圈选择页 */}
        {view === "circles" && circles && (
          <div className="flex flex-col gap-4 mb-12">
            <div className="flex items-baseline justify-between">
              <h3 className="us-serif text-lg">去哪个圈子坐坐？</h3>
              <button
                className="us-btn-ghost text-xs"
                onClick={() => setView("choose")}
              >
                ← 返回
              </button>
            </div>
            {circles.map((c, i) => (
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
                  <span className="text-sm text-stone-500">我在圈里叫「{c.my_nickname}」</span>
                  <span className="text-xs text-[#264653]/70 shrink-0">{activeLabel(c)}</span>
                </div>
              </button>
            ))}
          </div>
        )}

        {/* 无圈引导：新建 / 加入（建圈入圈完整流程在 个人 页也有入口） */}
        {view === "noCircle" && (
          <div className="mb-12">
            <div className="flex items-baseline justify-between mb-4">
              <h3 className="us-serif text-lg">先有个圈子</h3>
              <button
                className="us-btn-ghost text-xs"
                onClick={() => setView("choose")}
              >
                ← 返回
              </button>
            </div>
            <div className="grid sm:grid-cols-2 gap-8">
              <section>
                <h4 className="us-serif text-base mb-4 pb-2 border-b border-[#264653]/10">
                  建一个新圈子
                </h4>
                <CreateCircleForm
                  accountId={account.account_id}
                  defaultNickname={account.nickname}
                  onCreated={(c) => {
                    const s = toSession(c, account)
                    saveSession(s)
                    setCreatedSession(s)
                    setCreatedCode(c.invite_code)
                  }}
                />
              </section>
              <section>
                <h4 className="us-serif text-base mb-4 pb-2 border-b border-[#264653]/10">
                  我有邀请码
                </h4>
                <JoinCircleForm
                  accountId={account.account_id}
                  onJoined={(c) => {
                    const s = toSession(c, account)
                    saveSession(s)
                    onEnterCircle(s)
                  }}
                />
              </section>
            </div>
          </div>
        )}

        <button
          className="text-sm text-stone-400 hover:text-[#264653] transition-colors"
          onClick={onLogout}
        >
          换个账号登录
        </button>
      </div>
    </div>
  )
}
