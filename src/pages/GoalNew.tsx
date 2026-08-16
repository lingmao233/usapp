import { useEffect, useState } from "react"
import { useNavigate } from "react-router"
import {
  api,
  loadAccountId,
  type AccountCircle,
  type Goal,
  type Session,
} from "@/lib/api"

type GoalType = Goal["type"]

const TYPE_CARDS: { type: GoalType; label: string; desc: string }[] = [
  { type: "weight_loss", label: "减肥", desc: "算每日热量预算，联动拍照热量记录" },
  { type: "savings", label: "存款", desc: "算每月可花预算，联动记账看花销进度" },
  { type: "study", label: "学习", desc: "按每日时长排计划，欠账次日补足" },
  { type: "custom", label: "自定义", desc: "先享受 AI 每日计划，联动后续接入" },
]

const DEFAULT_TITLE: Record<GoalType, string> = {
  weight_loss: "减肥目标",
  savings: "存款目标",
  study: "学习目标",
  custom: "",
}

interface FieldDef {
  /** 提交键名，与服务端 rules 引擎读取口径一致 */
  key: string
  label: string
  placeholder?: string
  kind?: "number" | "date" | "choice"
  options?: { value: string; label: string }[]
}

/** 问卷字段定义：全部可跳过。减肥/存款的键名对齐服务端规则引擎（rules.py） */
const TYPE_FIELDS: Record<GoalType, FieldDef[]> = {
  weight_loss: [
    { key: "weight_kg", label: "当前体重", placeholder: "kg", kind: "number" },
    { key: "target_weight_kg", label: "目标体重", placeholder: "kg", kind: "number" },
    { key: "deadline", label: "目标日期", kind: "date" },
    { key: "height_cm", label: "身高", placeholder: "cm", kind: "number" },
    { key: "age", label: "年龄", kind: "number" },
    {
      key: "sex",
      label: "性别",
      kind: "choice",
      options: [
        { value: "male", label: "男" },
        { value: "female", label: "女" },
      ],
    },
    { key: "schedule", label: "作息", placeholder: "如 23:00 睡 7:00 起" },
    {
      key: "activity",
      label: "运动基础",
      kind: "choice",
      options: [
        { value: "sedentary", label: "久坐不动" },
        { value: "light", label: "每周轻运动 1-3 次" },
        { value: "moderate", label: "每周中等运动 3-5 次" },
        { value: "active", label: "每周高强度 6-7 次" },
      ],
    },
  ],
  savings: [
    { key: "target_fen", label: "目标总额", placeholder: "元", kind: "number" },
    { key: "deadline", label: "截止日期", kind: "date" },
    { key: "fixed_income_fen", label: "固定收入", placeholder: "元/月，可空", kind: "number" },
    { key: "fixed_expense_fen", label: "固定支出", placeholder: "元/月，可空", kind: "number" },
  ],
  study: [
    { key: "subject", label: "学什么", placeholder: "如 日语 N3 / 吉他" },
    { key: "deadline", label: "目标日期", kind: "date" },
    { key: "daily_minutes", label: "每日可投入时长", placeholder: "分钟", kind: "number" },
    { key: "level", label: "当前水平", placeholder: "如 零基础 / 学过一年" },
  ],
  custom: [{ key: "deadline", label: "目标日期", kind: "date" }],
}

/** 金额为元的键（提交时换算成分，键名本身带 _fen 后缀） */
const MONEY_KEYS = new Set(["target_fen", "fixed_income_fen", "fixed_expense_fen"])
/** 进 params 的"目标参数"键；问卷全量都进 answers（服务端规则引擎主要读 answers） */
const PARAM_KEYS: Record<GoalType, string[]> = {
  weight_loss: ["target_weight_kg", "deadline"],
  savings: ["target_fen", "deadline"],
  study: ["deadline"],
  custom: ["deadline"],
}

export default function GoalNew({ session }: { session: Session }) {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [type, setType] = useState<GoalType | null>(null)
  const [title, setTitle] = useState("")
  const [fields, setFields] = useState<Record<string, string>>({})
  // 可见性：默认私有；公开=勾选若干圈子 + 粒度二选一
  const [scope, setScope] = useState<"private" | "public">("private")
  const [circles, setCircles] = useState<AccountCircle[]>([])
  const [checked, setChecked] = useState<string[]>([])
  const [detailLevel, setDetailLevel] = useState<"summary" | "detail">("summary")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")

  // 进到可见性步骤时拉"我所在的圈子"列表；拿不到账号则退回当前圈
  useEffect(() => {
    if (step !== 2) return
    const accountId = loadAccountId()
    if (!accountId) {
      setCircles([
        {
          circle_id: session.circle_id,
          circle_name: session.circle_name,
        } as AccountCircle,
      ])
      return
    }
    api
      .accountCircles(accountId)
      .then((res) => setCircles(res.circles))
      .catch(() =>
        setCircles([
          {
            circle_id: session.circle_id,
            circle_name: session.circle_name,
          } as AccountCircle,
        ]),
      )
  }, [step, session.circle_id, session.circle_name])

  function pickType(t: GoalType) {
    setType(t)
    setTitle(DEFAULT_TITLE[t])
    setFields({})
    setStep(1)
  }

  function set(key: string, value: string) {
    setFields((f) => ({ ...f, [key]: value }))
  }

  /** 组装 params/answers：数字/金额换算在这里做，空值不落库（问卷可全部跳过） */
  function buildPayload(t: GoalType) {
    const answers: Record<string, unknown> = {}
    for (const f of TYPE_FIELDS[t]) {
      const raw = (fields[f.key] ?? "").trim()
      if (!raw) continue
      if (MONEY_KEYS.has(f.key)) {
        answers[f.key] = Math.round(Number(raw) * 100)
      } else if (f.kind === "number") {
        answers[f.key] = Number(raw)
      } else {
        answers[f.key] = raw
      }
    }
    const params: Record<string, unknown> = {}
    for (const k of PARAM_KEYS[t]) {
      if (answers[k] !== undefined) params[k] = answers[k]
    }
    return { params, answers }
  }

  async function handleSubmit() {
    if (!type) return
    if (scope === "public" && checked.length === 0) {
      setError("公开的话至少勾一个圈子，或者保持私有")
      return
    }
    setBusy(true)
    setError("")
    try {
      const { params, answers } = buildPayload(type)
      const res = await api.createGoal(session.user_id, {
        type,
        title: title.trim() || DEFAULT_TITLE[type],
        params,
        answers,
        visible_circle_ids: scope === "public" ? checked : [],
        detail_level: detailLevel,
      })
      navigate(`/me/goals/${res.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : "创建失败，再试一次")
      setBusy(false)
    }
  }

  function toggleCircle(id: string) {
    setChecked((xs) => (xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]))
  }

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <button className="us-btn-ghost mb-4 -ml-4" onClick={() => navigate("/me")}>
        ← 返回我的
      </button>
      <h2 className="us-serif text-2xl mb-1">新建目标</h2>
      <p className="text-xs text-stone-500 mb-6">
        {step === 0 && "第 1 步：选一个类型"}
        {step === 1 && "第 2 步：答几个小问题"}
        {step === 2 && "第 3 步：要不要公开给圈子"}
      </p>

      {/* 第 1 步：类型四选 */}
      {step === 0 && (
        <div className="grid grid-cols-2 gap-3">
          {TYPE_CARDS.map((c, i) => (
            <button
              key={c.type}
              className="us-rise bg-white/60 rounded-2xl p-5 text-left hover:bg-white/80 transition-colors"
              style={{ animationDelay: `${i * 70}ms` }}
              onClick={() => pickType(c.type)}
            >
              <p className="us-serif text-lg">{c.label}</p>
              <p className="text-xs text-stone-500 mt-1.5 leading-relaxed">{c.desc}</p>
            </button>
          ))}
        </div>
      )}

      {/* 第 2 步：动态问卷（全部可跳过） */}
      {step === 1 && type && (
        <div className="us-rise">
          <p className="text-xs text-stone-400 mb-5">
            全部可跳过，填了更准；跳过的部分按通用值估算
          </p>
          <div className="flex flex-col gap-5 mb-8">
            <div>
              <label className="text-xs text-stone-500">目标名称</label>
              <input
                className="us-input mt-1"
                placeholder="给目标起个名字"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            {TYPE_FIELDS[type].map((f) => (
              <div key={f.key}>
                <label className="text-xs text-stone-500">{f.label}</label>
                {f.kind === "choice" ? (
                  <div className="flex gap-2 mt-1.5 flex-wrap">
                    {f.options?.map((o) => (
                      <button
                        key={o.value}
                        type="button"
                        className={
                          fields[f.key] === o.value
                            ? "us-chip"
                            : "us-btn-ghost text-xs border border-[#264653]/15"
                        }
                        onClick={() => set(f.key, fields[f.key] === o.value ? "" : o.value)}
                      >
                        {o.label}
                      </button>
                    ))}
                  </div>
                ) : (
                  <input
                    className="us-input mt-1"
                    type={f.kind === "date" ? "date" : f.kind === "number" ? "number" : "text"}
                    placeholder={f.placeholder}
                    value={fields[f.key] ?? ""}
                    onChange={(e) => set(f.key, e.target.value)}
                  />
                )}
              </div>
            ))}
          </div>
          {type === "custom" && !title.trim() && (
            <p className="text-xs text-stone-400 mb-3">自定义目标只需要填个名字</p>
          )}
          <div className="flex justify-between">
            <button className="us-btn-ghost" onClick={() => setStep(0)}>
              ← 上一步
            </button>
            <button
              className="us-btn"
              disabled={type === "custom" && !title.trim()}
              onClick={() => setStep(2)}
            >
              下一步
            </button>
          </div>
        </div>
      )}

      {/* 第 3 步：可见性（默认私有） */}
      {step === 2 && type && (
        <div className="us-rise">
          <div className="flex gap-2 mb-5">
            <button
              className={scope === "private" ? "us-chip" : "us-btn-ghost text-xs border border-[#264653]/15"}
              onClick={() => setScope("private")}
            >
              🔒 私有（默认）
            </button>
            <button
              className={scope === "public" ? "us-chip" : "us-btn-ghost text-xs border border-[#264653]/15"}
              onClick={() => setScope("public")}
            >
              公开到圈子
            </button>
          </div>
          {scope === "private" ? (
            <p className="text-sm text-stone-500 leading-relaxed mb-6">
              只有你自己能看到。之后随时可以在目标详情里改成公开。
            </p>
          ) : (
            <div className="mb-6">
              <p className="text-xs text-stone-500 mb-2">对哪些圈子可见（没勾的永远看不到）：</p>
              <div className="flex flex-col gap-2 mb-5">
                {circles.map((c) => (
                  <label
                    key={c.circle_id}
                    className="flex items-center gap-2.5 text-sm cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      className="accent-[#264653] w-4 h-4"
                      checked={checked.includes(c.circle_id)}
                      onChange={() => toggleCircle(c.circle_id)}
                    />
                    {c.circle_name}
                  </label>
                ))}
              </div>
              <p className="text-xs text-stone-500 mb-2">公开粒度：</p>
              <div className="flex gap-2">
                <button
                  className={
                    detailLevel === "summary"
                      ? "us-chip"
                      : "us-btn-ghost text-xs border border-[#264653]/15"
                  }
                  onClick={() => setDetailLevel("summary")}
                >
                  仅进度
                </button>
                <button
                  className={
                    detailLevel === "detail"
                      ? "us-chip"
                      : "us-btn-ghost text-xs border border-[#264653]/15"
                  }
                  onClick={() => setDetailLevel("detail")}
                >
                  含明细
                </button>
              </div>
              <p className="text-xs text-stone-400 mt-2 leading-relaxed">
                「仅进度」只给圈友看百分比和打卡情况；「含明细」会展示账单金额、体重、热量等具体数字。
                公开后圈友可以鞭策你（可在详情里关闭）。
              </p>
            </div>
          )}
          {error && <p className="text-sm text-red-700 mb-3">{error}</p>}
          <div className="flex justify-between">
            <button className="us-btn-ghost" onClick={() => setStep(1)}>
              ← 上一步
            </button>
            <button className="us-btn" disabled={busy} onClick={handleSubmit}>
              {busy ? "创建中…" : "创建目标"}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
