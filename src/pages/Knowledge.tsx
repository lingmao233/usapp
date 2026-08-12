import { useCallback, useEffect, useState } from "react"
import { api, type KnowledgeItem, type Session } from "@/lib/api"

export default function Knowledge({ session }: { session: Session }) {
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [tags, setTags] = useState<string[]>([])
  const [activeTag, setActiveTag] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [searching, setSearching] = useState(false)
  const [searchMode, setSearchMode] = useState(false)

  const load = useCallback(
    async (tag?: string | null) => {
      try {
        const res = await api.listKnowledge(session.circle_id, tag ?? undefined)
        setItems(res.items)
        if (!tag) setTags(res.tags)
        setSearchMode(false)
      } catch {
        /* 静默 */
      }
    },
    [session.circle_id],
  )

  useEffect(() => {
    load()
  }, [load])

  async function handleSearch() {
    const q = query.trim()
    if (!q) return
    setSearching(true)
    try {
      const res = await api.searchKnowledge(q, session.circle_id)
      setItems(res.results)
      setSearchMode(true)
      setActiveTag(null)
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">知识库</h2>
      <p className="text-xs text-stone-500 mb-6">链接和长文会自动归档到这里，配好摘要和标签</p>

      {/* 语义搜索 */}
      <div className="flex gap-3 items-end mb-6">
        <input
          className="us-input flex-1"
          placeholder="用自然语言搜：比如「海边旅行攻略」"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
        />
        <button className="us-btn" disabled={searching || !query.trim()} onClick={handleSearch}>
          {searching ? "搜…" : "搜一下"}
        </button>
      </div>

      {/* 标签 */}
      {!searchMode && tags.length > 0 && (
        <div className="flex gap-2 flex-wrap mb-6">
          <button
            className={`us-chip transition-colors ${!activeTag ? "bg-[#F4A261]/50" : ""}`}
            onClick={() => {
              setActiveTag(null)
              load(null)
            }}
          >
            全部
          </button>
          {tags.map((t) => (
            <button
              key={t}
              className={`us-chip transition-colors ${activeTag === t ? "bg-[#F4A261]/50" : ""}`}
              onClick={() => {
                setActiveTag(t)
                load(t)
              }}
            >
              #{t}
            </button>
          ))}
        </div>
      )}
      {searchMode && (
        <button
          className="us-btn-ghost text-xs mb-4 -ml-4"
          onClick={() => {
            setQuery("")
            load(null)
          }}
        >
          ← 清除搜索，回到全部
        </button>
      )}

      {/* 条目 */}
      {items.length === 0 ? (
        <div className="text-center text-stone-400 py-16 text-sm leading-loose">
          {searchMode ? "没搜到相关的收藏，换个说法试试" : "还没有收藏。丢一条带链接的碎片试试"}
        </div>
      ) : (
        <div className="flex flex-col">
          {items.map((item, i) => (
            <article
              key={item.id}
              className="us-rise py-5 border-b border-[#264653]/10"
              style={{ animationDelay: `${Math.min(i, 8) * 70}ms` }}
            >
              <div className="flex items-baseline gap-2 mb-1">
                <span className="text-xs text-stone-400">{item.user_nickname} 收藏</span>
                {item.similarity !== undefined && (
                  <span className="text-xs text-[#264653]/60">
                    相关度 {(item.similarity * 100).toFixed(0)}%
                  </span>
                )}
              </div>
              <h3 className="us-serif text-lg leading-snug">
                {item.url ? (
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="hover:text-[#F4A261] transition-colors"
                  >
                    {item.title}
                  </a>
                ) : (
                  item.title
                )}
              </h3>
              <p className="text-sm text-stone-600 leading-relaxed mt-2">{item.summary}</p>
              <div className="flex gap-1.5 mt-2.5 flex-wrap">
                {item.tags.map((t) => (
                  <span key={t} className="us-chip">
                    #{t}
                  </span>
                ))}
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}
