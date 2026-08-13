/** 方案追问（轻量对话）：挂在方案卡片下方，上下文只有这条共同愿望与方案。
 *
 * 会话存服务端的通用 chat_threads/chat_messages（kind='plan'），
 * 将来独立 AI 聊天页复用同一套表与 /api/chat 路由。
 */
import { useEffect, useRef, useState } from "react"
import { api, type ChatMessage, type Session } from "@/lib/api"

export default function PlanChat({ wishId, session }: { wishId: string; session: Session }) {
  const [open, setOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState("")
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  // 展开时才拉记录；新消息到达滚到底部
  useEffect(() => {
    if (!open) return
    api
      .getPlanChat(wishId, session.user_id)
      .then((r) => setMessages(r.messages))
      .catch(() => {})
  }, [open, wishId, session.user_id])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }, [messages.length])

  async function send() {
    const text = draft.trim()
    if (!text || sending) return
    setSending(true)
    setDraft("")
    // 先乐观上屏自己的问题，回复到达后整体替换为服务端全量
    setMessages((ms) => [
      ...ms,
      { id: `tmp-${Date.now()}`, role: "user", content: text, created_at: "" },
    ])
    try {
      const r = await api.sendPlanChat(wishId, session.user_id, text)
      setMessages(r.messages)
    } catch {
      /* 失败时乐观消息保留，用户可重发 */
    } finally {
      setSending(false)
    }
  }

  if (!open) {
    return (
      <button
        className="text-xs text-[#264653] underline underline-offset-2 hover:opacity-70 mt-2"
        onClick={() => setOpen(true)}
      >
        💬 追问这个方案
      </button>
    )
  }

  return (
    <div className="mt-3 border-t border-[#264653]/10 pt-3">
      <div className="flex flex-col gap-2 max-h-56 overflow-y-auto pr-1">
        {messages.length === 0 && (
          <p className="text-xs text-stone-400">
            对方案有疑问直接问：换时间、砍预算、换个地方都行
          </p>
        )}
        {messages.map((m) => (
          <div
            key={m.id}
            className={`text-sm leading-relaxed rounded-xl px-3 py-2 max-w-[85%] ${
              m.role === "user"
                ? "self-end bg-[#264653] text-white"
                : "self-start bg-white/80 text-stone-700"
            }`}
          >
            {m.content}
          </div>
        ))}
        {sending && <p className="text-xs text-stone-400 self-start">助手思考中…</p>}
        <div ref={bottomRef} />
      </div>
      <div className="flex gap-2 mt-2.5">
        <input
          className="us-input flex-1 text-sm"
          placeholder="追问方案…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button className="us-btn text-sm" disabled={sending || !draft.trim()} onClick={send}>
          问
        </button>
      </div>
    </div>
  )
}
