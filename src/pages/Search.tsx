import { useCallback, useEffect, useState } from "react"
import { Search as SearchIcon } from "lucide-react"
import {
  api,
  type Fragment,
  type KnowledgeItem,
  type Session,
  type Wish,
} from "@/lib/api"

const WISH_STATUS_LABEL: Record<string, string> = {
  open: "进行中",
  done: "已实现",
}

function fragmentTime(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ""
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

/** 知识条目卡（列表与搜索结果共用） */
function KnowledgeCard({ item }: { item: KnowledgeItem }) {
  return (
    <article className="py-4 border-b border-[#264653]/10">
      <div className="flex items-baseline gap-2 mb-1">
        <span className="text-xs text-stone-400">{item.user_nickname} 收藏</span>
        {item.similarity !== undefined && (
          <span className="text-xs text-[#264653]/60">
            相关度 {(item.similarity * 100).toFixed(0)}%
          </span>
        )}
      </div>
      <h4 className="us-serif text-base leading-snug">
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
      </h4>
      <p className="text-sm text-stone-600 leading-relaxed mt-1.5">{item.summary}</p>
      <div className="flex gap-1.5 mt-2 flex-wrap">
        {item.tags.map((t) => (
          <span key={t} className="us-chip">
            #{t}
          </span>
        ))}
      </div>
    </article>
  )
}

/** 搜索页（顶替原知识库入口）：全局搜碎片 + 知识库 + 愿望；空 query 默认展示知识库列表。
 * 没有全局搜索接口：知识库走语义搜索接口，碎片/愿望拉全量客户端过滤后合并展示。 */
export default function Search({ session }: { session: Session }) {
  const [query, setQuery] = useState("")
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  // 空 query 默认内容：知识库列表
  const [feed, setFeed] = useState<KnowledgeItem[]>([])
  // 搜索结果（分组）
  const [fragHits, setFragHits] = useState<Fragment[]>([])
  const [wishHits, setWishHits] = useState<Wish[]>([])
  const [knowHits, setKnowHits] = useState<KnowledgeItem[]>([])

  useEffect(() => {
    api
      .listKnowledge(session.circle_id)
      .then((res) => setFeed(res.items))
      .catch(() => {})
  }, [session.circle_id])

  const handleSearch = useCallback(async () => {
    const q = query.trim().toLowerCase()
    if (!q) return
    setSearching(true)
    setSearched(true)
    // 三路并发，独立成败：一路失败不拖垮其它分组
    const [fragRes, wishRes, knowRes] = await Promise.allSettled([
      api.listFragments(session.circle_id, session.user_id),
      api.listWishes(session.circle_id, session.user_id),
      api.searchKnowledge(q, session.circle_id),
    ])
    setFragHits(
      fragRes.status === "fulfilled"
        ? fragRes.value.fragments.filter((f) => f.content.toLowerCase().includes(q))
        : [],
    )
    setWishHits(
      wishRes.status === "fulfilled"
        ? wishRes.value.wishes.filter((w) => w.content.toLowerCase().includes(q))
        : [],
    )
    setKnowHits(knowRes.status === "fulfilled" ? knowRes.value.results : [])
    setSearching(false)
  }, [query, session.circle_id, session.user_id])

  function clearSearch() {
    setQuery("")
    setSearched(false)
  }

  const totalHits = fragHits.length + wishHits.length + knowHits.length

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">搜索</h2>
      <p className="text-xs text-stone-500 mb-6">搜碎片、知识库和愿望；不输关键词就是知识库</p>

      <div className="flex gap-3 items-center mb-6">
        <div className="relative flex-1">
          <SearchIcon
            size={16}
            className="absolute left-3.5 top-1/2 -translate-y-1/2 text-stone-400 pointer-events-none"
          />
          <input
            className="us-input w-full pl-9"
            placeholder="比如「海边」「想暴富」「旅行攻略」…"
            value={query}
            autoFocus
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && handleSearch()}
          />
        </div>
        <button className="us-btn" disabled={searching || !query.trim()} onClick={handleSearch}>
          {searching ? "搜…" : "搜一下"}
        </button>
      </div>

      {/* 空 query：知识库默认列表 */}
      {!searched && (
        <section>
          <h3 className="us-serif text-lg mb-2">知识库</h3>
          {feed.length === 0 ? (
            <p className="text-center text-stone-400 py-14 text-sm leading-loose">
              还没有收藏。丢一条带链接的碎片试试
            </p>
          ) : (
            <div className="flex flex-col">
              {feed.map((item) => (
                <KnowledgeCard key={item.id} item={item} />
              ))}
            </div>
          )}
        </section>
      )}

      {/* 搜索结果：分组展示 */}
      {searched && (
        <div>
          <div className="flex items-baseline justify-between mb-4">
            <p className="text-xs text-stone-400">
              「{query.trim()}」共 {totalHits} 条结果
            </p>
            <button className="us-btn-ghost text-xs" onClick={clearSearch}>
              ← 清除搜索
            </button>
          </div>

          {totalHits === 0 && !searching && (
            <p className="text-center text-stone-400 py-14 text-sm leading-loose">
              什么都没搜到，换个说法试试
            </p>
          )}

          {fragHits.length > 0 && (
            <section className="mb-8">
              <h3 className="us-serif text-lg mb-2">碎片</h3>
              <div className="flex flex-col">
                {fragHits.map((f) => (
                  <div key={f.id} className="py-4 border-b border-[#264653]/10">
                    <div className="flex items-baseline gap-2 mb-1">
                      <span className="text-xs text-stone-400">{f.user_nickname}</span>
                      <span className="text-xs text-stone-300">{fragmentTime(f.created_at)}</span>
                    </div>
                    <p className="text-sm text-stone-700 leading-relaxed whitespace-pre-wrap">
                      {f.content}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {wishHits.length > 0 && (
            <section className="mb-8">
              <h3 className="us-serif text-lg mb-2">愿望</h3>
              <div className="flex flex-col">
                {wishHits.map((w) => (
                  <div key={w.id} className="py-4 border-b border-[#264653]/10">
                    <div className="flex items-baseline gap-2 mb-1">
                      <span className="text-xs text-stone-400">{w.user_nickname}</span>
                      <span className="text-xs text-stone-300">
                        {WISH_STATUS_LABEL[w.status] ?? w.status}
                      </span>
                    </div>
                    <p className="text-sm text-stone-700 leading-relaxed">{w.content}</p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {knowHits.length > 0 && (
            <section className="mb-8">
              <h3 className="us-serif text-lg mb-2">知识库</h3>
              <div className="flex flex-col">
                {knowHits.map((item) => (
                  <KnowledgeCard key={item.id} item={item} />
                ))}
              </div>
            </section>
          )}
        </div>
      )}
    </div>
  )
}
