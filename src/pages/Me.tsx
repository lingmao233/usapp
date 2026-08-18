import { useCallback, useEffect, useState } from "react"
import { Link, useNavigate } from "react-router"
import { api, type Goal, type PlanItem, type PlanNudge } from "@/lib/api"

const GOAL_TYPE_LABEL: Record<string, string> = {
  weight_loss: "减肥",
  savings: "存款",
  study: "学习",
  custom: "自定义",
}

function timeLabel(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

/** 从 progress dict 提取百分比（键名宽松匹配；≤1 视为比例），没有则隐藏进度条 */
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

/** 目标卡摘要：有进度给进度条，否则退回目标日期行 */
function GoalSummary({ goal }: { goal: Goal }) {
  const percent = progressPercent(goal.progress)
  const targetDate =
    (goal.params?.target_date as string) || (goal.params?.deadline as string) || ""
  return (
    <div className="mt-2">
      {percent !== null ? (
        <div className="flex items-center gap-3">
          <div className="flex-1 h-2 rounded-full bg-[#264653]/10 overflow-hidden">
            <div
              className="h-full rounded-full bg-[#F4A261] transition-all duration-500"
              style={{ width: `${percent}%` }}
            />
          </div>
          <span className="text-xs text-stone-500 shrink-0">{percent}%</span>
        </div>
      ) : targetDate ? (
        <p className="text-xs text-stone-500">目标日期 {targetDate}</p>
      ) : (
        <p className="text-xs text-stone-400">进行中</p>
      )}
    </div>
  )
}

export default function Me({ accountId }: { accountId: string }) {
  const navigate = useNavigate()
  const [items, setItems] = useState<PlanItem[]>([])
  const [generating, setGenerating] = useState(false)
  const [goals, setGoals] = useState<Goal[]>([])
  const [planNudges, setPlanNudges] = useState<PlanNudge[]>([])
  const [draft, setDraft] = useState("")
  const [adding, setAdding] = useState(false)
  const [submitError, setSubmitError] = useState("")
  const [loaded, setLoaded] = useState(false)

  // 单次拉今日计划；generating 真值=后台懒生成中
  const refreshToday = useCallback(async () => {
    try {
      const res = await api.todayPlan(accountId)
      setItems(res.items)
      setGenerating(Boolean(res.generating))
      return Boolean(res.generating)
    } catch {
      return false /* 静默，保持现状 */
    }
  }, [accountId])

  // 首载：今日计划带 generating 轮询（3s 间隔，90s deadline 收敛，照抄 Wishes 模式）
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const deadline = Date.now() + 90_000
      for (;;) {
        const still = await refreshToday()
        setLoaded(true)
        if (!still || cancelled || Date.now() > deadline) return
        await new Promise((r) => setTimeout(r, 3000))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [refreshToday])

  // 目标列表独立成败，不拖垮今日计划
  useEffect(() => {
    api
      .listGoals(accountId)
      .then(setGoals)
      .catch(() => {})
  }, [accountId])

  // 今天收到的计划鞭策留言（仅本人可见；独立成败）
  useEffect(() => {
    api
      .listPlanNudges(accountId)
      .then(setPlanNudges)
      .catch(() => {})
  }, [accountId])

  async function handleToggle(item: PlanItem) {
    const done = !item.done
    try {
      await api.updatePlanItem(item.id, accountId, { done })
      setItems((xs) => xs.map((x) => (x.id === item.id ? { ...x, done } : x)))
    } catch {
      /* 失败保持现状，用户可重试 */
    }
  }

  async function handleDelete(item: PlanItem) {
    try {
      await api.deletePlanItem(item.id, accountId)
      setItems((xs) => xs.filter((x) => x.id !== item.id))
    } catch {
      /* 失败保持现状，用户可重试 */
    }
  }

  async function handleAdd() {
    const content = draft.trim()
    if (!content) return
    setAdding(true)
    setSubmitError("")
    try {
      await api.addPlanItem(accountId, content)
      setDraft("")
      await refreshToday()
    } catch {
      setSubmitError("加条目失败，再试一次")
    } finally {
      setAdding(false)
    }
  }

  const doneCount = items.filter((x) => Boolean(x.done)).length
  const activeGoals = goals.filter((g) => g.status === "active")

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">我的</h2>
      <p className="text-xs text-stone-500 mb-6">今日计划、进行中的目标，都在这一页</p>

      {/* 今日计划：AI 懒生成条目 + 自定义条目混排，adjust 条目醒目样式 */}
      <section className="mb-10">
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="us-serif text-lg">今日计划</h3>
          <span className="text-xs text-stone-400">
            {generating
              ? "AI 正在生成今日计划…"
              : items.length > 0
                ? `完成 ${doneCount}/${items.length}`
                : ""}
          </span>
        </div>
        {items.length > 0 && (
          <div className="flex flex-col mb-3">
            {items.map((item, i) => {
              const done = Boolean(item.done)
              const isAdjust = item.source === "adjust"
              return (
                <div
                  key={item.id}
                  className={`us-rise flex items-center gap-3 py-3 ${
                    isAdjust
                      ? "bg-[#F4A261]/25 rounded-xl px-3 -mx-3 my-1"
                      : "border-b border-[#264653]/10"
                  }`}
                  style={{ animationDelay: `${i * 60}ms` }}
                >
                  <button
                    className={`shrink-0 h-5 w-5 rounded-full border transition-colors flex items-center justify-center text-xs ${
                      done
                        ? "bg-[#264653] border-[#264653] text-white"
                        : "border-[#264653]/30 hover:bg-[#264653]/10"
                    }`}
                    title={done ? "取消完成" : "打卡完成"}
                    onClick={() => handleToggle(item)}
                    aria-label={done ? "取消完成" : "打卡完成"}
                  >
                    {done && "✓"}
                  </button>
                  {isAdjust && <span className="us-chip shrink-0">调整</span>}
                  {item.source === "ai" && (
                    <span className="text-xs text-stone-400 shrink-0">AI</span>
                  )}
                  <span
                    className={`text-sm leading-relaxed flex-1 ${
                      done ? "line-through opacity-60" : ""
                    }`}
                  >
                    {item.content}
                  </span>
                  <button
                    className="text-stone-300 hover:text-stone-500 text-sm leading-none shrink-0"
                    onClick={() => handleDelete(item)}
                    aria-label="删除条目"
                  >
                    ×
                  </button>
                </div>
              )
            })}
          </div>
        )}
        {items.length === 0 && (
          <p className="text-sm text-stone-400 mb-3">
            {generating ? "正在生成，喝口水稍等…" : loaded ? "今天还没有条目，加一条？" : "加载中…"}
          </p>
        )}
        <div className="flex gap-3 items-center">
          <input
            className="us-input flex-1"
            placeholder="自定义一条：背 20 个单词 / 跑 3 公里…"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          />
          <button className="us-btn" disabled={adding || !draft.trim()} onClick={handleAdd}>
            {adding ? "加…" : "加条目"}
          </button>
        </div>
        {submitError && <p className="text-xs text-red-500 mt-2">{submitError}</p>}

        {/* 今天收到的计划鞭策留言（样式照抄 GoalDetail 鞭策留言列表，简化无屏蔽管理） */}
        {planNudges.length > 0 && (
          <div className="bg-white/60 rounded-2xl p-5 mt-4">
            <p className="us-serif text-base mb-3">计划鞭策</p>
            <div className="flex flex-col gap-3">
              {planNudges.map((n) => (
                <div key={n.id} className="text-sm">
                  <div className="flex items-baseline gap-2">
                    <span className="font-medium text-[#264653]">
                      {n.from_nickname ?? "圈友"}
                    </span>
                    <span className="text-xs text-stone-400">{timeLabel(n.created_at)}</span>
                  </div>
                  {n.message && <p className="text-stone-600 mt-0.5">{n.message}</p>}
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      {/* 目标卡列表：空态引导去新建 */}
      <section className="mb-10">
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="us-serif text-lg">我的目标</h3>
          <Link to="/me/goals/new" className="us-btn-ghost text-xs">
            + 新建目标
          </Link>
        </div>
        {activeGoals.length === 0 ? (
          <div className="bg-white/60 rounded-2xl p-6 text-center">
            <p className="text-sm text-stone-500 leading-relaxed mb-3">
              还没有进行中的目标。设一个减肥/存款/学习目标，AI 每天给你排计划，还能公开到圈子里接受鞭策。
            </p>
            <Link to="/me/goals/new" className="us-btn">
              设一个目标
            </Link>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {activeGoals.map((g, i) => (
              <button
                key={g.id}
                className="us-rise bg-white/60 rounded-2xl p-5 text-left"
                style={{ animationDelay: `${i * 80}ms` }}
                onClick={() => navigate(`/me/goals/${g.id}`)}
              >
                <div className="flex items-center gap-2">
                  <span className="us-chip shrink-0">{GOAL_TYPE_LABEL[g.type] ?? "目标"}</span>
                  <span className="text-base font-medium leading-relaxed">{g.title}</span>
                </div>
                <GoalSummary goal={g} />
              </button>
            ))}
          </div>
        )}
      </section>

      {/* 工具入口：记账 / 热量 */}
      <section>
        <h3 className="us-serif text-lg mb-3">工具</h3>
        <div className="grid grid-cols-2 gap-3">
          <button
            className="us-rise us-panel rounded-2xl p-5 text-left"
            onClick={() => navigate("/me/ledger")}
          >
            <p className="us-serif text-base">记一笔</p>
            <p className="text-xs text-stone-500 mt-1 leading-relaxed">拍小票自动识别，也能手动记</p>
          </button>
          <button
            className="us-rise us-panel rounded-2xl p-5 text-left"
            style={{ animationDelay: "80ms" }}
            onClick={() => navigate("/me/calories")}
          >
            <p className="us-serif text-base">拍热量</p>
            <p className="text-xs text-stone-500 mt-1 leading-relaxed">食物拍照估算热量，仅供参考</p>
          </button>
        </div>
      </section>
    </div>
  )
}
