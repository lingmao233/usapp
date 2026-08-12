import { useCallback, useEffect, useState } from 'react'
import Taro from '@tarojs/taro'
import { Input, Text, View } from '@tarojs/components'
import { api, copyText, type KnowledgeItem, type Session } from '@/platform'

export default function Knowledge({ session }: { session: Session }) {
  const [items, setItems] = useState<KnowledgeItem[]>([])
  const [tags, setTags] = useState<string[]>([])
  const [activeTag, setActiveTag] = useState<string | null>(null)
  const [query, setQuery] = useState('')
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

  /** 小程序不能直接开外链：点击标题复制链接 */
  async function handleOpenUrl(url: string) {
    if (await copyText(url)) {
      Taro.showToast({ title: '链接已复制，去浏览器打开', icon: 'none' })
    }
  }

  return (
    <View className="max-w-2xl mx-auto px-5 py-8">
      <Text className="us-serif text-2xl mb-1 block">知识库</Text>
      <Text className="text-xs text-stone-500 mb-6 block">
        链接和长文会自动归档到这里，配好摘要和标签
      </Text>

      {/* 语义搜索 */}
      <View className="flex gap-3 items-end mb-6">
        <Input
          className="us-input flex-1"
          placeholder="用自然语言搜：比如「海边旅行攻略」"
          placeholderClass="us-input-ph"
          value={query}
          onInput={(e) => setQuery(e.detail.value)}
          onConfirm={handleSearch}
        />
        <View
          className={`us-btn ${searching || !query.trim() ? 'us-btn-disabled' : ''}`}
          onClick={searching || !query.trim() ? undefined : handleSearch}
        >
          {searching ? '搜…' : '搜一下'}
        </View>
      </View>

      {/* 标签 */}
      {!searchMode && tags.length > 0 && (
        <View className="flex gap-2 flex-wrap mb-6">
          <View
            className={`us-chip transition-colors ${!activeTag ? 'bg-[#F4A261]/50' : ''}`}
            onClick={() => {
              setActiveTag(null)
              load(null)
            }}
          >
            全部
          </View>
          {tags.map((t) => (
            <View
              key={t}
              className={`us-chip transition-colors ${activeTag === t ? 'bg-[#F4A261]/50' : ''}`}
              onClick={() => {
                setActiveTag(t)
                load(t)
              }}
            >
              #{t}
            </View>
          ))}
        </View>
      )}
      {searchMode && (
        <View
          className="us-btn-ghost text-xs mb-4 -ml-4"
          onClick={() => {
            setQuery('')
            load(null)
          }}
        >
          ← 清除搜索，回到全部
        </View>
      )}

      {/* 条目 */}
      {items.length === 0 ? (
        <View className="text-center text-stone-400 py-16 text-sm leading-loose">
          {searchMode ? '没搜到相关的收藏，换个说法试试' : '还没有收藏。丢一条带链接的碎片试试'}
        </View>
      ) : (
        <View className="flex flex-col">
          {items.map((item, i) => (
            <View
              key={item.id}
              className="us-rise py-5 border-b border-[#264653]/10"
              style={{ animationDelay: `${Math.min(i, 8) * 70}ms` }}
            >
              <View className="flex items-baseline gap-2 mb-1">
                <Text className="text-xs text-stone-400">{item.user_nickname} 收藏</Text>
                {item.similarity !== undefined && (
                  <Text className="text-xs text-[#264653]/60">
                    相关度 {(item.similarity * 100).toFixed(0)}%
                  </Text>
                )}
              </View>
              <Text className="us-serif text-lg leading-snug block">
                {item.url ? (
                  <Text className="transition-colors" onClick={() => handleOpenUrl(item.url)}>
                    {item.title}
                  </Text>
                ) : (
                  item.title
                )}
              </Text>
              <Text className="text-sm text-stone-600 leading-relaxed mt-2 block">
                {item.summary}
              </Text>
              <View className="flex gap-1.5 mt-2.5 flex-wrap">
                {item.tags.map((t) => (
                  <Text key={t} className="us-chip">
                    #{t}
                  </Text>
                ))}
              </View>
            </View>
          ))}
        </View>
      )}
    </View>
  )
}
