import { useCallback, useEffect, useState } from "react"
import {
  api,
  type CalorieEntry,
  type ExerciseEquiv,
  type StagingRow,
} from "@/lib/api"
import ImagePicker, { type PickedImage } from "@/components/ImagePicker"

function todayLocal() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

function shiftDay(day: string, delta: number): string {
  const d = new Date(day + "T00:00:00")
  d.setDate(d.getDate() + delta)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

/** 识别纠正记录管理（名字/克数）：纠正错了可删——删了下次识别不再生效 */
function CorrectionsPanel({ accountId }: { accountId: string }) {
  const [open, setOpen] = useState(false)
  const [names, setNames] = useState<{ id: string; recognized_name: string; corrected_name: string; created_at: string }[]>([])
  const [grams, setGrams] = useState<{ id: string; name: string; ai_grams: number; user_grams: number; created_at: string }[]>([])

  async function loadCorrections() {
    try {
      const res = await api.listCalorieCorrections(accountId)
      setNames(res.names)
      setGrams(res.grams)
    } catch {
      /* 静默 */
    }
  }

  async function remove(kind: "name" | "gram", id: string) {
    try {
      await api.deleteCalorieCorrection(kind, id, accountId)
      await loadCorrections()
    } catch {
      /* 静默 */
    }
  }

  return (
    <section className="mt-10">
      <button
        className="us-btn-ghost text-xs"
        onClick={() => {
          const next = !open
          setOpen(next)
          if (next) loadCorrections()
        }}
      >
        {open ? "收起" : "我的识别纠正记录"}（{open ? "" : "点我查看"}）
      </button>
      {open && (
        <div className="mt-3 flex flex-col gap-2">
          {names.length === 0 && grams.length === 0 && (
            <p className="text-xs text-stone-400">还没有纠正记录（改菜名/改克数时会自动记下）</p>
          )}
          {names.map((c) => (
            <div key={c.id} className="flex items-center justify-between text-sm border-b border-[#264653]/8 pb-2">
              <span className="text-stone-600">
                「{c.recognized_name}」→「{c.corrected_name}」
                <span className="text-xs text-stone-400 ml-2">{c.created_at.slice(0, 10)}</span>
              </span>
              <button className="us-btn-ghost text-xs text-stone-400" onClick={() => remove("name", c.id)}>
                删除
              </button>
            </div>
          ))}
          {grams.map((c) => (
            <div key={c.id} className="flex items-center justify-between text-sm border-b border-[#264653]/8 pb-2">
              <span className="text-stone-600">
                {c.name}：{c.ai_grams}g → {c.user_grams}g
                <span className="text-xs text-stone-400 ml-2">{c.created_at.slice(0, 10)}</span>
              </span>
              <button className="us-btn-ghost text-xs text-stone-400" onClick={() => remove("gram", c.id)}>
                删除
              </button>
            </div>
          ))}
          <p className="text-xs text-stone-400 leading-relaxed mt-1">
            这些纠正会让识别越来越贴合你；纠正错了删掉即可，下次识别不再生效。
          </p>
        </div>
      )}
    </section>
  )
}

/** 共建食物库管理面板：查/改/删 staging 行（错值治理——红茶 294 这类可在这里清掉） */
function StagingPanel({ accountId, onChanged }: { accountId: string; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState("")
  const [showDeleted, setShowDeleted] = useState(false)
  const [rows, setRows] = useState<StagingRow[]>([])
  const [total, setTotal] = useState(0)
  const [msg, setMsg] = useState("")
  const [editing, setEditing] = useState<number | null>(null)
  const [editText, setEditText] = useState("")

  async function loadRows() {
    try {
      const res = await api.listStaging(accountId, query.trim() || undefined, showDeleted)
      setRows(res.items)
      setTotal(res.total)
    } catch {
      /* 静默 */
    }
  }

  async function saveKcal(row: StagingRow) {
    const v = Number(editText)
    setEditing(null)
    if (!Number.isFinite(v) || v <= 0 || v > 1000 || v === row.kcal_per_100g) return
    try {
      await api.updateStaging(row.id, accountId, { kcal_per_100g: Math.round(v * 10) / 10 })
      setMsg(`已把「${row.name}」改为 ${Math.round(v * 10) / 10} kcal/100g`)
      await loadRows()
      onChanged()
    } catch {
      setMsg("没改成，再试一次")
    }
  }

  async function toggleVerified(row: StagingRow) {
    try {
      await api.updateStaging(row.id, accountId, { verified: !row.verified })
      await loadRows()
    } catch {
      setMsg("没改成，再试一次")
    }
  }

  async function remove(row: StagingRow) {
    if (!window.confirm(`删掉「${row.name}」？删后识别不再用它计价（可重新收录）`)) return
    try {
      await api.deleteStaging(row.id, accountId)
      setMsg(`已删「${row.name}」`)
      await loadRows()
      onChanged()
    } catch {
      setMsg("删除失败，再试一次")
    }
  }

  return (
    <section className="mt-4">
      <button
        className="us-btn-ghost text-xs"
        onClick={() => {
          const next = !open
          setOpen(next)
          if (next) loadRows()
        }}
      >
        {open ? "收起" : "共建食物库管理"}（{open ? "" : "错值可在这里改/删"}）
      </button>
      {open && (
        <div className="mt-3 flex flex-col gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <input
              className="us-input flex-1 min-w-32 !py-1 !text-xs"
              placeholder="搜食物名/品牌…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) void loadRows()
              }}
            />
            <button className="us-btn-ghost text-xs" onClick={() => void loadRows()}>搜索</button>
            <label className="text-xs text-stone-500 flex items-center gap-1">
              <input type="checkbox" checked={showDeleted}
                     onChange={(e) => setShowDeleted(e.target.checked)} />
              含已删
            </label>
            <span className="text-xs text-stone-400">共 {total} 条</span>
          </div>
          {rows.length === 0 && (
            <p className="text-xs text-stone-400">
              {query ? "没搜到" : "共建库还是空的（识别联网入库/手动收录会进来）"}
            </p>
          )}
          {rows.map((r) => (
            <div key={r.id} className="flex items-center justify-between text-sm border-b border-[#264653]/8 pb-2 gap-2 flex-wrap">
              <span className="text-stone-600">
                {r.brand ? `${r.brand}·` : ""}{r.name}
                {editing === r.id ? (
                  <input
                    autoFocus
                    type="number"
                    className="us-input w-20 !py-0.5 !text-xs ml-1"
                    value={editText}
                    onChange={(e) => setEditText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.nativeEvent.isComposing) void saveKcal(r)
                      if (e.key === "Escape") setEditing(null)
                    }}
                    onBlur={() => void saveKcal(r)}
                  />
                ) : (
                  <button
                    className="ml-1 underline decoration-dotted underline-offset-4 hover:text-[#264653] transition-colors"
                    title="点我改每 100g 热量（错值治理）"
                    onClick={() => {
                      setEditing(r.id)
                      setEditText(String(r.kcal_per_100g))
                    }}
                  >
                    {r.kcal_per_100g} kcal/100g
                  </button>
                )}
                <span className={`ml-1 text-[10px] border rounded px-1 align-middle ${
                  r.deleted ? "text-stone-400 border-stone-300"
                  : r.verified ? "text-[#2A9D8F] border-[#2A9D8F]/40"
                  : "text-[#F4A261] border-[#F4A261]/50"}`}>
                  {r.deleted ? "已删" : r.verified ? "已核验" : "待核实"}
                </span>
                <span className="ml-1 text-[10px] text-stone-400">
                  {r.source === "web" ? "联网" : r.source === "user" ? "手填" : r.source}
                  {r.approvals ? ` · 认可 ${r.approvals}` : ""}
                </span>
              </span>
              <span className="flex gap-2 shrink-0">
                <button className="us-btn-ghost text-xs text-stone-400"
                        onClick={() => void toggleVerified(r)}>
                  {r.verified ? "标待核实" : "标已核验"}
                </button>
                <button className="us-btn-ghost text-xs text-stone-400"
                        onClick={() => void remove(r)}>
                  删除
                </button>
              </span>
            </div>
          ))}
          <p className="text-xs text-stone-400 leading-relaxed mt-1">
            共建库是全局共享的：联网搜到的错值（比如把 kJ 当成 kcal 的离谱数）在这里改掉或删掉，
            大家的识别都会跟着准。
          </p>
          {msg && <p className="text-xs text-stone-500">{msg}</p>}
        </div>
      )}
    </section>
  )
}

/** 运动等效文案：「跑步（8公里/小时）45 分钟」 */
function equivText(v: ExerciseEquiv) {
  return `${v.name} ${Math.round(v.minutes)} 分钟`
}

function equivList(eq?: Record<string, ExerciseEquiv>): ExerciseEquiv[] {
  return Object.values(eq ?? {}).filter((v) => v && typeof v.minutes === "number" && v.minutes > 0)
}

/** 菜名内联编辑：点名字进编辑态，Enter/失焦保存（服务端重走匹配链重算热量并记名字纠正） */
function NameField({ name, onSave }: { name: string; onSave: (n: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)

  async function save() {
    const n = text.trim()
    setEditing(false)
    if (busy || !n || n === name) return
    setBusy(true)
    try {
      await onSave(n)
    } finally {
      setBusy(false)
    }
  }

  if (!editing) {
    return (
      <button
        className="text-left underline decoration-dotted underline-offset-4 decoration-transparent hover:decoration-[#264653]/50 transition-colors"
        title="识别错了？点我改名"
        onClick={() => {
          setText(name)
          setEditing(true)
        }}
      >
        {name}
      </button>
    )
  }
  return (
    <input
      autoFocus
      className="us-input w-28 !py-0.5 !text-xs"
      value={text}
      disabled={busy}
      onChange={(e) => setText(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !e.nativeEvent.isComposing) void save()
        if (e.key === "Escape") setEditing(false)
      }}
      onBlur={() => void save()}
    />
  )
}

/** 克数内联编辑：点数字进编辑态，Enter/失焦保存（服务端按 kcal_per_100g 重算并记纠正） */
function GramsField({ grams, onSave }: { grams: number; onSave: (g: number) => Promise<void> }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)

  async function save() {
    const g = Number(text)
    setEditing(false)
    if (busy || !Number.isFinite(g) || g <= 0 || g > 5000 || Math.round(g) === grams) return
    setBusy(true)
    try {
      await onSave(g)
    } finally {
      setBusy(false)
    }
  }

  if (!editing) {
    return (
      <button
        className="text-stone-400 underline decoration-dotted underline-offset-4 hover:text-[#264653] transition-colors"
        title="点我改克数（改完会帮下次估得更准）"
        onClick={() => {
          setText(String(grams))
          setEditing(true)
        }}
      >
        {grams}g
      </button>
    )
  }
  return (
    <span className="inline-flex items-baseline gap-0.5">
      <input
        autoFocus
        type="number"
        className="us-input w-20 !py-0.5 !text-xs"
        value={text}
        disabled={busy}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.nativeEvent.isComposing) void save()
          if (e.key === "Escape") setEditing(false)
        }}
        onBlur={() => void save()}
      />
      <span className="text-xs text-stone-400">g</span>
    </span>
  )
}

/** 待确认卡（识别时已落库 pending 行，带 id；确认时只回传 id + 可改的总热量/备注） */
interface PendingEntry {
  id: string
  items: { name: string; kcal: number; brand?: string; grams?: number; source?: string; staging_id?: number; confidence?: number }[]
  total: string
  note: string
  exercise_equiv: Record<string, ExerciseEquiv>
}

export default function Calories({ accountId }: { accountId: string }) {
  const today = todayLocal()
  const [day, setDay] = useState(today)  // 查看的热量记录日期（默认今天，可翻历史）
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
  const [mFood, setMFood] = useState("")
  const [mGrams, setMGrams] = useState("")
  const [mKcal, setMKcal] = useState("")
  const [mNote, setMNote] = useState("")
  const [manualBusy, setManualBusy] = useState(false)
  const [submitError, setSubmitError] = useState("")
  // 食物名即时匹配结果（查正式表/共建库，只读）
  const [lookup, setLookup] = useState<
    { found: true; name: string; kcal_per_100g: number; source?: string } | { found: false } | null
  >(null)

  // 食物名/克数变化后防抖查库：命中且给了克数 → 自动算总热量填入（仍可手改）
  useEffect(() => {
    const name = mFood.trim()
    if (!name) {
      setLookup(null)
      return
    }
    const t = setTimeout(async () => {
      try {
        const g = Number(mGrams)
        const res = await api.lookupCalorieFood(accountId, name, g > 0 ? g : undefined)
        if (res.found) {
          setLookup({ found: true, name: res.name ?? name, kcal_per_100g: res.kcal_per_100g ?? 0, source: res.source })
          if (g > 0 && res.kcal) setMKcal(String(res.kcal))
        } else {
          setLookup({ found: false })
        }
      } catch {
        setLookup(null)
      }
    }, 500)
    return () => clearTimeout(t)
  }, [mFood, mGrams, accountId])
  // 手动加食物（营养共建：入 staging 预数据库，后台联网核验）
  const [fName, setFName] = useState("")
  const [fBrand, setFBrand] = useState("")
  const [fKcal, setFKcal] = useState("")
  const [fPortion, setFPortion] = useState("")
  const [foodBusy, setFoodBusy] = useState(false)
  const [foodMsg, setFoodMsg] = useState("")
  // 超预算联动提示（服务端确认/手动入账后返回 adjustment）
  const [adjustNotice, setAdjustNotice] = useState("")

  const load = useCallback(async () => {
    try {
      const res = await api.listCalories(accountId, day)
      setEntries(res.items)
      setConsumed(Math.round(res.consumed_kcal))
      setBudget(res.budget_kcal ?? null)
    } catch {
      /* 静默，保持现状 */
    }
    setLoaded(true)
  }, [accountId, day])

  useEffect(() => {
    load()
  }, [load])

  // SSE：后台联网入库完成（staging_ready）/ 共建库治理（staging_updated）→ 自动刷新列表，
  // 「有查表数据，更新为 X kcal」的提示跟着出现，不用再干等手动刷新
  useEffect(() => {
    const es = new EventSource(`/api/calories/events?account_id=${accountId}`)
    es.onmessage = () => {
      if (day === todayLocal()) void load()
    }
    return () => es.close()
  }, [accountId, day, load])

  async function handleRecognize() {
    if (!image) return
    setRecBusy(true)
    setRecError("")
    try {
      const url = (await api.uploadImage(image.original, image.display, image.vision)).url
      const entry = await api.recognizeFood(accountId, url, hint.trim() || undefined)
      setPending({
        id: entry.id,
        items: (entry.items ?? []).map((x) => ({
          name: x.name,
          kcal: x.kcal,
          brand: x.brand,
          grams: x.grams,
          source: x.source,
          staging_id: x.staging_id,
          confidence: x.confidence,
        })),
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

  /** 丢弃：pending 行已在服务端落库，按 id 真删（不留僵尸行） */
  async function handleDiscard() {
    const id = pending?.id
    setPending(null)
    if (!id) return
    try {
      await api.deleteCalorie(id, accountId)
    } catch {
      /* 删不掉下次刷新也不会再出现在待确认卡（本地已移除），失败静默即可 */
    }
  }

  /** 待确认卡改条目（克数/名字）：服务端重算后整卡刷新（kcal/总热量/运动等效都变） */
  async function handlePendingPatch(index: number, patch: { grams?: number; name?: string }) {
    if (!pending) return
    try {
      const res = await api.updateCalorieItem(pending.id, accountId, index, patch)
      const e = res.entry
      setPending({
        id: e.id,
        items: e.items.map((x) => ({
          name: x.name,
          kcal: x.kcal,
          brand: x.brand,
          grams: x.grams,
          source: x.source,
          staging_id: x.staging_id,
          confidence: x.confidence,
        })),
        total: String(Math.round(e.total_kcal)),
        note: e.note || pending.note,
        exercise_equiv: e.exercise_equiv ?? {},
      })
    } catch {
      setSubmitError("没改成，再试一次")
    }
  }

  /** 已入账记录改条目（克数/名字）：重算 + 触发超预算联动，列表重载 */
  async function handleEntryPatch(entryId: string, index: number,
                                  patch: { grams?: number; name?: string }) {
    try {
      const res = await api.updateCalorieItem(entryId, accountId, index, patch)
      setAdjustNotice(res.adjustment?.content ?? "")
      await load()
    } catch {
      setSubmitError("没改成，再试一次")
    }
  }

  async function handleManual() {
    const kcal = Number(mKcal)
    if (!mKcal || !Number.isFinite(kcal) || kcal <= 0) {
      setSubmitError(
        lookup && lookup.found
          ? "补个克数就自动算出来了，或直接填热量"
          : "没查到这种食物，填一下热量（或下方收录进共建库）",
      )
      return
    }
    setManualBusy(true)
    setSubmitError("")
    try {
      const food = mFood.trim()
      const g = Number(mGrams)
      const items = food
        ? [{
            name: food,
            kcal: Math.round(kcal),
            grams: g > 0 ? g : undefined,
            kcal_per_100g: lookup && lookup.found ? lookup.kcal_per_100g : undefined,
            source: lookup && lookup.found ? lookup.source : "model",
          }]
        : undefined
      const res = await api.addCalorie(
        accountId, Math.round(kcal), mNote.trim() || food, items,
      )
      setAdjustNotice(res.adjustment?.content ?? "")
      setMFood("")
      setMGrams("")
      setMKcal("")
      setMNote("")
      setLookup(null)
      await load()
    } catch {
      setSubmitError("录入失败，再试一次")
    } finally {
      setManualBusy(false)
    }
  }

  /** 手动加食物：入共建 staging 库；分量说明以括号备注附在名称后（匹配归一时会忽略，与成分表口径一致） */
  async function handleAddFood() {
    const name = fName.trim()
    const kcal = Number(fKcal)
    if (!name) {
      setFoodMsg("食物名称没填")
      return
    }
    if (!Number.isFinite(kcal) || kcal <= 0 || kcal > 1000) {
      setFoodMsg("每 100g 热量没填对（0-1000 kcal）")
      return
    }
    setFoodBusy(true)
    setFoodMsg("")
    try {
      const portion = fPortion.trim()
      const fullName = portion ? `${name}（${portion}）` : name
      const res = await api.addFood(accountId, fullName, Math.round(kcal * 10) / 10, undefined, fBrand.trim() || undefined)
      setFoodMsg(
        res.food.verified
          ? `「${res.food.name}」已收录并通过核验`
          : `「${res.food.name}」已收录，联网核验中，暂标「待核实」`,
      )
      setFName("")
      setFBrand("")
      setFKcal("")
      setFPortion("")
    } catch {
      setFoodMsg("收录失败，再试一次")
    } finally {
      setFoodBusy(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">热量</h2>
      <p className="text-xs text-stone-500 mb-6">食物拍照估算热量，估算仅供参考</p>

      {/* 当日累计 vs 预算（有减肥目标时服务端给 budget_kcal） */}
      <section className="us-panel rounded-2xl p-5 mb-8">
        <div className="flex items-baseline justify-between mb-2">
          <p className="us-serif text-base">{day === today ? "今日" : `${day} `}已摄入</p>
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
                    <span>
                      {x.brand ? <span className="text-[#264653]/70">{x.brand}·</span> : null}
                      <NameField name={x.name} onSave={(n) => handlePendingPatch(i, { name: n })} />
                      {x.grams ? (
                        <>
                          {" "}
                          <GramsField grams={x.grams}
                                      onSave={(g) => handlePendingPatch(i, { grams: g })} />
                        </>
                      ) : null}
                      {x.source === "table" && (
                        <span className="ml-1 text-[10px] text-[#2A9D8F] border border-[#2A9D8F]/40 rounded px-1 align-middle">
                          查表
                        </span>
                      )}
                      {x.source === "staging" && (
                        <span className="ml-1 text-[10px] text-[#F4A261] border border-[#F4A261]/50 rounded px-1 align-middle">
                          待核实
                        </span>
                      )}
                      {x.source === "web_pending" && (
                        <span className="ml-1 text-[10px] text-[#E76F51] border border-[#E76F51]/50 rounded px-1 align-middle">
                          待认可
                        </span>
                      )}
                      {x.source === "model" && (
                        <span className="ml-1 text-[10px] text-stone-400 border border-stone-300 rounded px-1 align-middle">
                          估值
                        </span>
                      )}
                      {x.source === "image_rag" && (
                        <span className="ml-1 text-[10px] text-[#2A9D8F] border border-[#2A9D8F]/40 rounded px-1 align-middle">
                          图库
                        </span>
                      )}
                      {x.confidence != null && x.confidence < 0.5 && (
                        <span
                          className="ml-1 text-[10px] text-[#E76F51] border border-[#E76F51]/50 rounded px-1 align-middle"
                          title="这项把握不大：点名字/克数帮忙改一下，改完下次更准"
                        >
                          没把握
                        </span>
                      )}
                    </span>
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
            <p className="text-xs text-stone-400 mb-3">
              估算仅供参考；名字和克数点一下就能改，改完会帮下次估得更准
            </p>
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

      {/* 手动录入：食物名 + 克数 + 热量（可空，查表命中自动填） */}
      <section className="mb-10">
        <p className="us-serif text-base mb-3">手动录入</p>
        <div className="flex gap-3 items-center flex-wrap">
          <input
            className="us-input flex-1 min-w-32"
            placeholder="食物名，如「米饭」"
            value={mFood}
            onChange={(e) => setMFood(e.target.value)}
          />
          <input
            className="us-input w-24"
            type="number"
            placeholder="克数 g"
            value={mGrams}
            onChange={(e) => setMGrams(e.target.value)}
          />
          <input
            className="us-input w-32"
            type="number"
            placeholder="热量（kcal）"
            value={mKcal}
            onChange={(e) => setMKcal(e.target.value)}
          />
          <input
            className="us-input flex-1 min-w-28"
            placeholder="备注（可空），如「午饭」"
            value={mNote}
            onChange={(e) => setMNote(e.target.value)}
          />
          <button className="us-btn" disabled={manualBusy || !mKcal} onClick={handleManual}>
            {manualBusy ? "记…" : "录入"}
          </button>
        </div>
        {lookup && (
          <p className="text-xs mt-2 leading-relaxed">
            {lookup.found ? (
              <span className="text-[#2A9D8F]">
                查到「{lookup.name}」：每 100g 约 {lookup.kcal_per_100g} kcal
                {lookup.source === "staging" ? "（共建库，待核实）" : "（成分表）"}
                {Number(mGrams) > 0 ? "，已按克数自动算好热量" : "，填上克数自动算热量"}
              </span>
            ) : (
              <span className="text-stone-400">
                库里没有「{mFood.trim()}」，手填热量即可，也可以下方收录进共建库
              </span>
            )}
          </p>
        )}
        {submitError && <p className="text-xs text-red-500 mt-2">{submitError}</p>}
      </section>

      {/* 手动加食物（营养共建：入 staging 库，后台联网核验，满 3 人认可进正式成分表） */}
      <section className="mb-10">
        <p className="us-serif text-base mb-3">手动加食物</p>
        <div className="flex gap-3 items-center flex-wrap">
          <input
            className="us-input flex-1 min-w-32"
            placeholder="食物名称，如「火鸡面」"
            value={fName}
            onChange={(e) => setFName(e.target.value)}
          />
          <input
            className="us-input w-28"
            placeholder="品牌（可空），如「三养」"
            value={fBrand}
            onChange={(e) => setFBrand(e.target.value)}
          />
          <input
            className="us-input w-36"
            type="number"
            placeholder="每 100g 热量（kcal）"
            value={fKcal}
            onChange={(e) => setFKcal(e.target.value)}
          />
          <input
            className="us-input flex-1 min-w-32"
            placeholder="分量说明（可空），如「一小碗约 150g」"
            value={fPortion}
            onChange={(e) => setFPortion(e.target.value)}
          />
          <button className="us-btn" disabled={foodBusy || !fName.trim() || !fKcal} onClick={handleAddFood}>
            {foodBusy ? "收录中…" : "收录"}
          </button>
        </div>
        <p className="text-xs text-stone-400 mt-2">
          收录进共建库后会联网核验，核验通过与大家的认可积累到 3 次后会进入正式成分表
        </p>
        {foodMsg && <p className="text-xs text-stone-500 mt-1">{foodMsg}</p>}
      </section>

      {/* 当日记录：可翻历史（← 前一天 / 后一天 →），每条可删 */}
      <section>
        <div className="flex items-center justify-between mb-3 gap-2 flex-wrap">
          <h3 className="us-serif text-lg">{day === today ? "今日记录" : "当日记录"}</h3>
          <div className="flex items-center gap-2 text-sm">
            <button className="us-btn-ghost text-xs" onClick={() => setDay(shiftDay(day, -1))}>
              ← 前一天
            </button>
            <input
              type="date"
              className="us-input !py-0.5 !text-xs w-36"
              value={day}
              max={today}
              onChange={(e) => {
                if (e.target.value) setDay(e.target.value)
              }}
            />
            <button
              className="us-btn-ghost text-xs"
              disabled={day >= today}
              onClick={() => setDay(shiftDay(day, 1))}
            >
              后一天 →
            </button>
            {day !== today && (
              <button className="us-btn-ghost text-xs" onClick={() => setDay(today)}>
                回到今天
              </button>
            )}
          </div>
        </div>
        {entries.length === 0 ? (
          <p className="text-sm text-stone-400">
            {loaded ? (day === today ? "今天还没有记录，拍一张？" : "这一天没有记录") : "加载中…"}
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
                  <button
                    className="us-btn-ghost text-xs text-stone-500 hover:text-[#e25563] shrink-0"
                    title="删掉这条记录（历史某天录错也能删）"
                    onClick={async () => {
                      if (!window.confirm("删掉这条热量记录？")) return
                      try {
                        await api.deleteCalorie(e.id, accountId)
                        await load()
                      } catch {
                        setSubmitError("删除失败，再试一次")
                      }
                    }}
                  >
                    删除
                  </button>
                </div>
                {/* 有菜品明细（拍照识别/手动带名）时逐条列出，名字与克数可点改（重算并记纠正） */}
                {e.items.some((x) => x.grams) && (
                  <div className="flex flex-col gap-0.5 mt-1 pl-1">
                    {e.items.map((x, xi) => (
                      <p key={xi} className="text-xs text-stone-500">
                        {x.brand ? `${x.brand}·` : ""}
                        <NameField name={x.name}
                                   onSave={(n) => handleEntryPatch(e.id, xi, { name: n })} />
                        {x.grams ? (
                          <>
                            {" "}
                            <GramsField
                              grams={x.grams}
                              onSave={(g) => handleEntryPatch(e.id, xi, { grams: g })}
                            />
                          </>
                        ) : null}
                        <span className="text-stone-400"> · {Math.round(x.kcal)} kcal</span>
                        {x.source === "model" && (
                          <span className="ml-1 text-[10px] text-stone-400 border border-stone-300 rounded px-1 align-middle">
                            估值
                          </span>
                        )}
                        {x.source === "image_rag" && (
                          <span className="ml-1 text-[10px] text-[#2A9D8F] border border-[#2A9D8F]/40 rounded px-1 align-middle">
                            图库
                          </span>
                        )}
                        {x.upgrade && (
                          <button
                            className="ml-1 text-[10px] font-medium text-[#E76F51] bg-[#F4A261]/15 border border-[#F4A261]/70 rounded px-1.5 py-0.5 align-middle hover:bg-[#F4A261]/30 transition-colors"
                            title={`联网查到了：每 100g 约 ${x.upgrade.kcal_per_100g} kcal，点击按它重算`}
                            onClick={() => handleEntryPatch(e.id, xi, { name: x.name })}
                          >
                            ↻ 联网数据：更新为 {x.upgrade.kcal} kcal
                          </button>
                        )}
                      </p>
                    ))}
                  </div>
                )}
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

      {/* 识别纠正记录（纠正错了可删）+ 共建食物库管理（错值治理），默认收起 */}
      <div className="mt-2">
        <CorrectionsPanel accountId={accountId} />
        <StagingPanel accountId={accountId} onChanged={() => void load()} />
      </div>
    </div>
  )
}
