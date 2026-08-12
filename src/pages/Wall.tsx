import { useCallback, useEffect, useRef, useState } from "react"
import { Heart, MessageCircle, Send } from "lucide-react"
import {
  api,
  type Comment,
  type Fragment,
  type RelatedFragment,
  type Report,
  type ReportMeta,
  type Session,
} from "@/lib/api"
import Markdown from "@/components/Markdown"
import ImagePicker, { type PickedImage } from "@/components/ImagePicker"
import FragImage from "@/components/FragImage"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

const CATEGORY_LABEL: Record<string, string> = {
  eat: "想吃",
  go: "想去",
  learn: "想学",
  buy: "想买",
  do: "想做",
}

function timeLabel(iso: string) {
  const d = new Date(iso)
  const now = Date.now()
  const diff = now - d.getTime()
  if (diff < 60_000) return "刚刚"
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

const RELATED_COPY = [
  "原来你们想到一块了",
  "巧了，TA 也在念叨这个",
  "这条可能会让你心头一动",
]

function RelatedCard({ items }: { items: RelatedFragment[] }) {
  return (
    <div className="us-related p-4 mt-3 flex flex-col gap-2">
      <p className="text-sm font-medium text-[#264653]">
        {RELATED_COPY[Math.floor(Math.random() * RELATED_COPY.length)]} ✨
      </p>
      {items.map((r) => (
        <div key={r.id} className="text-sm leading-relaxed">
          <span className="text-[#264653] font-medium">{r.user_nickname}</span>
          <span className="text-stone-500 text-xs"> · {timeLabel(r.created_at)}</span>
          <p className="text-stone-700 mt-0.5">{r.content}</p>
          {r.image_url && (
            <FragImage url={r.image_url} className="rounded-lg max-h-32 object-cover mt-1" alt="配图" />
          )}
        </div>
      ))}
    </div>
  )
}

function FragmentCard({
  fragment,
  related,
  index,
  isMine,
  onLike,
  onOpenComments,
  onDelete,
}: {
  fragment: Fragment
  related?: RelatedFragment[]
  index: number
  isMine: boolean
  onLike: (f: Fragment) => void
  onOpenComments: (f: Fragment) => void
  onDelete: (f: Fragment) => void
}) {
  return (
    <article
      className="us-rise py-5 border-b border-[#264653]/10"
      style={{ animationDelay: `${Math.min(index, 8) * 70}ms` }}
    >
      <div className="flex items-baseline gap-2 mb-1.5">
        <span className="text-sm font-medium text-[#264653]">{fragment.user_nickname}</span>
        <span className="text-xs text-stone-400">{timeLabel(fragment.created_at)}</span>
        {fragment.visibility === "private" && (
          <span className="text-xs text-stone-400" title="仅自己可见">
            🔒 仅自己可见
          </span>
        )}
        {fragment.is_wish && (
          <span className="us-chip">
            {CATEGORY_LABEL[fragment.wish_category] ?? "心愿"}清单收录
          </span>
        )}
        {fragment.is_knowledge && fragment.visibility === "public" && (
          <span className="us-chip">已归档知识库</span>
        )}
        {/* 只有自己的碎片能删（服务端同样校验） */}
        {isMine && (
          <button
            className="ml-auto text-stone-300 hover:text-stone-500 text-sm leading-none"
            onClick={() => onDelete(fragment)}
            aria-label="删除碎片"
          >
            ×
          </button>
        )}
      </div>
      <p className="leading-relaxed whitespace-pre-wrap">{fragment.content}</p>
      {/* 配图（发图片）：展示图限高，点击新窗口看原图；可见性跟随碎片（服务端过滤） */}
      {fragment.image_url && (
        <FragImage url={fragment.image_url} className="rounded-xl max-h-72 object-cover mt-2" alt="配图" />
      )}
      {fragment.tags.length > 0 && (
        <div className="flex gap-1.5 mt-2 flex-wrap">
          {fragment.tags.map((t) => (
            <span key={t} className="text-xs text-stone-400">
              #{t}
            </span>
          ))}
        </div>
      )}
      {/* 互动（第 4 期）：仅公共碎片可点赞/评论，隐私碎片不渲染互动 UI */}
      {fragment.visibility === "public" && (
        <div className="flex items-center gap-5 mt-3">
          <button
            className="flex items-center gap-1 text-xs text-stone-400 hover:text-[#e25563] transition-colors"
            onClick={() => onLike(fragment)}
            aria-label="点赞"
          >
            <Heart
              className={`w-4 h-4 ${fragment.liked_by_me ? "fill-[#e25563] text-[#e25563]" : ""}`}
            />
            {fragment.like_count > 0 && <span>{fragment.like_count}</span>}
          </button>
          <button
            className="flex items-center gap-1 text-xs text-stone-400 hover:text-[#264653] transition-colors"
            onClick={() => onOpenComments(fragment)}
            aria-label="评论"
          >
            <MessageCircle className="w-4 h-4" />
            <span>{fragment.comment_count > 0 ? fragment.comment_count : "评论"}</span>
          </button>
        </div>
      )}
      {related && related.length > 0 && <RelatedCard items={related} />}
    </article>
  )
}

/** 评论抽屉（底部 sheet）：平铺数据按 parent_id 组楼中楼，回复缩进一层 */
function CommentsSheet({
  fragment,
  session,
  onClose,
  onCountChange,
}: {
  fragment: Fragment
  session: Session
  onClose: () => void
  onCountChange: (fragmentId: string, count: number) => void
}) {
  const [comments, setComments] = useState<Comment[]>([])
  const [draft, setDraft] = useState("")
  const [replyTo, setReplyTo] = useState<Comment | null>(null)
  const [posting, setPosting] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await api.listComments(fragment.id)
      setComments(res.comments)
      onCountChange(fragment.id, res.comments.length)
    } catch {
      /* 拉取失败保持现状，下次打开重试 */
    }
  }, [fragment.id, onCountChange])

  useEffect(() => {
    load()
  }, [load])

  // 楼中楼：顶级按时间正序；每个顶级下收集全部后代（含回复的回复）缩进一层平铺
  const byId = new Map(comments.map((c) => [c.id, c]))
  const repliesOf = (id: string) => comments.filter((c) => c.parent_id === id)
  const collectDescendants = (id: string): Comment[] => {
    const out: Comment[] = []
    const queue = [...repliesOf(id)]
    while (queue.length) {
      const c = queue.shift()!
      out.push(c)
      queue.push(...repliesOf(c.id))
    }
    return out
  }

  async function handleSubmit() {
    const content = draft.trim()
    if (!content) return
    setPosting(true)
    try {
      await api.addComment(fragment.id, session.user_id, content, replyTo?.id)
      setDraft("")
      setReplyTo(null)
      await load()
    } finally {
      setPosting(false)
    }
  }

  const renderComment = (c: Comment, isReply: boolean) => {
    const parent = c.parent_id ? byId.get(c.parent_id) : undefined
    return (
      <div key={c.id} className={isReply ? "ml-7 mt-3" : "mt-4"}>
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-[#264653]">{c.author_nickname}</span>
          <span className="text-xs text-stone-400">{timeLabel(c.created_at)}</span>
          {/* 回复的回复：标注回复对象（直接挂顶级的靠缩进表达，不标注） */}
          {isReply && parent?.parent_id && (
            <span className="text-xs text-stone-400">回复 @{parent.author_nickname}</span>
          )}
        </div>
        <p className="text-sm leading-relaxed mt-0.5">{c.content}</p>
        <button className="text-xs text-stone-400 mt-1" onClick={() => setReplyTo(c)}>
          回复
        </button>
      </div>
    )
  }

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="bottom" className="rounded-t-2xl">
        <div className="w-full max-w-2xl mx-auto px-5 pb-6 flex flex-col max-h-[70vh]">
          <SheetHeader className="p-0 mb-2">
            <SheetTitle className="us-serif text-lg">评论</SheetTitle>
          </SheetHeader>
          <p className="text-xs text-stone-400 mb-2 line-clamp-2">
            {fragment.user_nickname}：{fragment.content}
          </p>
          <div className="flex-1 overflow-y-auto min-h-24">
            {comments.length === 0 ? (
              <p className="text-sm text-stone-400 py-8 text-center">还没有评论，说第一句吧</p>
            ) : (
              comments
                .filter((c) => !c.parent_id)
                .map((c) => (
                  <div key={c.id}>
                    {renderComment(c, false)}
                    {collectDescendants(c.id).map((r) => renderComment(r, true))}
                  </div>
                ))
            )}
          </div>
          {replyTo && (
            <div className="flex items-center gap-2 mt-3 text-xs text-stone-400">
              <span>回复 @{replyTo.author_nickname}</span>
              <button onClick={() => setReplyTo(null)}>取消</button>
            </div>
          )}
          <div className="flex items-center gap-2 mt-3">
            <input
              className="us-input flex-1"
              placeholder={replyTo ? `回复 @${replyTo.author_nickname}…` : "说点什么…"}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSubmit()
              }}
            />
            <button
              className="us-btn flex items-center gap-1"
              disabled={posting || !draft.trim()}
              onClick={handleSubmit}
            >
              <Send className="w-3.5 h-3.5" />
              发送
            </button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}

function ReportView({ reportId, onBack }: { reportId: string; onBack: () => void }) {
  const [report, setReport] = useState<Report | null>(null)
  useEffect(() => {
    api.getReport(reportId).then(setReport).catch(() => setReport(null))
  }, [reportId])
  return (
    <div className="us-rise">
      <button className="us-btn-ghost mb-4 -ml-4" onClick={onBack}>
        ← 返回碎片墙
      </button>
      {report ? (
        <div className="bg-white/60 rounded-2xl p-6">
          <Markdown text={report.content} />
        </div>
      ) : (
        <p className="text-stone-400 text-sm">报告加载中…</p>
      )}
    </div>
  )
}

export default function Wall({ session }: { session: Session }) {
  const [fragments, setFragments] = useState<Fragment[]>([])
  const [relatedMap, setRelatedMap] = useState<Record<string, RelatedFragment[]>>({})
  const [draft, setDraft] = useState("")
  const [posting, setPosting] = useState(false)
  const [reports, setReports] = useState<ReportMeta[]>([])
  const [reportGenerating, setReportGenerating] = useState(false)
  const [openReport, setOpenReport] = useState<string | null>(null)
  const [visibility, setVisibility] = useState<"public" | "private">("public")
  const [members, setMembers] = useState<{ id: string; nickname: string }[]>([])
  const [authorFilter, setAuthorFilter] = useState<string>("all")
  const [commentsFor, setCommentsFor] = useState<Fragment | null>(null)
  const [image, setImage] = useState<PickedImage | null>(null)
  const [submitError, setSubmitError] = useState("")
  const pollingRef = useRef<number | null>(null)

  // 互动写回：点赞/评论后局部更新对应卡片的计数与状态，不整列刷新
  const patchFragment = useCallback((id: string, patch: Partial<Fragment>) => {
    setFragments((fs) => fs.map((f) => (f.id === id ? { ...f, ...patch } : f)))
  }, [])

  const handleCountChange = useCallback(
    (id: string, count: number) => patchFragment(id, { comment_count: count }),
    [patchFragment],
  )

  async function handleLike(f: Fragment) {
    try {
      const res = await api.toggleLike(f.id, session.user_id)
      patchFragment(f.id, { liked_by_me: res.liked, like_count: res.like_count })
    } catch {
      /* 网络失败保持现状，用户可再点 */
    }
  }

  async function handleDelete(f: Fragment) {
    if (!window.confirm("删掉这条碎片？它的评论和点赞也会一起删掉")) return
    try {
      await api.deleteFragment(f.id, session.user_id)
      setFragments((fs) => fs.filter((x) => x.id !== f.id))
    } catch {
      /* 失败保持现状，用户可重试 */
    }
  }

  const loadReports = useCallback(async () => {
    try {
      const res = await api.listReports(session.circle_id)
      setReports(res.reports)
      setReportGenerating(Boolean(res.generating))
      return Boolean(res.generating)
    } catch {
      return false
    }
  }, [session.circle_id])

  const loadFragments = useCallback(async () => {
    try {
      const res = await api.listFragments(
        session.circle_id,
        session.user_id,
        authorFilter === "all" ? undefined : authorFilter,
      )
      setFragments(res.fragments)
      // 拉每条碎片的相关推荐（数据量小，MVP 可接受）
      const entries = await Promise.all(
        res.fragments.map(async (f) => {
          if (!f.processed) return [f.id, []] as const
          try {
            const rel = await api.relatedFragments(f.id, session.user_id)
            return [f.id, rel.related] as const
          } catch {
            return [f.id, []] as const
          }
        }),
      )
      setRelatedMap(Object.fromEntries(entries))
      return res.fragments
    } catch {
      return []
    }
  }, [session.circle_id, session.user_id, authorFilter])

  // 成员列表用于作者筛选器
  useEffect(() => {
    api
      .listMembers(session.circle_id)
      .then((res) => setMembers(res.members))
      .catch(() => {})
  }, [session.circle_id])

  useEffect(() => {
    loadFragments()
    loadReports()
    return () => {
      if (pollingRef.current) window.clearInterval(pollingRef.current)
    }
  }, [loadFragments, loadReports])

  // 有未处理的碎片时轮询，等 AI 管线跑完
  useEffect(() => {
    const hasPending = fragments.some((f) => !f.processed)
    if (hasPending && !pollingRef.current) {
      pollingRef.current = window.setInterval(async () => {
        const list = await loadFragments()
        if (list.every((f) => f.processed) && pollingRef.current) {
          window.clearInterval(pollingRef.current)
          pollingRef.current = null
        }
      }, 1200)
    }
  }, [fragments, loadFragments])

  // 周报生成中：隔几秒刷新一次列表
  useEffect(() => {
    if (!reportGenerating) return
    const timer = window.setInterval(async () => {
      const still = await loadReports()
      if (!still) window.clearInterval(timer)
    }, 4000)
    return () => window.clearInterval(timer)
  }, [reportGenerating, loadReports])

  async function handlePost() {
    const content = draft.trim()
    if (!content && !image) return
    setPosting(true)
    setSubmitError("")
    try {
      // 先上传图片拿 url，再随碎片一起提交
      let imageUrl: string | undefined
      if (image) {
        imageUrl = (await api.uploadImage(image.original, image.display)).url
      }
      await api.createFragment(session.circle_id, session.user_id, content, visibility, imageUrl)
      setDraft("")
      if (image) URL.revokeObjectURL(image.preview)
      setImage(null)
      await loadFragments()
    } catch {
      setSubmitError("发布失败，再试一次")
    } finally {
      setPosting(false)
    }
  }

  if (openReport) {
    return (
      <div className="max-w-2xl mx-auto px-5 py-8">
        <ReportView reportId={openReport} onBack={() => setOpenReport(null)} />
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      {/* 发布框 */}
      <div className="us-rise mb-2">
        <textarea
          className="us-input resize-none"
          rows={3}
          placeholder="随手丢一条：一句话、一个链接、一个想做的事…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handlePost()
          }}
        />
        <div className="flex justify-between items-center mt-3 mb-6">
          <span className="flex items-center gap-3">
            <span className="text-xs text-stone-400 hidden sm:inline">AI 会自动分类、找关联、收愿望</span>
            <ImagePicker image={image} onChange={setImage} />
          </span>
          <div className="flex items-center gap-2">
            {/* 公开/隐私开关，默认公开 */}
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
              🔒 隐私
            </button>
            <button
              className="us-btn"
              disabled={posting || (!draft.trim() && !image)}
              onClick={handlePost}
            >
              {posting ? "丢出去中…" : "丢碎片"}
            </button>
          </div>
        </div>
        {submitError && <p className="text-xs text-red-500 -mt-4 mb-4">{submitError}</p>}
      </div>

      {/* 周报入口 */}
      <div className="us-panel rounded-2xl px-5 py-4 mb-6 flex items-center justify-between us-rise">
        <div>
          <p className="us-serif text-base">每周交集报告</p>
          <p className="text-xs text-stone-500 mt-0.5">
            {reportGenerating
              ? "本周报告生成中，喝口水稍等一下…"
              : reports.length > 0
                ? `已有 ${reports.length} 期，最新一期 ${reports[0].week_start}`
                : "丢几条碎片，周一就有第一期"}
          </p>
        </div>
        {reports.length > 0 && (
          <div className="flex gap-2 flex-wrap justify-end">
            {reports.slice(0, 3).map((r) => (
              <button key={r.id} className="us-btn-ghost border border-[#264653]/15 text-xs" onClick={() => setOpenReport(r.id)}>
                {r.week_start.slice(5)} 期
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 碎片流：作者筛选（全部 / 我 / 各成员） */}
      <div className="flex gap-1.5 mb-2 flex-wrap">
        <button
          className={authorFilter === "all" ? "us-chip" : "us-btn-ghost text-xs"}
          onClick={() => setAuthorFilter("all")}
        >
          全部
        </button>
        <button
          className={authorFilter === session.user_id ? "us-chip" : "us-btn-ghost text-xs"}
          onClick={() => setAuthorFilter(session.user_id)}
        >
          我
        </button>
        {members
          .filter((m) => m.id !== session.user_id)
          .map((m) => (
            <button
              key={m.id}
              className={authorFilter === m.id ? "us-chip" : "us-btn-ghost text-xs"}
              onClick={() => setAuthorFilter(m.id)}
            >
              {m.nickname}
            </button>
          ))}
      </div>
      {fragments.length === 0 ? (
        <div className="text-center text-stone-400 py-16 leading-loose">
          <p className="us-serif text-xl text-[#264653] mb-2">圈子还空空的</p>
          <p className="text-sm">先来三条：最近单曲循环的歌 / 想做的事 / 刷到的好文章</p>
        </div>
      ) : (
        <div>
          {fragments.map((f, i) => (
            <FragmentCard
              key={f.id}
              fragment={f}
              related={relatedMap[f.id]}
              index={i}
              isMine={f.user_id === session.user_id}
              onLike={handleLike}
              onOpenComments={setCommentsFor}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}
      {commentsFor && (
        <CommentsSheet
          fragment={commentsFor}
          session={session}
          onClose={() => setCommentsFor(null)}
          onCountChange={handleCountChange}
        />
      )}
    </div>
  )
}
