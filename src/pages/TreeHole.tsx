/** 情绪树洞：账号级私密对话（与圈子正交，只需要 accountId，圈内/无圈 Self 壳都可进）。
 *
 * chat 是整包响应 {reply, citations, tools_used, intent, guardrail}：
 * - guardrail=true 的干预话术由后端给，前端照常当普通回复展示；
 * - citations（依据摘抄）只随当轮响应返回，history 接口没有——本地挂在当条消息上展示；
 * - tools_used 是工具名数组，映射成中文标签提示「刚刚查了：…」。
 */
import { useEffect, useRef, useState } from "react"
import { Send } from "lucide-react"
import {
  api,
  type TreeholeCitation,
  type TreeholeMessage,
  type TreeholePersona,
} from "@/lib/api"
import Markdown from "@/components/Markdown"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

/** 当轮响应附带的展示信息（citations/tools 不落 history，只挂本地消息） */
type LocalMsg = TreeholeMessage & {
  citations?: TreeholeCitation[]
  tools?: string[]
}

/** 工具名 → 中文标签（与后端 treehole/tools.py 的 TOOLS 注册表对应；未知名兜底原样展示） */
const TOOL_LABEL: Record<string, string> = {
  query_ledger: "账本",
  query_today_plan: "计划",
  query_calories: "热量",
  search_fragments: "碎片",
  get_memory_profile: "记忆",
}

/** 依据来源 → 中文标签（后端 retrieve：fragment=碎片 / atom=记忆） */
const CITATION_KIND_LABEL: Record<string, string> = {
  fragment: "碎片",
  atom: "记忆",
}

const PERSONA_FIELDS: {
  key: keyof Omit<TreeholePersona, "default">
  label: string
  placeholder: string
  multiline?: boolean
}[] = [
  { key: "name", label: "名称", placeholder: "TA 的名字，比如「树洞」「阿青」" },
  { key: "personality", label: "性格", placeholder: "温和耐心？毒舌？慢热？" },
  { key: "speaking_style", label: "说话风格", placeholder: "口语化短句 / 文绉绉 / 爱反问…" },
  { key: "relationship", label: "与你的关系", placeholder: "老朋友 / 树洞先生 / 另一个自己…" },
  {
    key: "background",
    label: "背景设定",
    placeholder: "TA 的来历、你们之间的故事（可空）",
    multiline: true,
  },
]

/** 人设卡编辑（底部 sheet）：未设立（default）时显示「给 TA 立个人设」引导 */
function PersonaSheet({
  accountId,
  persona,
  onClose,
  onSaved,
}: {
  accountId: string
  persona: TreeholePersona | null
  onClose: () => void
  onSaved: (p: TreeholePersona) => void
}) {
  const [form, setForm] = useState({
    name: persona?.name ?? "",
    personality: persona?.personality ?? "",
    speaking_style: persona?.speaking_style ?? "",
    relationship: persona?.relationship ?? "",
    background: persona?.background ?? "",
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  async function save() {
    if (saving) return
    setSaving(true)
    setError("")
    try {
      onSaved(await api.putTreeholePersona(accountId, form))
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败，再试一次")
    } finally {
      setSaving(false)
    }
  }

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="bottom" className="rounded-t-2xl">
        <div className="w-full max-w-2xl mx-auto px-5 pb-6 flex flex-col max-h-[80vh]">
          <SheetHeader className="p-0 mb-3">
            <SheetTitle className="us-serif text-lg">
              {persona?.default ? "给 TA 立个人设" : "人设卡"}
            </SheetTitle>
          </SheetHeader>
          <div className="flex-1 overflow-y-auto min-h-0 flex flex-col gap-4 pr-1">
            {persona?.default && (
              <p className="text-xs text-stone-400 leading-relaxed">
                现在 TA 是默认的倾听者。立了人设，TA 会按这个名字、性格和口吻一直陪你聊。
              </p>
            )}
            {PERSONA_FIELDS.map((f) => (
              <label key={f.key} className="block">
                <span className="text-xs text-stone-500">{f.label}</span>
                {f.multiline ? (
                  <textarea
                    className="us-input resize-none mt-1"
                    rows={3}
                    placeholder={f.placeholder}
                    value={form[f.key]}
                    onChange={(e) => setForm((v) => ({ ...v, [f.key]: e.target.value }))}
                  />
                ) : (
                  <input
                    className="us-input mt-1"
                    placeholder={f.placeholder}
                    value={form[f.key]}
                    onChange={(e) => setForm((v) => ({ ...v, [f.key]: e.target.value }))}
                  />
                )}
              </label>
            ))}
          </div>
          {error && <p className="text-xs text-red-500 mt-3">{error}</p>}
          <div className="flex justify-end gap-2 mt-4">
            <button className="us-btn-ghost text-sm" onClick={onClose}>
              取消
            </button>
            <button className="us-btn" disabled={saving} onClick={save}>
              {saving ? "保存中…" : "保存人设"}
            </button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}

export default function TreeHole({ accountId }: { accountId: string }) {
  const [messages, setMessages] = useState<LocalMsg[]>([])
  const [persona, setPersona] = useState<TreeholePersona | null>(null)
  const [draft, setDraft] = useState("")
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState("")
  const [personaOpen, setPersonaOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  // 历史原文 + 人设卡并行拉取（互不影响，各自失败各自静默）
  useEffect(() => {
    api
      .treeholeHistory(accountId)
      .then(setMessages)
      .catch(() => {})
    api
      .getTreeholePersona(accountId)
      .then(setPersona)
      .catch(() => {})
  }, [accountId])

  // 新消息/发送态变化时滚到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
  }, [messages.length, sending])

  async function send() {
    const text = draft.trim()
    if (!text || sending) return
    setSending(true)
    setSendError("")
    setDraft("")
    // 先乐观上屏自己的话；回复到达后追加（citations/tools 挂在这条本地消息上）
    setMessages((ms) => [
      ...ms,
      {
        id: `tmp-u-${Date.now()}`,
        role: "user",
        content: text,
        created_at: new Date().toISOString(),
      },
    ])
    try {
      const r = await api.treeholeChat(accountId, text)
      setMessages((ms) => [
        ...ms,
        {
          id: `tmp-a-${Date.now()}`,
          role: "assistant",
          content: r.reply,
          created_at: new Date().toISOString(),
          citations: r.citations,
          tools: r.tools_used,
        },
      ])
    } catch {
      /* 失败时乐观消息保留，用户可重发 */
      setSendError("这句没发出去，再试一次")
    } finally {
      setSending(false)
    }
  }

  async function handleClear() {
    if (!window.confirm("清空这段对话？（TA 记住的关于你的事会保留）")) return
    try {
      await api.treeholeClear(accountId)
      setMessages([])
    } catch {
      /* 失败保持现状，用户可重试 */
    }
  }

  const personaName = persona?.name || "树洞"

  return (
    <div className="max-w-2xl mx-auto px-5 pt-5 flex flex-col min-h-[calc(100dvh-60px)]">
      {/* 页头：标题 + 人设/清空入口 */}
      <div className="flex items-center justify-between gap-3 pb-3 border-b border-[#264653]/10">
        <div className="min-w-0">
          <h2 className="us-serif text-xl">情绪树洞</h2>
          <p className="text-xs text-stone-400 mt-0.5 truncate">正在和「{personaName}」说话</p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button className="us-btn-ghost text-xs" onClick={() => setPersonaOpen(true)}>
            人设
          </button>
          <button
            className="us-btn-ghost text-xs text-stone-400"
            onClick={handleClear}
            disabled={messages.length === 0}
          >
            清空
          </button>
        </div>
      </div>

      {/* 消息流：用户右 / AI 左 */}
      <div className="flex-1 flex flex-col gap-3 py-4">
        {messages.length === 0 && !sending ? (
          <div className="text-center text-stone-400 py-16 leading-loose">
            <p className="us-serif text-xl text-[#264653] mb-2">这里只有你和 TA</p>
            <p className="text-sm">说什么都行，TA 记得你发过的碎片</p>
            {persona?.default && (
              <button
                className="us-btn-ghost text-xs mt-4 border border-[#264653]/15"
                onClick={() => setPersonaOpen(true)}
              >
                给 TA 立个人设 →
              </button>
            )}
          </div>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={`flex flex-col ${m.role === "user" ? "items-end" : "items-start"}`}
            >
              <div
                className={`text-sm leading-relaxed rounded-2xl px-3.5 py-2 max-w-[85%] break-words ${
                  m.role === "user"
                    ? "bg-[#264653] text-white whitespace-pre-wrap"
                    : "bg-white/80 text-stone-700"
                }`}
              >
                {m.role === "user" ? m.content : <Markdown text={m.content} />}
              </div>
              {m.tools && m.tools.length > 0 && (
                <p className="text-xs text-stone-400 mt-1">
                  刚刚查了：{m.tools.map((t) => TOOL_LABEL[t] ?? t).join("、")}
                </p>
              )}
              {m.citations && m.citations.length > 0 && (
                <details className="mt-1 max-w-[85%] text-xs text-stone-400">
                  <summary className="cursor-pointer select-none hover:text-stone-500">
                    依据 {m.citations.length} 条
                  </summary>
                  <ul className="mt-1 flex flex-col gap-1 border-l-2 border-[#264653]/10 pl-2">
                    {m.citations.map((c) => (
                      <li key={`${c.kind}-${c.id}`} className="leading-relaxed">
                        <span className="text-[#264653]/70">
                          {CITATION_KIND_LABEL[c.kind] ?? c.kind}
                        </span>
                        <span className="mx-1">·</span>
                        {c.excerpt}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          ))
        )}
        {sending && (
          <div className="self-start max-w-[85%] rounded-2xl bg-white/80 px-3.5 py-2 text-sm text-stone-400">
            正在想…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 输入区：吸底（内容少时停在视口底，内容多时随滚动吸附） */}
      <div className="sticky bottom-0 bg-[#F5F0E1] pt-2 pb-4">
        {sendError && <p className="text-xs text-red-500 mb-1">{sendError}</p>}
        <div className="flex items-center gap-2">
          <input
            className="us-input flex-1"
            placeholder={`和${personaName}说点什么…`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && send()}
          />
          <button
            className="us-btn shrink-0"
            disabled={sending || !draft.trim()}
            onClick={send}
            aria-label="发送"
          >
            <Send className="w-3.5 h-3.5" />
            发送
          </button>
        </div>
      </div>

      {personaOpen && (
        <PersonaSheet
          accountId={accountId}
          persona={persona}
          onClose={() => setPersonaOpen(false)}
          onSaved={(p) => {
            setPersona(p)
            setPersonaOpen(false)
          }}
        />
      )}
    </div>
  )
}
