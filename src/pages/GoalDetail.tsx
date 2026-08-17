import { useEffect, useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { api, type Goal, type Nudge, type PlanItem } from "@/lib/api"

const GOAL_TYPE_LABEL: Record<string, string> = {
  weight_loss: "减肥",
  savings: "存款",
  study: "学习",
  custom: "自定义",
}

const STATUS_LABEL: Record<string, string> = {
  active: "进行中",
  done: "已完成",
  abandoned: "已放弃",
}

/** framework/progress 已知键的中文标签（与服务端 rules.py 产出对齐）；未知键原样展示 */
const FIELD_LABEL: Record<string, string> = {
  // 减肥框架
  bmr_kcal: "基础代谢",
  tdee_kcal: "每日消耗",
  budget_kcal: "每日热量预算",
  deficit_kcal: "每日缺口",
  days_left: "剩余天数",
  // 存款框架
  monthly_save_fen: "每月建议存款",
  monthly_spendable_fen: "每月可花额度",
  elastic_baseline_fen: "弹性花销基线",
  months_left: "剩余月数",
  required_monthly_fen: "达成需每月存",
  // 学习框架
  daily_minutes: "每日投入",
  // 进度摘要
  streak_days: "连续全勤",
}

/** 不逐行展示的键（噪音/已合并/内部字段） */
const HIDDEN_KEYS = new Set([
  "percent",
  "progress_percent",
  "percentage",
  "rate",
  "activity_factor",
  "today_done",
  "today_total",
  "total_done",
  "total_items",
])

function fmtValue(key: string, v: unknown): string {
  if (typeof v === "number" && Number.isFinite(v)) {
    if (key.endsWith("_fen")) return `¥${(v / 100).toFixed(2)}`
    if (key.endsWith("_kcal")) return `${Math.round(v)} kcal`
    if (key === "days_left") return `${v} 天`
    if (key === "months_left") return `${v} 个月`
    if (key === "daily_minutes") return `${v} 分钟`
    return String(v)
  }
  return String(v)
}

/** dict 里可展示的原始条目（字符串/数字），按已知标签优先排序 */
function primitiveEntries(dict?: Record<string, unknown>): [string, unknown][] {
  if (!dict) return []
  return Object.entries(dict)
    .filter(
      ([k, v]) =>
        (typeof v === "string" || typeof v === "number") && !HIDDEN_KEYS.has(k),
    )
    .sort((a, b) => Number(!(a[0] in FIELD_LABEL)) - Number(!(b[0] in FIELD_LABEL)))
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

function timeLabel(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

/** 进度/框架数据卡：有百分比先画进度条，"今日完成 x/y" 合并一行，其余字段逐行列出 */
function DataCard({ title, dict }: { title: string; dict?: Record<string, unknown> }) {
  const entries = primitiveEntries(dict)
  const percent = progressPercent(dict)
  const todayDone = typeof dict?.today_done === "number" ? dict.today_done : null
  const todayTotal = typeof dict?.today_total === "number" ? dict.today_total : null
  const totalDone = typeof dict?.total_done === "number" ? dict.total_done : null
  const totalItems = typeof dict?.total_items === "number" ? dict.total_items : null
  if (entries.length === 0 && percent === null && todayTotal === null) return null
  return (
    <div className="bg-white/60 rounded-2xl p-5 mb-4">
      <p className="us-serif text-base mb-3">{title}</p>
      {percent !== null && (
        <div className="flex items-center gap-3 mb-3">
          <div className="flex-1 h-2 rounded-full bg-[#264653]/10 overflow-hidden">
            <div className="h-full rounded-full bg-[#F4A261]" style={{ width: `${percent}%` }} />
          </div>
          <span className="text-xs text-stone-500 shrink-0">{percent}%</span>
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        {todayTotal !== null && (
          <div className="flex items-baseline justify-between gap-4 text-sm">
            <span className="text-stone-500 shrink-0">今日完成</span>
            <span>
              {todayDone}/{todayTotal}
            </span>
          </div>
        )}
        {totalItems !== null && (
          <div className="flex items-baseline justify-between gap-4 text-sm">
            <span className="text-stone-500 shrink-0">累计完成</span>
            <span>
              {totalDone}/{totalItems}
            </span>
          </div>
        )}
        {entries.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-4 text-sm">
            <span className="text-stone-500 shrink-0">{FIELD_LABEL[k] ?? k}</span>
            <span className="text-right leading-relaxed">{fmtValue(k, v)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** 存款目标的月度结算卡（服务端懒结算落库 framework.settlement） */
function SettlementCard({ settlement }: { settlement: Record<string, unknown> }) {
  const fen = (k: string) =>
    typeof settlement[k] === "number" ? `¥${((settlement[k] as number) / 100).toFixed(2)}` : "—"
  return (
    <div className="us-panel rounded-2xl p-5 mb-4">
      <p className="us-serif text-base mb-2">
        {typeof settlement.month === "string" ? `${settlement.month} 结算` : "月度结算"}
      </p>
      {typeof settlement.advice === "string" && settlement.advice && (
        <p className="text-sm leading-relaxed mb-3">{settlement.advice}</p>
      )}
      <div className="flex flex-col gap-1.5 text-sm">
        <div className="flex justify-between">
          <span className="text-stone-500">实际存入</span>
          <span>{fen("actual_saved_fen")}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-stone-500">累计已存</span>
          <span>{fen("saved_fen")}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-stone-500">滚雪球后每月目标</span>
          <span>{fen("monthly_target_fen")}</span>
        </div>
      </div>
    </div>
  )
}

export default function GoalDetail({ accountId }: { accountId: string }) {
  const { id } = useParams()
  const navigate = useNavigate()
  const [goal, setGoal] = useState<Goal | null>(null)
  const [loadState, setLoadState] = useState<"loading" | "ok" | "error">("loading")
  // owner 专属数据
  const [nudges, setNudges] = useState<Nudge[]>([])
  const [relatedItems, setRelatedItems] = useState<PlanItem[]>([])
  const [blockedSenders, setBlockedSenders] = useState<string[]>([])
  // 鞭策开关（owner）
  const [nudgeEnabled, setNudgeEnabled] = useState(true)
  const [nudgeToggleBusy, setNudgeToggleBusy] = useState(false)
  // viewer 鞭策
  const [nudgeMsg, setNudgeMsg] = useState("")
  const [nudgeBusy, setNudgeBusy] = useState(false)
  const [nudged, setNudged] = useState(false)
  const [nudgeError, setNudgeError] = useState("")

  const isOwner = goal?.account_id === accountId

  useEffect(() => {
    if (!id) return
    api
      .getGoal(id, accountId)
      .then((g) => {
        setGoal(g)
        setLoadState("ok")
        setNudgeEnabled(Boolean(g.nudge_enabled))
        // 服务端附带 viewer 当日是否已鞭策（置灰；发送失败 429 兜底）
        setNudged(Boolean(g.viewer_nudged_today))
      })
      .catch(() => setLoadState("error"))
  }, [id, accountId])

  // owner：拉鞭策留言 + 今日关联计划条目（独立成败）
  useEffect(() => {
    if (!id || !isOwner) return
    api
      .listNudges(id, accountId)
      .then(setNudges)
      .catch(() => {})
    api
      .todayPlan(accountId)
      .then((res) => setRelatedItems(res.items.filter((x) => x.goal_id === id)))
      .catch(() => {})
  }, [id, isOwner, accountId])

  // 鞭策开关（仅 owner）：即时保存；共享范围/粒度请到「个人 → 共享设置」
  async function handleToggleNudge(enabled: boolean) {
    if (!goal) return
    setNudgeEnabled(enabled)
    setNudgeToggleBusy(true)
    try {
      await api.toggleNudge(goal.id, accountId, enabled)
    } catch {
      setNudgeEnabled(!enabled) // 失败回滚
    } finally {
      setNudgeToggleBusy(false)
    }
  }

  async function handleNudge() {
    if (!goal) return
    setNudgeBusy(true)
    setNudgeError("")
    try {
      await api.sendNudge(goal.id, accountId, nudgeMsg.trim())
      setNudged(true)
      setNudgeMsg("")
    } catch (e) {
      const msg = e instanceof Error ? e.message : "发送失败，再试一次"
      setNudgeError(msg)
      // 限频（429）由后端兜底：今天已鞭策过则置灰
      if (msg.includes("429") || msg.includes("已经鞭策过")) setNudged(true)
    } finally {
      setNudgeBusy(false)
    }
  }

  async function handleBlock(senderAccountId: string) {
    if (!window.confirm("屏蔽后 TA 不能再鞭策你，确定？")) return
    try {
      await api.blockNudgeUser(accountId, senderAccountId)
      setBlockedSenders((xs) => [...xs, senderAccountId])
    } catch {
      /* 失败保持现状，用户可重试 */
    }
  }

  if (loadState === "loading") {
    return (
      <div className="max-w-2xl mx-auto px-5 py-8">
        <p className="text-sm text-stone-400">加载中…</p>
      </div>
    )
  }
  if (loadState === "error" || !goal) {
    return (
      <div className="max-w-2xl mx-auto px-5 py-8">
        <button className="us-btn-ghost mb-4 -ml-4" onClick={() => navigate(-1)}>
          ← 返回
        </button>
        <p className="text-sm text-red-700">目标不存在，或对你不可见</p>
      </div>
    )
  }

  const ownerName = goal.owner_nickname ?? "圈友"
  const visibleNudges = nudges.filter((n) => !blockedSenders.includes(n.from_account_id))
  const settlement =
    goal.framework && typeof goal.framework.settlement === "object" && goal.framework.settlement
      ? (goal.framework.settlement as Record<string, unknown>)
      : null

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <button className="us-btn-ghost mb-4 -ml-4" onClick={() => navigate(-1)}>
        ← 返回
      </button>
      <div className="flex items-center gap-2 mb-1 flex-wrap">
        <span className="us-chip">{GOAL_TYPE_LABEL[goal.type] ?? "目标"}</span>
        <span className="text-xs text-stone-400">{STATUS_LABEL[goal.status] ?? goal.status}</span>
        {!isOwner && <span className="text-xs text-stone-400">· {ownerName} 的目标</span>}
      </div>
      <h2 className="us-serif text-2xl mb-6">{goal.title}</h2>

      {/* 进度：owner 与 viewer 都看；粒度裁剪服务端已做，有什么渲染什么 */}
      <DataCard title="进度" dict={goal.progress} />
      {isOwner ? (
        <>
          <DataCard title="目标框架" dict={goal.framework} />
          {settlement && <SettlementCard settlement={settlement} />}
        </>
      ) : (
        goal.share_level === "detail" && <DataCard title="明细" dict={goal.framework} />
      )}

      {isOwner ? (
        <>
          {/* 关联计划条目（今日） */}
          {relatedItems.length > 0 && (
            <div className="bg-white/60 rounded-2xl p-5 mb-4">
              <p className="us-serif text-base mb-3">今日相关条目</p>
              <div className="flex flex-col gap-2">
                {relatedItems.map((item) => (
                  <div key={item.id} className="flex items-center gap-2 text-sm">
                    <span
                      className={`h-4 w-4 rounded-full flex items-center justify-center text-[10px] shrink-0 ${
                        item.done ? "bg-[#264653] text-white" : "border border-[#264653]/30"
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
              <p className="text-xs text-stone-400 mt-3">打卡在「我的」页今日计划里</p>
            </div>
          )}

          {/* 鞭策留言列表（owner 全见）+ 按人屏蔽 */}
          <div className="bg-white/60 rounded-2xl p-5 mb-4">
            <p className="us-serif text-base mb-3">鞭策留言</p>
            {visibleNudges.length === 0 ? (
              <p className="text-sm text-stone-400">还没有人鞭策你</p>
            ) : (
              <div className="flex flex-col gap-3">
                {visibleNudges.map((n) => (
                  <div key={n.id} className="text-sm">
                    <div className="flex items-baseline gap-2">
                      <span className="font-medium text-[#264653]">
                        {n.from_nickname ?? "圈友"}
                      </span>
                      <span className="text-xs text-stone-400">{timeLabel(n.created_at)}</span>
                      <button
                        className="ml-auto text-xs text-stone-300 hover:text-stone-500"
                        onClick={() => handleBlock(n.from_account_id)}
                      >
                        屏蔽 TA
                      </button>
                    </div>
                    {n.message && <p className="text-stone-700 mt-0.5">{n.message}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 鞭策开关 + 共享入口提示 */}
          <div className="us-panel rounded-2xl p-5">
            <p className="us-serif text-base mb-3">共享与鞭策</p>
            <label className="flex items-center gap-2.5 text-sm cursor-pointer mb-3">
              <input
                type="checkbox"
                className="accent-[#264653] w-4 h-4"
                checked={nudgeEnabled}
                disabled={nudgeToggleBusy}
                onChange={(e) => handleToggleNudge(e.target.checked)}
              />
              允许圈友鞭策我
            </label>
            <p className="text-xs text-stone-400 leading-relaxed">
              共享给哪些圈子、给进度还是明细，统一到
              <Link to="/account" className="text-[#264653] underline underline-offset-2 mx-1">
                个人 → 共享设置
              </Link>
              按圈子开关。
            </p>
          </div>
        </>
      ) : (
        /* viewer：鞭策入口（目标转私有/关鞭策后消失；当日已鞭策置灰，后端 429 兜底） */
        goal.nudge_enabled && (
          <div className="us-panel rounded-2xl p-5">
            <p className="us-serif text-base mb-2">鞭策一下</p>
            <p className="text-xs text-stone-400 mb-3">每天对同一个人只能鞭策一次，留言只有你们俩可见</p>
            {nudged ? (
              <p className="text-sm text-stone-500">今天已经鞭策过了，明天再来 💪</p>
            ) : (
              <>
                <div className="flex gap-3 items-center">
                  <input
                    className="us-input flex-1"
                    placeholder="留句话，比如「别躺了，起来卷」"
                    value={nudgeMsg}
                    onChange={(e) => setNudgeMsg(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleNudge()}
                  />
                  <button className="us-btn" disabled={nudgeBusy} onClick={handleNudge}>
                    {nudgeBusy ? "发送中…" : "鞭策"}
                  </button>
                </div>
                {nudgeError && <p className="text-sm text-red-700 mt-2">{nudgeError}</p>}
              </>
            )}
          </div>
        )
      )}
    </div>
  )
}
