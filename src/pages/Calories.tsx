import { useCallback, useEffect, useState } from "react"
import {
  api,
  type CalorieEntry,
  type ExerciseEquiv,
} from "@/lib/api"
import ImagePicker, { type PickedImage } from "@/components/ImagePicker"

function todayLocal() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

/** 运动等效文案：「跑步（8公里/小时）45 分钟」 */
function equivText(v: ExerciseEquiv) {
  return `${v.name} ${Math.round(v.minutes)} 分钟`
}

function equivList(eq?: Record<string, ExerciseEquiv>): ExerciseEquiv[] {
  return Object.values(eq ?? {}).filter((v) => v && typeof v.minutes === "number" && v.minutes > 0)
}

/** 待确认卡（识别时已落库 pending 行，带 id；确认时只回传 id + 可改的总热量/备注） */
interface PendingEntry {
  id: string
  items: { name: string; kcal: number }[]
  total: string
  note: string
  exercise_equiv: Record<string, ExerciseEquiv>
}

export default function Calories({ accountId }: { accountId: string }) {
  const today = todayLocal()
  const [entries, setEntries] = useState<CalorieEntry[]>([])
  const [consumed, setConsumed] = useState(0)
  const [budget, setBudget] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  // 拍照识别
  const [image, setImage] = useState<PickedImage | null>(null)
  const [hint, setHint] = useState("")
  const [recBusy, setRecBusy] = useState(false)
  const [recError, setRecError] = useState("")
  const [pending, setPending] = useState<PendingEntry | null>(null)
  const [confirmBusy, setConfirmBusy] = useState(false)
  // 手动录入
  const [mKcal, setMKcal] = useState("")
  const [mNote, setMNote] = useState("")
  const [manualBusy, setManualBusy] = useState(false)
  const [submitError, setSubmitError] = useState("")
  // 超预算联动提示（服务端确认/手动入账后返回 adjustment）
  const [adjustNotice, setAdjustNotice] = useState("")

  const load = useCallback(async () => {
    try {
      const res = await api.listCalories(accountId, today)
      setEntries(res.items)
      setConsumed(Math.round(res.consumed_kcal))
      setBudget(res.budget_kcal ?? null)
    } catch {
      /* 静默，保持现状 */
    }
    setLoaded(true)
  }, [accountId, today])

  useEffect(() => {
    load()
  }, [load])

  async function handleRecognize() {
    if (!image) return
    setRecBusy(true)
    setRecError("")
    try {
      const url = (await api.uploadImage(image.original, image.display)).url
      const entry = await api.recognizeFood(accountId, url, hint.trim() || undefined)
      setPending({
        id: entry.id,
        items: (entry.items ?? []).map((x) => ({ name: x.name, kcal: x.kcal })),
        total: String(Math.round(entry.total_kcal || 0)),
        note: entry.note || hint.trim(),
        exercise_equiv: entry.exercise_equiv ?? {},
      })
      URL.revokeObjectURL(image.preview)
      setImage(null)
    } catch (e) {
      const msg = e instanceof Error ? e.message : ""
      setRecError(
        msg.includes("400") || msg.includes("视觉模型") || msg.includes("没识别出")
          ? msg.includes("没识别出")
            ? "没识别出有效食物，请手动录入"
            : "未配置视觉模型，请手动录入"
          : msg || "识别失败，再试一次",
      )
    } finally {
      setRecBusy(false)
    }
  }

  async function handleConfirm() {
    if (!pending) return
    const total = Number(pending.total)
    if (!Number.isFinite(total) || total <= 0) {
      setSubmitError("总热量没填对，检查一下")
      return
    }
    setConfirmBusy(true)
    setSubmitError("")
    try {
      const res = await api.confirmCalorie(pending.id, accountId, Math.round(total), pending.note)
      setAdjustNotice(res.adjustment?.content ?? "")
      setPending(null)
      setHint("")
      await load()
    } catch {
      setSubmitError("入账失败，再试一次")
    } finally {
      setConfirmBusy(false)
    }
  }

  /** 丢弃：pending 行已在服务端，按 id 删掉（热量没有删除端点，丢弃仅本地移除卡片） */
  function handleDiscard() {
    setPending(null)
  }

  async function handleManual() {
    const kcal = Number(mKcal)
    if (!Number.isFinite(kcal) || kcal <= 0) {
      setSubmitError("热量没填对")
      return
    }
    setManualBusy(true)
    setSubmitError("")
    try {
      const res = await api.addCalorie(accountId, Math.round(kcal), mNote.trim())
      setAdjustNotice(res.adjustment?.content ?? "")
      setMKcal("")
      setMNote("")
      await load()
    } catch {
      setSubmitError("录入失败，再试一次")
    } finally {
      setManualBusy(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">热量</h2>
      <p className="text-xs text-stone-500 mb-6">食物拍照估算热量，估算仅供参考</p>

      {/* 今日累计 vs 预算（有减肥目标时服务端给 budget_kcal） */}
      <section className="us-panel rounded-2xl p-5 mb-8">
        <div className="flex items-baseline justify-between mb-2">
          <p className="us-serif text-base">今日已摄入</p>
          <p className="text-sm">
            <span className="text-xl font-medium">{consumed}</span>
            <span className="text-stone-500"> kcal{budget !== null ? ` / 预算 ${budget}` : ""}</span>
          </p>
        </div>
        {budget !== null && (
          <div className="h-2 rounded-full bg-[#264653]/10 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                consumed > budget ? "bg-[#e25563]" : "bg-[#F4A261]"
              }`}
              style={{ width: `${Math.min(100, Math.round((consumed / budget) * 100))}%` }}
            />
          </div>
        )}
        {adjustNotice ? (
          <p className="text-xs text-[#e25563] mt-2 leading-relaxed">
            {adjustNotice}（已加到今日计划）
          </p>
        ) : (
          budget !== null &&
          consumed > budget && (
            <p className="text-xs text-[#e25563] mt-2">超预算了，今日计划里会加一条调整条目</p>
          )
        )}
      </section>

      {/* 拍照识别（可补一句文字描述提准） */}
      <section className="mb-8">
        <p className="us-serif text-base mb-3">拍照估算</p>
        <div className="flex items-center gap-3 flex-wrap">
          <ImagePicker image={image} onChange={setImage} />
          <input
            className="us-input flex-1 min-w-40"
            placeholder="补一句描述更准，如「红烧肉一碗约 300g」（可空）"
            value={hint}
            onChange={(e) => setHint(e.target.value)}
          />
          <button className="us-btn" disabled={!image || recBusy} onClick={handleRecognize}>
            {recBusy ? "识别中…" : "开始识别"}
          </button>
        </div>
        {recError && <p className="text-xs text-red-500 mt-2">{recError}</p>}

        {/* 待确认卡：菜品明细 + 总热量可改 + 运动等效 + 免责标注 */}
        {pending && (
          <div className="us-rise bg-white/60 rounded-2xl p-5 mt-4">
            <p className="text-xs text-stone-500 mb-3">待确认（数字可改，确认后才入账）：</p>
            {pending.items.length > 0 && (
              <div className="flex flex-col gap-1.5 mb-3">
                {pending.items.map((x, i) => (
                  <div key={i} className="flex justify-between text-sm">
                    <span>{x.name}</span>
                    <span className="text-stone-500">{Math.round(x.kcal)} kcal</span>
                  </div>
                ))}
              </div>
            )}
            <div className="flex items-baseline gap-2 mb-3">
              <span className="text-sm text-stone-500">总热量</span>
              <input
                className="us-input text-xl w-28"
                type="number"
                value={pending.total}
                onChange={(e) => setPending({ ...pending, total: e.target.value })}
              />
              <span className="text-sm text-stone-500">kcal</span>
            </div>
            {equivList(pending.exercise_equiv).length > 0 && (
              <p className="text-xs text-stone-500 mb-3 leading-relaxed">
                吃掉这些 ≈ {equivList(pending.exercise_equiv).map(equivText).join(" / ")}
              </p>
            )}
            <p className="text-xs text-stone-400 mb-3">估算仅供参考，误差可能不小</p>
            <div className="flex gap-3">
              <button className="us-btn" disabled={confirmBusy} onClick={handleConfirm}>
                {confirmBusy ? "入账中…" : "确认入账"}
              </button>
              <button className="us-btn-ghost" disabled={confirmBusy} onClick={handleDiscard}>
                丢弃
              </button>
            </div>
          </div>
        )}
      </section>

      {/* 手动录入 */}
      <section className="mb-10">
        <p className="us-serif text-base mb-3">手动录入</p>
        <div className="flex gap-3 items-center flex-wrap">
          <input
            className="us-input w-32"
            type="number"
            placeholder="热量（kcal）"
            value={mKcal}
            onChange={(e) => setMKcal(e.target.value)}
          />
          <input
            className="us-input flex-1 min-w-32"
            placeholder="备注（可空），如「午饭」"
            value={mNote}
            onChange={(e) => setMNote(e.target.value)}
          />
          <button className="us-btn" disabled={manualBusy || !mKcal} onClick={handleManual}>
            {manualBusy ? "记…" : "录入"}
          </button>
        </div>
        {submitError && <p className="text-xs text-red-500 mt-2">{submitError}</p>}
      </section>

      {/* 今日记录 */}
      <section>
        <h3 className="us-serif text-lg mb-3">今日记录</h3>
        {entries.length === 0 ? (
          <p className="text-sm text-stone-400">
            {loaded ? "今天还没有记录，拍一张？" : "加载中…"}
          </p>
        ) : (
          <div className="flex flex-col">
            {entries.map((e, i) => (
              <div
                key={e.id}
                className="us-rise py-3 border-b border-[#264653]/10"
                style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }}
              >
                <div className="flex items-baseline gap-3">
                  <span className="text-base font-medium shrink-0">
                    {Math.round(e.total_kcal)} kcal
                  </span>
                  <span className="text-sm text-stone-500 flex-1 leading-relaxed">
                    {e.items.length > 0
                      ? e.items.map((x) => x.name).join("、")
                      : e.note || "手动录入"}
                  </span>
                </div>
                {equivList(e.exercise_equiv).length > 0 && (
                  <p className="text-xs text-stone-400 mt-1">
                    ≈ {equivList(e.exercise_equiv).map(equivText).join(" / ")}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
