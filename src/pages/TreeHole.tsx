/** 情绪树洞：账号级私密对话（与圈子正交，只需要 accountId，圈内/无圈 Self 壳都可进）。
 *
 * chat 是整包响应 {reply, citations, tools_used, intent, guardrail}：
 * - guardrail=true 的干预话术由后端给，前端照常当普通回复展示；
 * - citations（依据摘抄）与 tools（查了什么工具）随消息持久化，history 直接带出
 *   （当轮流式到达时先挂本地消息，刷新后从 history 读回）；
 * - tools_used 是工具名数组，映射成中文标签提示「刚刚查了：…」。
 */
import { useEffect, useRef, useState } from "react"
import { ImagePlus, Send, X } from "lucide-react"
import {
  api,
  treeholeChatStream,
  type TreeholeCitation,
  type TreeholeMessage,
  type TreeholePersona,
} from "@/lib/api"
import { displayUrl, prepareImage, type PreparedImage } from "@/lib/image"
import Markdown from "@/components/Markdown"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

/** 图片消息的气泡文本：剥掉服务端追加的 [图片：…] caption 标记（内部记忆用，不展示） */
function visibleContent(m: TreeholeMessage): string {
  if (!m.image_url) return m.content
  return m.content.replace(/\n?\[图片：[\s\S]*$/, "").trim()
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
  key: keyof Omit<TreeholePersona, "default" | "thinking">
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

/** 人设卡编辑（底部 sheet）：未设立（default）时显示「给 TA 立个人设」引导。
 * 两种模式：模板填写（结构化五字段）/ 整段粘贴（custom_prompt，非空时生成完全优先） */
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
  const [mode, setMode] = useState<"template" | "custom">(
    persona?.custom_prompt ? "custom" : "template",
  )
  const [form, setForm] = useState({
    name: persona?.name ?? "",
    personality: persona?.personality ?? "",
    speaking_style: persona?.speaking_style ?? "",
    relationship: persona?.relationship ?? "",
    background: persona?.background ?? "",
    custom_prompt: persona?.custom_prompt ?? "",
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")

  async function save() {
    if (saving) return
    setSaving(true)
    setError("")
    try {
      // 模板模式保存时清空 custom_prompt：避免旧的整段人设继续压过刚填的模板；
      // thinking 由页头选择器管理，人设保存原样带上不重置
      const base = mode === "custom" ? form : { ...form, custom_prompt: "" }
      onSaved(await api.putTreeholePersona(accountId, { ...base, thinking: persona?.thinking }))
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
          {/* 模式切换：模板填写 / 整段粘贴 */}
          <div className="flex gap-2 mb-4">
            {([
              ["template", "模板填写"],
              ["custom", "整段粘贴"],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                className={`rounded-full px-3.5 py-1.5 text-sm transition-colors duration-200 ${
                  mode === key
                    ? "bg-[#161616] text-white"
                    : "text-[#264653] hover:bg-[#264653]/8"
                }`}
                onClick={() => setMode(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="flex-1 overflow-y-auto min-h-0 flex flex-col gap-4 pr-1">
            {persona?.default && (
              <p className="text-xs text-stone-400 leading-relaxed">
                现在 TA 是默认的倾听者。立了人设，TA 会按这个名字、性格和口吻一直陪你聊。
              </p>
            )}
            {mode === "custom" ? (
              <>
                <label className="block">
                  <span className="text-xs text-stone-500">名称</span>
                  <input
                    className="us-input mt-1"
                    placeholder="TA 的名字，比如「树洞」「阿青」"
                    value={form.name}
                    onChange={(e) => setForm((v) => ({ ...v, name: e.target.value }))}
                  />
                </label>
                <label className="block">
                  <span className="text-xs text-stone-500">整段人设</span>
                  <textarea
                    className="us-input resize-none mt-1"
                    rows={10}
                    placeholder={"把别处写好的人设整段粘进来，比如：\n你是「阿青」，28 岁的女心理咨询师，说话温和但直接……"}
                    value={form.custom_prompt}
                    onChange={(e) =>
                      setForm((v) => ({ ...v, custom_prompt: e.target.value }))
                    }
                  />
                </label>
                <p className="text-xs text-stone-400 leading-relaxed">
                  整段人设会原样交给 AI，优先级高于模板字段；想回到模板填写，切到「模板填写」保存即可。
                </p>
              </>
            ) : (
              PERSONA_FIELDS.map((f) => (
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
              ))
            )}
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
  const [messages, setMessages] = useState<TreeholeMessage[]>([])
  const [hasMore, setHasMore] = useState(false)
  const [loadingEarlier, setLoadingEarlier] = useState(false)
  const [persona, setPersona] = useState<TreeholePersona | null>(null)
  const [draft, setDraft] = useState("")
  const [image, setImage] = useState<PreparedImage | null>(null)
  const [imagePreview, setImagePreview] = useState("")
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState("")
  const [personaOpen, setPersonaOpen] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  // 置底判定用：尾部消息 id 的上次值（「加载更早」是头部前置，不该触发滚底）
  const lastIdRef = useRef<string | null>(null)

  // 历史原文 + 人设卡并行拉取（互不影响，各自失败各自静默）
  useEffect(() => {
    api
      .treeholeHistory(accountId)
      .then((r) => {
        setMessages(r.items)
        setHasMore(r.has_more)
      })
      .catch(() => {})
    api
      .getTreeholePersona(accountId)
      .then(setPersona)
      .catch(() => {})
  }, [accountId])

  // 「加载更早」：以当前最早一条的 created_at 为游标翻上一页，前置进消息流。
  // 页面滚动的是 window（无内部滚动容器）：前置前记下 scrollY/scrollHeight，
  // 渲染后补偿高度差，视线停在原来那条消息上
  async function loadEarlier() {
    if (loadingEarlier || messages.length === 0) return
    setLoadingEarlier(true)
    const prevHeight = document.documentElement.scrollHeight
    const prevScroll = window.scrollY
    try {
      const r = await api.treeholeHistory(accountId, {
        before_created: messages[0].created_at,
      })
      setMessages((ms) => [...r.items, ...ms])
      setHasMore(r.has_more)
      requestAnimationFrame(() => {
        window.scrollTo(0, prevScroll + document.documentElement.scrollHeight - prevHeight)
      })
    } catch {
      /* 翻页失败保持现状，可重试 */
    } finally {
      setLoadingEarlier(false)
    }
  }

  // 尾部新增（发送/收到回复）或发送态变化时滚到底部
  useEffect(() => {
    const last = messages[messages.length - 1]
    const lastId = last?.id ?? null
    const grewAtTail = lastId !== null && lastId !== lastIdRef.current
    lastIdRef.current = lastId
    if (grewAtTail || sending) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" })
    }
  }, [messages.length, sending])

  async function onPickImage(f: File | null) {
    if (fileRef.current) fileRef.current.value = ""
    if (!f) return
    try {
      const prepared = await prepareImage(f)
      setImage(prepared)
      setImagePreview(URL.createObjectURL(prepared.display))
      setSendError("")
    } catch (e) {
      setSendError(e instanceof Error ? e.message : "图片处理失败，换一张试试")
    }
  }

  async function send() {
    const text = draft.trim()
    const img = image
    if ((!text && !img) || sending) return
    setSending(true)
    setSendError("")
    setDraft("")
    // 有图先上传（原图 + 1600px 展示图双份，与碎片同款管线）；失败不消费这条消息
    let imageUrl = ""
    if (img) {
      try {
        imageUrl = (await api.uploadImage(img.original, img.display, img.vision)).url
      } catch {
        setSendError("图片没传上去，再试一次")
        setSending(false)
        return
      }
      setImage(null)
      setImagePreview("")
    }
    // 先乐观上屏自己的话；回复到达后追加（citations/tools 挂在这条本地消息上）
    setMessages((ms) => [
      ...ms,
      {
        id: `tmp-u-${Date.now()}`,
        role: "user",
        content: text,
        image_url: imageUrl,
        created_at: new Date().toISOString(),
      },
    ])
    // 流式优先：delta 逐段长到本地气泡上；done 到达换成权威全文（含引用/工具）。
    // 一个 delta 都没收到就挂了 → 回退整包接口重试（行为与旧版一致）；
    // 已经流出一半才断 → 保留半截提示重发（回退重试可能重复生成，不自动重发）
    const aid = `tmp-a-${Date.now()}`
    setMessages((ms) => [...ms, {
      id: aid, role: "assistant", content: "", created_at: new Date().toISOString(),
    }])
    let streamed = false
    try {
      const r = await treeholeChatStream(accountId, text, imageUrl || undefined, (piece) => {
        streamed = true
        setMessages((ms) => ms.map((m) => (m.id === aid ? { ...m, content: m.content + piece } : m)))
      })
      setMessages((ms) => ms.map((m) => (m.id === aid ? {
        ...m, content: r.reply, citations: r.citations as TreeholeCitation[] | undefined,
        tools: r.tools_used,
      } : m)))
    } catch {
      if (!streamed) {
        setMessages((ms) => ms.filter((m) => m.id !== aid)) // 撤掉空壳气泡走整包回退
        try {
          const r = await api.treeholeChat(accountId, text, imageUrl || undefined)
          setMessages((ms) => [...ms, {
            id: `tmp-a-${Date.now()}`,
            role: "assistant",
            content: r.reply,
            created_at: new Date().toISOString(),
            citations: r.citations,
            tools: r.tools_used,
          }])
        } catch {
          setSendError("这句没发出去，再试一次")
        }
      } else {
        setSendError("回复中断了，这段可能没说完整；再发一次就好")
      }
    } finally {
      setSending(false)
    }
  }

  async function handleClear() {
    if (!window.confirm("清空这段对话？（TA 记住的关于你的事会保留）")) return
    try {
      await api.treeholeClear(accountId)
      setMessages([])
      setHasMore(false)
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
          {/* 思考程度：随人设卡持久化（fast/balanced/deep → 后端思考参数档位） */}
          <select
            className="us-input !py-0.5 !text-xs w-16"
            value={persona?.thinking || "balanced"}
            title="思考程度：快=更快回（能关思考就关）；平衡=模型默认；深思=想得更细但更慢"
            disabled={!persona}
            onChange={async (e) => {
              if (!persona) return
              const thinking = e.target.value
              try {
                const { default: _isDefault, ...fields } = persona
                setPersona(await api.putTreeholePersona(accountId, { ...fields, thinking }))
              } catch {
                /* 改不动保持原档（下次加载恢复真实值） */
              }
            }}
          >
            <option value="fast">快</option>
            <option value="balanced">平衡</option>
            <option value="deep">深思</option>
          </select>
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
        {hasMore && messages.length > 0 && (
          <button
            className="us-btn-ghost text-xs self-center border border-[#264653]/15"
            onClick={loadEarlier}
            disabled={loadingEarlier}
          >
            {loadingEarlier ? "加载中…" : "加载更早"}
          </button>
        )}
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
                {m.image_url && (
                  <img
                    src={displayUrl(m.image_url)}
                    alt="发送的图片"
                    className="rounded-xl max-w-full max-h-64 object-cover mb-1"
                    onError={(e) => {
                      // 旧图没有 1600px 展示副本时回退原图
                      const t = e.currentTarget
                      if (m.image_url && !t.src.endsWith(m.image_url)) t.src = m.image_url
                    }}
                  />
                )}
                {m.role === "user" ? visibleContent(m) : <Markdown text={m.content} />}
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
        {sending && !messages.some((m) => m.role === "assistant" && m.id.startsWith("tmp-a-") && m.content) && (
          <div className="self-start max-w-[85%] rounded-2xl bg-white/80 px-3.5 py-2 text-sm text-stone-400">
            正在想…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 输入区：吸底（内容少时停在视口底，内容多时随滚动吸附） */}
      <div className="sticky bottom-0 bg-[#F5F0E1] pt-2 pb-4">
        {sendError && <p className="text-xs text-red-500 mb-1">{sendError}</p>}
        {imagePreview && (
          <div className="relative inline-block mb-2">
            <img
              src={imagePreview}
              alt="待发送的图片"
              className="h-16 w-16 rounded-xl object-cover border border-[#264653]/15"
            />
            <button
              className="absolute -top-1.5 -right-1.5 rounded-full bg-[#161616] text-white p-0.5"
              onClick={() => {
                setImage(null)
                setImagePreview("")
              }}
              aria-label="移除图片"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}
        <div className="flex items-center gap-2">
          <input
            ref={fileRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            className="hidden"
            onChange={(e) => onPickImage(e.target.files?.[0] ?? null)}
          />
          <button
            className="us-btn-ghost shrink-0 !px-2.5"
            onClick={() => fileRef.current?.click()}
            disabled={sending}
            title="发图片"
            aria-label="发图片"
          >
            <ImagePlus className="w-4 h-4" />
          </button>
          <input
            className="us-input flex-1"
            placeholder={image ? "给图片配句话（可空）…" : `和${personaName}说点什么…`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && send()}
          />
          <button
            className="us-btn shrink-0"
            disabled={sending || (!draft.trim() && !image)}
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
