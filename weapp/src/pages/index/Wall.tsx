import { useCallback, useEffect, useRef, useState } from 'react'
import { Text, Textarea, View } from '@tarojs/components'
import { useDidShow } from '@tarojs/taro'
import {
  api,
  type Fragment,
  type RelatedFragment,
  type Report,
  type ReportMeta,
  type Session,
} from '@/platform'
import Markdown from '@/components/Markdown'

const CATEGORY_LABEL: Record<string, string> = {
  eat: '想吃',
  go: '想去',
  learn: '想学',
  buy: '想买',
  do: '想做',
}

function timeLabel(iso: string) {
  const d = new Date(iso)
  const now = Date.now()
  const diff = now - d.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

const RELATED_COPY = [
  '原来你们想到一块了',
  '巧了，TA 也在念叨这个',
  '这条可能会让你心头一动',
]

function RelatedCard({ items }: { items: RelatedFragment[] }) {
  return (
    <View className="us-related p-4 mt-3 flex flex-col gap-2">
      <Text className="text-sm font-medium text-[#264653] block">
        {RELATED_COPY[Math.floor(Math.random() * RELATED_COPY.length)]} ✨
      </Text>
      {items.map((r) => (
        <View key={r.id} className="text-sm leading-relaxed">
          <Text className="text-[#264653] font-medium">{r.user_nickname}</Text>
          <Text className="text-stone-500 text-xs"> · {timeLabel(r.created_at)}</Text>
          <Text className="text-stone-700 mt-0.5 block">{r.content}</Text>
        </View>
      ))}
    </View>
  )
}

function FragmentCard({
  fragment,
  related,
  index,
}: {
  fragment: Fragment
  related?: RelatedFragment[]
  index: number
}) {
  return (
    <View
      className="us-rise py-5 border-b border-[#264653]/10"
      style={{ animationDelay: `${Math.min(index, 8) * 70}ms` }}
    >
      <View className="flex items-baseline gap-2 mb-1.5">
        <Text className="text-sm font-medium text-[#264653]">{fragment.user_nickname}</Text>
        <Text className="text-xs text-stone-400">{timeLabel(fragment.created_at)}</Text>
        {fragment.is_wish && (
          <Text className="us-chip">
            {CATEGORY_LABEL[fragment.wish_category] ?? '心愿'}清单收录
          </Text>
        )}
        {fragment.is_knowledge && <Text className="us-chip">已归档知识库</Text>}
      </View>
      <Text className="leading-relaxed whitespace-pre-wrap block">{fragment.content}</Text>
      {fragment.tags.length > 0 && (
        <View className="flex gap-1.5 mt-2 flex-wrap">
          {fragment.tags.map((t) => (
            <Text key={t} className="text-xs text-stone-400">
              #{t}
            </Text>
          ))}
        </View>
      )}
      {related && related.length > 0 && <RelatedCard items={related} />}
    </View>
  )
}

function ReportView({ reportId, onBack }: { reportId: string; onBack: () => void }) {
  const [report, setReport] = useState<Report | null>(null)
  useEffect(() => {
    api.getReport(reportId).then(setReport).catch(() => setReport(null))
  }, [reportId])
  return (
    <View className="us-rise">
      <View className="us-btn-ghost mb-4 -ml-4" onClick={onBack}>
        ← 返回碎片墙
      </View>
      {report ? (
        <View className="bg-white/60 rounded-2xl p-6">
          <Markdown text={report.content} />
        </View>
      ) : (
        <Text className="text-stone-400 text-sm block">报告加载中…</Text>
      )}
    </View>
  )
}

export default function Wall({ session }: { session: Session }) {
  const [fragments, setFragments] = useState<Fragment[]>([])
  const [relatedMap, setRelatedMap] = useState<Record<string, RelatedFragment[]>>({})
  const [draft, setDraft] = useState('')
  const [posting, setPosting] = useState(false)
  const [reports, setReports] = useState<ReportMeta[]>([])
  const [reportGenerating, setReportGenerating] = useState(false)
  const [openReport, setOpenReport] = useState<string | null>(null)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

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
      const res = await api.listFragments(session.circle_id)
      setFragments(res.fragments)
      // 拉每条碎片的相关推荐（数据量小，MVP 可接受）
      const entries = await Promise.all(
        res.fragments.map(async (f) => {
          if (!f.processed) return [f.id, []] as const
          try {
            const rel = await api.relatedFragments(f.id)
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
  }, [session.circle_id])

  useEffect(() => {
    loadFragments()
    loadReports()
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [loadFragments, loadReports])

  // 小程序切回前台时刷新，保证双端数据同步的体感一致
  useDidShow(() => {
    loadFragments()
    loadReports()
  })

  // 有未处理的碎片时轮询，等 AI 管线跑完
  useEffect(() => {
    const hasPending = fragments.some((f) => !f.processed)
    if (hasPending && !pollingRef.current) {
      pollingRef.current = setInterval(async () => {
        const list = await loadFragments()
        if (list.every((f) => f.processed) && pollingRef.current) {
          clearInterval(pollingRef.current)
          pollingRef.current = null
        }
      }, 1200)
    }
  }, [fragments, loadFragments])

  // 周报生成中：隔几秒刷新一次列表
  useEffect(() => {
    if (!reportGenerating) return
    const timer = setInterval(async () => {
      const still = await loadReports()
      if (!still) clearInterval(timer)
    }, 4000)
    return () => clearInterval(timer)
  }, [reportGenerating, loadReports])

  async function handlePost() {
    const content = draft.trim()
    if (!content) return
    setPosting(true)
    try {
      await api.createFragment(session.circle_id, session.user_id, content)
      setDraft('')
      await loadFragments()
    } finally {
      setPosting(false)
    }
  }

  if (openReport) {
    return (
      <View className="max-w-2xl mx-auto px-5 py-8">
        <ReportView reportId={openReport} onBack={() => setOpenReport(null)} />
      </View>
    )
  }

  return (
    <View className="max-w-2xl mx-auto px-5 py-8">
      {/* 发布框 */}
      <View className="us-rise mb-2">
        <Textarea
          className="us-input resize-none h-24"
          placeholder="随手丢一条：一句话、一个链接、一个想做的事…"
          placeholderClass="us-input-ph"
          value={draft}
          onInput={(e) => setDraft(e.detail.value)}
        />
        <View className="flex justify-between items-center mt-3 mb-6">
          <Text className="text-xs text-stone-400">AI 会自动分类、找关联、收愿望</Text>
          <View
            className={`us-btn ${posting || !draft.trim() ? 'us-btn-disabled' : ''}`}
            onClick={posting || !draft.trim() ? undefined : handlePost}
          >
            {posting ? '丢出去中…' : '丢碎片'}
          </View>
        </View>
      </View>

      {/* 周报入口 */}
      <View className="us-panel rounded-2xl px-5 py-4 mb-6 flex items-center justify-between us-rise">
        <View>
          <Text className="us-serif text-base block">每周交集报告</Text>
          <Text className="text-xs text-stone-500 mt-0.5 block">
            {reportGenerating
              ? '本周报告生成中，喝口水稍等一下…'
              : reports.length > 0
                ? `已有 ${reports.length} 期，最新一期 ${reports[0].week_start}`
                : '丢几条碎片，周一就有第一期'}
          </Text>
        </View>
        {reports.length > 0 && (
          <View className="flex gap-2 flex-wrap justify-end">
            {reports.slice(0, 3).map((r) => (
              <View
                key={r.id}
                className="us-btn-ghost border border-[#264653]/15 text-xs"
                onClick={() => setOpenReport(r.id)}
              >
                {r.week_start.slice(5)} 期
              </View>
            ))}
          </View>
        )}
      </View>

      {/* 碎片流 */}
      {fragments.length === 0 ? (
        <View className="text-center text-stone-400 py-16 leading-loose">
          <Text className="us-serif text-xl text-[#264653] mb-2 block">圈子还空空的</Text>
          <Text className="text-sm block">先来三条：最近单曲循环的歌 / 想做的事 / 刷到的好文章</Text>
        </View>
      ) : (
        <View>
          {fragments.map((f, i) => (
            <FragmentCard key={f.id} fragment={f} related={relatedMap[f.id]} index={i} />
          ))}
        </View>
      )}
    </View>
  )
}
