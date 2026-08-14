import { useCallback, useEffect, useState } from "react"
import { api, type CommonWish, type Session, type Wish, type WishPlan } from "@/lib/api"
import ImagePicker, { type PickedImage } from "@/components/ImagePicker"
import FragImage from "@/components/FragImage"
import PlanChat from "@/components/PlanChat"

const CATEGORY_LABEL: Record<string, string> = {
  eat: "想吃",
  go: "想去",
  learn: "想学",
  buy: "想买",
  do: "想做",
}

function PlanCard({ plan, participants }: { plan: WishPlan; participants?: string[] }) {
  return (
    <div className="us-related p-4 mt-3 text-sm leading-relaxed">
      <p className="font-medium text-[#264653] mb-2">
        「一起去」方案{participants && participants.length > 0 ? ` · ${participants.join("、")}` : ""}
      </p>
      <div className="flex flex-col gap-1 text-stone-700">
        <p>🕐 {plan.time}</p>
        <p>📍 {plan.location}</p>
        <p>💰 {plan.budget}</p>
      </div>
      <ol className="mt-2.5 flex flex-col gap-1 text-stone-700">
        {plan.steps.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span className="text-[#F4A261] font-medium">{i + 1}.</span>
            <span>{s}</span>
          </li>
        ))}
      </ol>
      {plan.links && plan.links.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-2">
          {plan.links.map((l, i) => (
            <a
              key={i}
              href={l.url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-[#264653] underline underline-offset-2 hover:opacity-70"
            >
              🔗 {l.label}
            </a>
          ))}
        </div>
      )}
      {plan.disclaimer && (
        <p className="mt-2 text-xs text-stone-400">{plan.disclaimer}</p>
      )}
    </div>
  )
}

export default function Wishes({ session }: { session: Session }) {
  const [wishes, setWishes] = useState<Wish[]>([])
  const [common, setCommon] = useState<CommonWish[]>([])
  const [draft, setDraft] = useState("")
  const [adding, setAdding] = useState(false)
  const [plans, setPlans] = useState<Record<string, { plan: WishPlan; participants?: string[] }>>({})
  const [planningId, setPlanningId] = useState<string | null>(null)
  const [visibility, setVisibility] = useState<"public" | "private">("public")
  const [image, setImage] = useState<PickedImage | null>(null)
  const [submitError, setSubmitError] = useState("")
  const [loaded, setLoaded] = useState(false)
  // 已完成愿望区块：默认折叠
  const [showDone, setShowDone] = useState(false)
  // 共同愿望后台重算中（旧结果照旧展示）
  const [commonRefreshing, setCommonRefreshing] = useState(false)

  // 共同愿望走 stale-while-revalidate：后台重算期间旧结果照旧展示，refreshing 收敛后一次性换新
  const refreshCommon = useCallback(async () => {
    const deadline = Date.now() + 90_000
    for (;;) {
      try {
        const c = await api.commonWishes(session.circle_id)
        setCommon(c.common_wishes) // 陈旧结果也先上屏（服务端保留的旧数据）
        setCommonRefreshing(!!c.refreshing)
        if (!c.refreshing) return
      } catch {
        setCommonRefreshing(false)
        return /* 静默，保持现状 */
      }
      if (Date.now() > deadline) return
      await new Promise((r) => setTimeout(r, 3000))
    }
  }, [session.circle_id])

  const load = useCallback(async () => {
    // 两个请求独立成败：共同愿望匹配重、偶发失败，不拖垮愿望列表的展示
    try {
      const w = await api.listWishes(session.circle_id, session.user_id)
      setWishes(w.wishes)
      // 已缓存的方案直接展示
      const cached: Record<string, { plan: WishPlan }> = {}
      w.wishes.forEach((wish) => {
        if (wish.plan) cached[wish.id] = { plan: wish.plan }
      })
      setPlans((prev) => ({ ...cached, ...prev }))
    } catch {
      /* 静默，保持现状 */
    }
    setLoaded(true)
    void refreshCommon()
  }, [session.circle_id, session.user_id, refreshCommon])

  useEffect(() => {
    load()
  }, [load])

  async function handleAdd() {
    const content = draft.trim()
    if (!content && !image) return
    setAdding(true)
    setSubmitError("")
    try {
      // 先上传图片拿 url，再随愿望一起提交
      let imageUrl: string | undefined
      if (image) {
        imageUrl = (await api.uploadImage(image.original, image.display)).url
      }
      await api.addWish(session.circle_id, session.user_id, content, visibility, imageUrl)
      setDraft("")
      if (image) URL.revokeObjectURL(image.preview)
      setImage(null)
      await load()
    } catch {
      setSubmitError("加愿望失败，再试一次")
    } finally {
      setAdding(false)
    }
  }

  async function handlePlan(wishIds: string[]) {
    const id = wishIds[0]
    if (!id) return
    setPlanningId(id)
    try {
      const res = await api.wishPlan(id, session.user_id)
      if (res.status === "generating") {
        // 后台生成中：轮询愿望列表，方案落库即展示（另有 Web Push 通知兜底）
        const deadline = Date.now() + 90_000
        while (Date.now() < deadline) {
          await new Promise((r) => setTimeout(r, 3000))
          try {
            const w = await api.listWishes(session.circle_id, session.user_id)
            const wish = w.wishes.find((x) => x.id === id)
            if (wish?.plan) {
              setPlans((prev) => ({ ...prev, [id]: { plan: wish.plan as WishPlan } }))
              return
            }
          } catch {
            /* 轮询单次失败继续等 */
          }
        }
        return // 超时放弃：推送通知会在生成完成后引导回来查看
      }
      if (res.plan) {
        setPlans((prev) => ({ ...prev, [id]: { plan: res.plan, participants: res.participants } }))
      }
    } finally {
      setPlanningId(null)
    }
  }

  async function handleDeleteWish(w: Wish) {
    if (!window.confirm("删掉这个愿望？")) return
    try {
      await api.deleteWish(w.id, session.user_id)
      setWishes((ws) => ws.filter((x) => x.id !== w.id))
    } catch {
      /* 失败保持现状，用户可重试 */
    }
  }

  // 勾选完成/取消：本地即时更新（服务端已校验作者本人），完成即移出共同愿望匹配池
  async function handleToggleDone(w: Wish) {
    const done = w.status !== "done"
    try {
      await api.toggleWishDone(w.id, session.user_id, done)
      setWishes((ws) => ws.map((x) => (x.id === w.id ? { ...x, status: done ? "done" : "active" } : x)))
      // 匹配池变了，共同愿望重拉一次（旧结果保留展示，后台重算完再换）
      void refreshCommon()
    } catch {
      /* 失败保持现状，用户可重试 */
    }
  }

  const myWishes = wishes.filter((w) => w.user_id === session.user_id)
  const myActive = myWishes.filter((w) => w.status !== "done")
  const myDone = myWishes.filter((w) => w.status === "done")
  const others = wishes.filter((w) => w.user_id !== session.user_id)

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">愿望清单</h2>
      <p className="text-xs text-stone-500 mb-6">
        碎片里说「想去/想学/想吃」会被自动收进来，也可以直接加
      </p>

      {/* 手动添加 */}
      <div className="flex gap-3 items-center mb-2">
        <input
          className="us-input flex-1"
          placeholder="加个愿望：想学滑板 / 想去海边…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
        />
        {/* 公开/私密开关，默认公开 */}
        <button
          className={visibility === "public" ? "us-chip" : "us-btn-ghost text-xs"}
          onClick={() => setVisibility("public")}
        >
          公开
        </button>
        <button
          className={visibility === "private" ? "us-chip" : "us-btn-ghost text-xs"}
          onClick={() => setVisibility("private")}
        >
          🔒 私密
        </button>
        <button
          className="us-btn"
          disabled={adding || (!draft.trim() && !image)}
          onClick={handleAdd}
        >
          {adding ? "加…" : "加愿望"}
        </button>
      </div>
      <div className="flex items-center gap-3 mb-8">
        <ImagePicker image={image} onChange={setImage} />
        {submitError && <span className="text-xs text-red-500">{submitError}</span>}
      </div>

      {/* 共同愿望 */}
      <section className="mb-10">
        <h3 className="us-serif text-lg mb-3">我们的共同愿望</h3>
        {common.length === 0 ? (
          <p className="text-sm text-stone-400 leading-relaxed">
            {commonRefreshing
              ? "AI 正在发现共同愿望…"
              : loaded
                ? "还没有撞在一起的愿望。等你们各自多丢几条，AI 会发现“原来你也想”。"
                : "加载中…"}
          </p>
        ) : (
          <div className="flex flex-col gap-4">
            {common.map((c, i) => {
              const planEntry = c.wish_ids[0] ? plans[c.wish_ids[0]] : undefined
              return (
                <div
                  key={i}
                  className="us-rise bg-white/60 rounded-2xl p-5"
                  style={{ animationDelay: `${i * 80}ms` }}
                >
                  <p className="text-sm text-stone-500 mb-1">
                    {c.matched_users.join(" 和 ")} 都想
                  </p>
                  <p className="text-base font-medium leading-relaxed">{c.content}</p>
                  <p className="text-sm text-stone-500 mt-2 leading-relaxed">{c.suggestion}</p>
                  {planEntry ? (
                    <>
                      <PlanCard plan={planEntry.plan} participants={planEntry.participants} />
                      {c.wish_ids[0] && <PlanChat wishId={c.wish_ids[0]} session={session} />}
                    </>
                  ) : (
                    <button
                      className="us-btn-ghost border border-[#264653]/15 text-xs mt-3"
                      disabled={planningId === c.wish_ids[0]}
                      onClick={() => handlePlan(c.wish_ids)}
                    >
                      {planningId === c.wish_ids[0] ? "生成中…" : "生成「一起去」方案"}
                    </button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

      {/* 我的愿望（未完成） */}
      <section className="mb-8">
        <h3 className="us-serif text-lg mb-3">我的愿望</h3>
        {myActive.length === 0 ? (
          <p className="text-sm text-stone-400">
            {loaded ? (myDone.length > 0 ? "都完成啦，再加一个？" : "还没有，写一个呗") : "加载中…"}
          </p>
        ) : (
          <div className="flex flex-col">
            {myActive.map((w, i) => (
              <div
                key={w.id}
                className="us-rise py-3 border-b border-[#264653]/10 flex items-center gap-3"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                {/* 勾选完成：移出共同愿望匹配池，进下方"已完成" */}
                <button
                  className="shrink-0 h-5 w-5 rounded-full border border-[#264653]/30 hover:bg-[#264653]/10 transition-colors"
                  title="标记为已完成"
                  onClick={() => handleToggleDone(w)}
                  aria-label="标记为已完成"
                />
                <span className="us-chip shrink-0">{CATEGORY_LABEL[w.category] ?? "想做"}</span>
                {w.image_url && (
                  <FragImage
                    url={w.image_url}
                    className="h-10 w-10 rounded-lg object-cover"
                    alt="愿望配图"
                  />
                )}
                <span className="text-sm leading-relaxed">{w.content}</span>
                {w.visibility === "private" && (
                  <span className="text-xs text-stone-400 shrink-0" title="仅自己可见">
                    🔒 仅自己可见
                  </span>
                )}
                {/* 只有自己的愿望能删（服务端同样校验） */}
                <button
                  className="ml-auto text-stone-300 hover:text-stone-500 text-sm leading-none shrink-0"
                  onClick={() => handleDeleteWish(w)}
                  aria-label="删除愿望"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 已完成愿望：独立区块，默认折叠；取消勾选回到未完成区和匹配池 */}
      {myDone.length > 0 && (
        <section className="mb-8">
          <button
            className="us-serif text-lg mb-3 flex items-center gap-2 text-stone-500 hover:text-[#264653] transition-colors"
            onClick={() => setShowDone((v) => !v)}
          >
            已完成愿望
            <span className="text-xs font-normal">
              {myDone.length} 条 {showDone ? "▲ 收起" : "▼ 展开"}
            </span>
          </button>
          {showDone && (
            <div className="flex flex-col">
              {myDone.map((w) => (
                <div
                  key={w.id}
                  className="py-3 border-b border-[#264653]/10 flex items-center gap-3 opacity-60"
                >
                  <button
                    className="shrink-0 h-5 w-5 rounded-full bg-[#264653] text-white text-xs flex items-center justify-center"
                    title="取消完成，回到愿望列表"
                    onClick={() => handleToggleDone(w)}
                    aria-label="取消完成"
                  >
                    ✓
                  </button>
                  <span className="text-sm leading-relaxed line-through">{w.content}</span>
                  <button
                    className="ml-auto text-stone-300 hover:text-stone-500 text-sm leading-none shrink-0"
                    onClick={() => handleDeleteWish(w)}
                    aria-label="删除愿望"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* 朋友们的愿望 */}
      {others.length > 0 && (
        <section>
          <h3 className="us-serif text-lg mb-3">朋友们的愿望</h3>
          <div className="flex flex-col">
            {others.map((w, i) => (
              <div
                key={w.id}
                className="us-rise py-3 border-b border-[#264653]/10 flex items-center gap-3"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <span className="us-chip shrink-0">{CATEGORY_LABEL[w.category] ?? "想做"}</span>
                {w.image_url && (
                  <FragImage
                    url={w.image_url}
                    className="h-10 w-10 rounded-lg object-cover"
                    alt="愿望配图"
                  />
                )}
                <span className="text-sm leading-relaxed flex-1">{w.content}</span>
                <span className="text-xs text-stone-400 shrink-0">{w.user_nickname}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
