import { useEffect, useState } from "react"
import { Link } from "react-router"
import {
  api,
  type FriendGoal,
  type FriendMember,
  type Session,
} from "@/lib/api"

const GOAL_TYPE_LABEL: Record<string, string> = {
  weight_loss: "减肥",
  savings: "存款",
  study: "学习",
  custom: "自定义",
}

function progressPercent(progress?: Record<string, unknown>): number | null {
  if (!progress) return null
  for (const k of ["percent", "progress_percent", "percentage", "rate"]) {
    const v = progress[k]
    if (typeof v === "number" && Number.isFinite(v)) {
      return Math.max(0, Math.min(100, Math.round(v <= 1 ? v * 100 : v)))
    }
  }
  return null
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-2 rounded-full bg-[#264653]/10 overflow-hidden">
        <div
          className="h-full rounded-full bg-[#F4A261] transition-all duration-500"
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="text-xs text-stone-500 shrink-0">{percent}%</span>
    </div>
  )
}

/** 共享出来的目标：progress 档只有进度条；detail 档可点进详情看明细 */
function FriendGoalRow({ goal }: { goal: FriendGoal }) {
  const percent = progressPercent(goal.progress)
  const todayDone = typeof goal.progress?.today_done === "number" ? goal.progress.today_done : null
  const todayTotal =
    typeof goal.progress?.today_total === "number" ? goal.progress.today_total : null
  const streak =
    typeof goal.progress?.streak_days === "number" ? goal.progress.streak_days : null
  const inner = (
    <>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="us-chip shrink-0">{GOAL_TYPE_LABEL[goal.type] ?? "目标"}</span>
        <span className="text-sm font-medium leading-relaxed">{goal.title}</span>
        {goal.share_level === "detail" && (
          <span className="text-xs text-[#264653]/70 ml-auto shrink-0">查看明细 →</span>
        )}
      </div>
      <div className="mt-2">
        {percent !== null && <ProgressBar percent={percent} />}
        <p className="text-xs text-stone-400 mt-1.5">
          {[
            todayTotal !== null ? `今日 ${todayDone}/${todayTotal}` : null,
            streak ? `连续全勤 ${streak} 天` : null,
            goal.share_level === "progress" ? "仅进度" : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
    </>
  )
  if (goal.share_level === "detail") {
    return (
      <Link
        to={`/me/goals/${goal.id}`}
        className="block bg-white/50 rounded-xl px-4 py-3 hover:bg-white/80 transition-colors"
      >
        {inner}
      </Link>
    )
  }
  return <div className="bg-white/50 rounded-xl px-4 py-3">{inner}</div>
}

/** 成员卡：目标列表 + 今日计划 + 鞭策（对人一天一次，服务端 429 兜底） */
function MemberCard({
  member,
  myAccountId,
  onNudged,
}: {
  member: FriendMember
  myAccountId: string
  onNudged: (accountId: string) => void
}) {
  const [msg, setMsg] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  // 鞭策挂在目标上：取第一个共享目标；只共享计划没共享目标时没有鞭策入口
  const nudgeGoal = member.goals?.find((g) => g.nudge_enabled) ?? member.goals?.[0]

  async function handleNudge() {
    if (!nudgeGoal) return
    setBusy(true)
    setError("")
    try {
      await api.sendNudge(nudgeGoal.id, myAccountId, msg.trim())
      setMsg("")
      onNudged(member.account_id)
    } catch (e) {
      const m = e instanceof Error ? e.message : "发送失败，再试一次"
      setError(m)
      if (m.includes("429") || m.includes("已经鞭策过")) onNudged(member.account_id)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="us-rise us-panel rounded-2xl p-5">
      <div className="flex items-baseline justify-between gap-3 mb-4">
        <h3 className="us-serif text-lg">{member.nickname}</h3>
        {member.viewer_nudged_today && (
          <span className="text-xs text-stone-400 shrink-0">今天已鞭策 ✓</span>
        )}
      </div>

      {member.goals && member.goals.length > 0 && (
        <div className="flex flex-col gap-2.5 mb-4">
          {member.goals.map((g) => (
            <FriendGoalRow key={g.id} goal={g} />
          ))}
        </div>
      )}

      {member.plan && (
        <div className="bg-white/50 rounded-xl px-4 py-3 mb-4">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-sm font-medium">今日计划</span>
            <span className="text-xs text-stone-500">
              完成 {member.plan.today_done}/{member.plan.today_total}
              {member.plan.share_level === "progress" && " · 仅进度"}
            </span>
          </div>
          {member.plan.today_total > 0 && (
            <div className="mt-2">
              <ProgressBar
                percent={Math.round((member.plan.today_done / member.plan.today_total) * 100)}
              />
            </div>
          )}
          {member.plan.share_level === "detail" && member.plan.items && (
            <div className="flex flex-col gap-1.5 mt-3">
              {member.plan.items.map((item) => (
                <div key={item.id} className="flex items-center gap-2 text-sm">
                  <span
                    className={`h-4 w-4 rounded-full flex items-center justify-center text-[10px] shrink-0 ${
                      Boolean(item.done)
                        ? "bg-[#264653] text-white"
                        : "border border-[#264653]/30"
                    }`}
                  >
                    {Boolean(item.done) && "✓"}
                  </span>
                  <span className={Boolean(item.done) ? "line-through opacity-60" : ""}>
                    {item.content}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 鞭策：当日已鞭策置灰；目标关了鞭策则提示 */}
      {nudgeGoal && !member.viewer_nudged_today && (
        <div>
          {nudgeGoal.nudge_enabled ? (
            <div className="flex gap-2 items-center">
              <input
                className="us-input flex-1"
                placeholder="留句话，比如「别躺了，起来卷」"
                value={msg}
                onChange={(e) => setMsg(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && handleNudge()}
              />
              <button className="us-btn shrink-0" disabled={busy} onClick={handleNudge}>
                {busy ? "发送中…" : "鞭策一下"}
              </button>
            </div>
          ) : (
            <p className="text-xs text-stone-400">TA 关闭了鞭策入口</p>
          )}
          {error && <p className="text-sm text-red-700 mt-2">{error}</p>}
        </div>
      )}
    </section>
  )
}

/** 朋友任务：圈内成员共享出来的目标 + 今日计划，按人分卡（服务端已按共享档位裁剪） */
export default function FriendsTasks({ session }: { session: Session }) {
  const accountId = session.account_id ?? ""
  const [members, setMembers] = useState<FriendMember[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!accountId) return
    api
      .friendTasks(session.circle_id, accountId)
      .then((res) => setMembers(res.members))
      .catch(() => setError(true))
  }, [session.circle_id, accountId])

  function handleNudged(accountId: string) {
    setMembers(
      (ms) =>
        ms?.map((m) => (m.account_id === accountId ? { ...m, viewer_nudged_today: true } : m)) ??
        null,
    )
  }

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">朋友任务</h2>
      <p className="text-xs text-stone-500 mb-6">
        伙伴们共享出来的目标与今日计划。看见谁躺平了，鞭策一下
      </p>

      {members === null && !error && <p className="text-sm text-stone-400">加载中…</p>}
      {error && <p className="text-sm text-red-700">没拉到朋友任务，检查网络后刷新试试</p>}
      {members?.length === 0 && (
        <div className="us-panel rounded-2xl p-6 text-center">
          <p className="text-sm text-stone-500 leading-relaxed mb-3">
            还没有伙伴共享目标或计划。
            <br />
            喊大家在「个人 → 共享设置」里把目标/每日计划打开，这里就热闹了。
          </p>
          <Link to="/account" className="us-btn inline-block">
            去开我的共享
          </Link>
        </div>
      )}
      <div className="flex flex-col gap-4">
        {members?.map((m) => (
          <MemberCard
            key={m.account_id}
            member={m}
            myAccountId={accountId}
            onNudged={handleNudged}
          />
        ))}
      </div>
    </div>
  )
}
