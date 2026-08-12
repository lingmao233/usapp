import { useCallback, useEffect, useState } from "react"
import { api, type CommonWish, type Session, type Wish, type WishPlan } from "@/lib/api"
import ImagePicker, { type PickedImage } from "@/components/ImagePicker"
import FragImage from "@/components/FragImage"

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

  const load = useCallback(async () => {
    try {
      const [w, c] = await Promise.all([
        api.listWishes(session.circle_id, session.user_id),
        api.commonWishes(session.circle_id),
      ])
      setWishes(w.wishes)
      setCommon(c.common_wishes)
      // 已缓存的方案直接展示
      const cached: Record<string, { plan: WishPlan }> = {}
      w.wishes.forEach((wish) => {
        if (wish.plan) cached[wish.id] = { plan: wish.plan }
      })
      setPlans((prev) => ({ ...cached, ...prev }))
    } catch {
      /* 静默 */
    }
  }, [session.circle_id, session.user_id])

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
      const res = await api.wishPlan(id)
      setPlans((prev) => ({ ...prev, [id]: { plan: res.plan, participants: res.participants } }))
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

  const myWishes = wishes.filter((w) => w.user_id === session.user_id)
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
            还没有撞在一起的愿望。等你们各自多丢几条，AI 会发现"原来你也想"。
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
                    <PlanCard plan={planEntry.plan} participants={planEntry.participants} />
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

      {/* 我的愿望 */}
      <section className="mb-8">
        <h3 className="us-serif text-lg mb-3">我的愿望</h3>
        {myWishes.length === 0 ? (
          <p className="text-sm text-stone-400">还没有，写一个呗</p>
        ) : (
          <div className="flex flex-col">
            {myWishes.map((w, i) => (
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
