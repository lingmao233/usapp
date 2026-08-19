import { useCallback, useEffect, useState } from "react"
import { api, type Expense } from "@/lib/api"
import ImagePicker, { type PickedImage } from "@/components/ImagePicker"

/** 预设类目，与服务端 EXPENSE_CATEGORIES 一致（不认得的会被服务端归「其他」） */
const CATEGORIES = [
  "餐饮",
  "交通",
  "购物",
  "居住",
  "娱乐",
  "医疗",
  "教育",
  "人情",
  "通讯",
  "其他",
]

function todayLocal() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
}

function monthOf(date: string) {
  return date.slice(0, 7)
}

function shiftMonth(month: string, delta: number) {
  const [y, m] = month.split("-").map(Number)
  const d = new Date(y, m - 1 + delta, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}

const fenToYuan = (fen: number) => (Math.abs(fen) / 100).toFixed(2)
/** 待确认卡金额保留符号（识别出的收入是负数，确认时原样回传） */
const fenToSignedYuan = (fen: number) => (fen / 100).toFixed(2)
const yuanToFen = (s: string) => Math.round(Number(s) * 100)

/** 待确认条目（识别时已落库 pending 行，带 id；确认/丢弃都按 id 调服务端） */
interface PendingExpense {
  id: string
  checked: boolean
  amount: string
  category: string
  merchant: string
  note: string
  spent_at: string
}

export default function Ledger({ accountId }: { accountId: string }) {
  const [month, setMonth] = useState(monthOf(todayLocal()))
  const [expenses, setExpenses] = useState<Expense[]>([])
  const [totalFen, setTotalFen] = useState(0)
  const [budgetFen, setBudgetFen] = useState<number | null>(null)
  const [loaded, setLoaded] = useState(false)
  // 拍照识别
  const [image, setImage] = useState<PickedImage | null>(null)
  const [recBusy, setRecBusy] = useState(false)
  const [recError, setRecError] = useState("")
  const [pending, setPending] = useState<PendingExpense[]>([])
  const [confirmBusy, setConfirmBusy] = useState(false)
  // 手动记一笔
  const [mAmount, setMAmount] = useState("")
  const [mCategory, setMCategory] = useState(CATEGORIES[0])
  const [mMerchant, setMMerchant] = useState("")
  const [mNote, setMNote] = useState("")
  const [mDate, setMDate] = useState(todayLocal())
  const [manualBusy, setManualBusy] = useState(false)
  const [submitError, setSubmitError] = useState("")
  // 分类筛选：null = 全部
  const [catFilter, setCatFilter] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await api.listExpenses(accountId, month)
      setExpenses(res.items)
      setTotalFen(res.month_total_fen)
      setBudgetFen(res.monthly_spendable_fen ?? null)
    } catch {
      /* 静默，保持现状 */
    }
    setLoaded(true)
  }, [accountId, month])

  useEffect(() => {
    setLoaded(false)
    load()
  }, [load])

  async function handleRecognize() {
    if (!image) return
    setRecBusy(true)
    setRecError("")
    try {
      // 识别只需要压缩后的 1600px JPEG；不再把手机原图重复上传。
      const url = (await api.uploadImage(image.display)).url
      const list = await api.recognizeReceipt(accountId, url)
      const today = todayLocal()
      setPending(
        list.map((e) => ({
          id: e.id,
          checked: true,
          amount: fenToSignedYuan(e.amount_fen || 0),
          category: CATEGORIES.includes(e.category) ? e.category : "其他",
          merchant: e.merchant ?? "",
          note: e.note ?? "",
          spent_at: (e.spent_at || today).slice(0, 10),
        })),
      )
      URL.revokeObjectURL(image.preview)
      setImage(null)
      if (list.length === 0) setRecError("没识别出账目，换张更清晰的图，或手动录入")
    } catch (e) {
      const msg = e instanceof Error ? e.message : ""
      setRecError(
        msg.includes("400") || msg.includes("视觉模型") || msg.includes("没识别出")
          ? msg.includes("没识别出")
            ? "没识别出有效账目，请手动录入"
            : "未配置视觉模型，请手动录入"
          : msg || "识别失败，再试一次",
      )
    } finally {
      setRecBusy(false)
    }
  }

  function patchPending(id: string, patch: Partial<PendingExpense>) {
    setPending((xs) => xs.map((x) => (x.id === id ? { ...x, ...patch } : x)))
  }

  /** 确认勾选（逐笔带 id 确认，可同时改字段）；未勾选的 pending 行删掉，不留垃圾数据 */
  async function handleConfirm() {
    const chosen = pending.filter((p) => p.checked)
    const dropped = pending.filter((p) => !p.checked)
    for (const p of chosen) {
      const fen = yuanToFen(p.amount || "0")
      if (!Number.isFinite(fen) || fen === 0) {
        setSubmitError("有金额没填对，检查一下")
        return
      }
    }
    setConfirmBusy(true)
    setSubmitError("")
    try {
      for (const p of chosen) {
        await api.confirmExpense(p.id, accountId, {
          amount_fen: yuanToFen(p.amount),
          category: p.category,
          merchant: p.merchant.trim(),
          note: p.note.trim(),
          spent_at: p.spent_at,
        })
      }
      for (const p of dropped) {
        await api.deleteExpense(p.id, accountId).catch(() => {})
      }
      setPending([])
      await load()
    } catch {
      setSubmitError("入账失败，再试一次")
    } finally {
      setConfirmBusy(false)
    }
  }

  /** 全部丢弃：pending 行已在服务端，逐笔删掉 */
  async function handleDiscardAll() {
    const ids = pending.map((p) => p.id)
    setPending([])
    for (const id of ids) {
      await api.deleteExpense(id, accountId).catch(() => {})
    }
  }

  async function handleManual() {
    const fen = yuanToFen(mAmount || "0")
    if (!Number.isFinite(fen) || fen <= 0) {
      setSubmitError("金额没填对")
      return
    }
    setManualBusy(true)
    setSubmitError("")
    try {
      await api.addExpense(accountId, {
        amount_fen: fen,
        category: mCategory,
        merchant: mMerchant.trim(),
        note: mNote.trim(),
        spent_at: mDate || todayLocal(),
      })
      setMAmount("")
      setMMerchant("")
      setMNote("")
      // 手动记的月份可能不是当前查看的月份，切过去
      if (monthOf(mDate) !== month) setMonth(monthOf(mDate))
      else await load()
    } catch {
      setSubmitError("记账失败，再试一次")
    } finally {
      setManualBusy(false)
    }
  }

  async function handleDelete(e: Expense) {
    try {
      await api.deleteExpense(e.id, accountId)
      setExpenses((xs) => xs.filter((x) => x.id !== e.id))
    } catch {
      /* 失败保持现状，用户可重试 */
    }
  }

  const byCategory = new Map<string, number>()
  expenses.forEach((e) => {
    if (e.amount_fen > 0) {
      byCategory.set(e.category, (byCategory.get(e.category) ?? 0) + e.amount_fen)
    }
  })
  const categoryRows = [...byCategory.entries()].sort((a, b) => b[1] - a[1])
  // 服务端已按 spent_at 倒序；分类筛选在客户端做（整月数据已在手）
  const sorted = catFilter ? expenses.filter((e) => (e.category || "其他") === catFilter) : expenses

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">记账</h2>
      <p className="text-xs text-stone-500 mb-6">拍小票/截图自动识别，确认后才进账本</p>

      {/* 拍照识别 */}
      <section className="us-panel rounded-2xl p-5 mb-8">
        <p className="us-serif text-base mb-3">拍照识别</p>
        <div className="flex items-center gap-3 flex-wrap">
          <ImagePicker image={image} onChange={setImage} />
          <button className="us-btn" disabled={!image || recBusy} onClick={handleRecognize}>
            {recBusy ? "识别中…" : "开始识别"}
          </button>
          {recError && <span className="text-xs text-red-500">{recError}</span>}
        </div>

        {/* 待确认卡片：金额大字号可改、分类下拉、多笔勾选确认 */}
        {pending.length > 0 && (
          <div className="mt-4">
            <p className="text-xs text-stone-500 mb-2">待确认（改完再入账，不确认的勾掉）：</p>
            <div className="flex flex-col gap-3">
              {pending.map((p) => (
                <div key={p.id} className="bg-white/70 rounded-xl p-4">
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      className="accent-[#264653] w-4 h-4 shrink-0"
                      checked={p.checked}
                      onChange={(e) => patchPending(p.id, { checked: e.target.checked })}
                      aria-label="确认这笔"
                    />
                    <div className="flex items-baseline gap-1">
                      <span className="text-sm text-stone-500">¥</span>
                      <input
                        className="us-input text-xl w-28"
                        type="number"
                        value={p.amount}
                        onChange={(e) => patchPending(p.id, { amount: e.target.value })}
                      />
                    </div>
                    <select
                      className="ml-auto bg-transparent text-sm text-[#264653] outline-none"
                      value={p.category}
                      onChange={(e) => patchPending(p.id, { category: e.target.value })}
                    >
                      {CATEGORIES.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="flex gap-3 mt-2 pl-7 flex-wrap">
                    <input
                      className="us-input text-sm flex-1 min-w-24"
                      placeholder="商户"
                      value={p.merchant}
                      onChange={(e) => patchPending(p.id, { merchant: e.target.value })}
                    />
                    <input
                      className="us-input text-sm flex-1 min-w-24"
                      placeholder="备注"
                      value={p.note}
                      onChange={(e) => patchPending(p.id, { note: e.target.value })}
                    />
                    <input
                      className="us-input text-sm w-32"
                      type="date"
                      value={p.spent_at}
                      onChange={(e) => patchPending(p.id, { spent_at: e.target.value })}
                    />
                  </div>
                </div>
              ))}
            </div>
            <div className="flex gap-3 mt-3">
              <button className="us-btn" disabled={confirmBusy} onClick={handleConfirm}>
                {confirmBusy ? "入账中…" : `确认入账（${pending.filter((p) => p.checked).length} 笔）`}
              </button>
              <button className="us-btn-ghost" disabled={confirmBusy} onClick={handleDiscardAll}>
                全部丢弃
              </button>
            </div>
          </div>
        )}
      </section>

      {/* 手动记一笔 */}
      <section className="mb-10">
        <p className="us-serif text-base mb-3">手动记一笔</p>
        <div className="flex gap-3 items-center flex-wrap">
          <input
            className="us-input w-28"
            type="number"
            placeholder="金额（元）"
            value={mAmount}
            onChange={(e) => setMAmount(e.target.value)}
          />
          <select
            className="bg-transparent text-sm text-[#264653] outline-none"
            value={mCategory}
            onChange={(e) => setMCategory(e.target.value)}
          >
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <input
            className="us-input flex-1 min-w-24"
            placeholder="商户（可空）"
            value={mMerchant}
            onChange={(e) => setMMerchant(e.target.value)}
          />
          <input
            className="us-input flex-1 min-w-24"
            placeholder="备注（可空）"
            value={mNote}
            onChange={(e) => setMNote(e.target.value)}
          />
          <input
            className="us-input w-36"
            type="date"
            value={mDate}
            onChange={(e) => setMDate(e.target.value)}
          />
          <button className="us-btn" disabled={manualBusy || !mAmount} onClick={handleManual}>
            {manualBusy ? "记…" : "记账"}
          </button>
        </div>
        {submitError && <p className="text-xs text-red-500 mt-2">{submitError}</p>}
      </section>

      {/* 月视图：月份切换 + 总额 + 存款预算进度 + 分类小计 + 明细 */}
      <section>
        <div className="flex items-center justify-between gap-2 mb-3">
          <button className="us-btn-ghost text-sm shrink-0" onClick={() => setMonth(shiftMonth(month, -1))}>
            ← 上月
          </button>
          <div className="flex items-center gap-2 min-w-0">
            {/* 原生月份选择器：可直接跳任意年/月，移动端友好 */}
            <input
              type="month"
              className="us-input !w-auto !py-1 px-2 text-sm"
              value={month}
              onChange={(e) => e.target.value && setMonth(e.target.value)}
              aria-label="选择月份"
            />
            <span className="us-serif text-lg whitespace-nowrap shrink-0">
              共 ¥{fenToYuan(totalFen)}
            </span>
          </div>
          <button className="us-btn-ghost text-sm shrink-0" onClick={() => setMonth(shiftMonth(month, 1))}>
            下月 →
          </button>
        </div>

        {budgetFen !== null && (
          <div className="bg-white/60 rounded-2xl p-4 mb-4">
            <div className="flex items-baseline justify-between text-sm mb-2">
              <span className="text-stone-500">本月已花 / 存款目标预算</span>
              <span>
                ¥{fenToYuan(totalFen)} / ¥{fenToYuan(budgetFen)}
              </span>
            </div>
            <div className="h-2 rounded-full bg-[#264653]/10 overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  totalFen > budgetFen ? "bg-[#e25563]" : "bg-[#F4A261]"
                }`}
                style={{ width: `${Math.min(100, Math.round((totalFen / budgetFen) * 100))}%` }}
              />
            </div>
          </div>
        )}

        {categoryRows.length > 0 && (
          <div className="flex gap-2 flex-wrap mb-4">
            {/* 分类小计即筛选器：点 chip 只看该类，再点或点「全部」还原 */}
            <button
              className={`us-chip transition-colors ${
                catFilter === null ? "bg-[#161616] text-white" : ""
              }`}
              onClick={() => setCatFilter(null)}
            >
              全部
            </button>
            {categoryRows.map(([c, fen]) => (
              <button
                key={c}
                className={`us-chip transition-colors ${
                  catFilter === c ? "bg-[#161616] text-white" : ""
                }`}
                onClick={() => setCatFilter(catFilter === c ? null : c)}
              >
                {c} ¥{fenToYuan(fen)}
              </button>
            ))}
          </div>
        )}

        {sorted.length === 0 ? (
          <p className="text-sm text-stone-400 py-8 text-center">
            {loaded
              ? catFilter
                ? `这个月没有「${catFilter}」的账`
                : "这个月还没有账，记一笔？"
              : "加载中…"}
          </p>
        ) : (
          <div className="flex flex-col">
            {sorted.map((e, i) => (
              <div
                key={e.id}
                className="us-rise py-3 border-b border-[#264653]/10 flex items-center gap-3"
                style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }}
              >
                <span className="text-xs text-stone-400 w-12 shrink-0">
                  {(e.spent_at || "").slice(5, 10)}
                </span>
                <span className="us-chip shrink-0">{e.category || "其他"}</span>
                <span className="text-sm leading-relaxed flex-1 truncate">
                  {e.merchant || e.note || "—"}
                  {e.merchant && e.note && <span className="text-stone-400"> · {e.note}</span>}
                </span>
                {/* 负数=收入（存款结算口径），绿色 + 号区分 */}
                {e.amount_fen < 0 ? (
                  <span className="text-sm font-medium shrink-0 text-[#2a9d8f]">
                    +¥{fenToYuan(e.amount_fen)}
                  </span>
                ) : (
                  <span className="text-sm font-medium shrink-0">¥{fenToYuan(e.amount_fen)}</span>
                )}
                <button
                  className="text-stone-300 hover:text-stone-500 text-sm leading-none shrink-0"
                  onClick={() => handleDelete(e)}
                  aria-label="删除账目"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
