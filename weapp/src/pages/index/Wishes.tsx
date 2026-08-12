import { useCallback, useEffect, useState } from 'react'
import { Input, Text, View } from '@tarojs/components'
import { api, type CommonWish, type Session, type Wish, type WishPlan } from '@/platform'

const CATEGORY_LABEL: Record<string, string> = {
  eat: '想吃',
  go: '想去',
  learn: '想学',
  buy: '想买',
  do: '想做',
}

function PlanCard({ plan, participants }: { plan: WishPlan; participants?: string[] }) {
  return (
    <View className="us-related p-4 mt-3 text-sm leading-relaxed">
      <Text className="font-medium text-[#264653] mb-2 block">
        「一起去」方案{participants && participants.length > 0 ? ` · ${participants.join('、')}` : ''}
      </Text>
      <View className="flex flex-col gap-1 text-stone-700">
        <Text className="block">🕐 {plan.time}</Text>
        <Text className="block">📍 {plan.location}</Text>
        <Text className="block">💰 {plan.budget}</Text>
      </View>
      <View className="mt-2.5 flex flex-col gap-1 text-stone-700">
        {plan.steps.map((s, i) => (
          <View key={i} className="flex gap-2">
            <Text className="text-[#F4A261] font-medium">{i + 1}.</Text>
            <Text className="flex-1">{s}</Text>
          </View>
        ))}
      </View>
    </View>
  )
}

export default function Wishes({ session }: { session: Session }) {
  const [wishes, setWishes] = useState<Wish[]>([])
  const [common, setCommon] = useState<CommonWish[]>([])
  const [draft, setDraft] = useState('')
  const [adding, setAdding] = useState(false)
  const [plans, setPlans] = useState<Record<string, { plan: WishPlan; participants?: string[] }>>({})
  const [planningId, setPlanningId] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const [w, c] = await Promise.all([
        api.listWishes(session.circle_id),
        api.commonWishes(session.circle_id),
      ])
      setWishes(w.wishes)
      setCommon(c.common_wishes)
      // 已缓存的方案直接展示
      const cached: Record<string, { plan: WishPlan }> = {}
      w.wishes.forEach((wish) => {
        if (wish.plan) cached[wish.id] = { plan: wish.plan }
      })
      setPlans((prev) => ({ ...cached, ...prev }))
    } catch {
      /* 静默 */
    }
  }, [session.circle_id])

  useEffect(() => {
    load()
  }, [load])

  async function handleAdd() {
    const content = draft.trim()
    if (!content) return
    setAdding(true)
    try {
      await api.addWish(session.circle_id, session.user_id, content)
      setDraft('')
      await load()
    } finally {
      setAdding(false)
    }
  }

  async function handlePlan(wishIds: string[]) {
    const id = wishIds[0]
    if (!id) return
    setPlanningId(id)
    try {
      const res = await api.wishPlan(id)
      setPlans((prev) => ({ ...prev, [id]: { plan: res.plan, participants: res.participants } }))
    } finally {
      setPlanningId(null)
    }
  }

  const myWishes = wishes.filter((w) => w.user_id === session.user_id)
  const others = wishes.filter((w) => w.user_id !== session.user_id)

  return (
    <View className="max-w-2xl mx-auto px-5 py-8">
      <Text className="us-serif text-2xl mb-1 block">愿望清单</Text>
      <Text className="text-xs text-stone-500 mb-6 block">
        碎片里说「想去/想学/想吃」会被自动收进来，也可以直接加
      </Text>

      {/* 手动添加 */}
      <View className="flex gap-3 items-end mb-8">
        <Input
          className="us-input flex-1"
          placeholder="加个愿望：想学滑板 / 想去海边…"
          placeholderClass="us-input-ph"
          value={draft}
          onInput={(e) => setDraft(e.detail.value)}
          onConfirm={handleAdd}
        />
        <View
          className={`us-btn ${adding || !draft.trim() ? 'us-btn-disabled' : ''}`}
          onClick={adding || !draft.trim() ? undefined : handleAdd}
        >
          {adding ? '加…' : '加愿望'}
        </View>
      </View>

      {/* 共同愿望 */}
      <View className="mb-10">
        <Text className="us-serif text-lg mb-3 block">我们的共同愿望</Text>
        {common.length === 0 ? (
          <Text className="text-sm text-stone-400 leading-relaxed block">
            还没有撞在一起的愿望。等你们各自多丢几条，AI 会发现"原来你也想"。
          </Text>
        ) : (
          <View className="flex flex-col gap-4">
            {common.map((c, i) => {
              const planEntry = c.wish_ids[0] ? plans[c.wish_ids[0]] : undefined
              return (
                <View
                  key={i}
                  className="us-rise bg-white/60 rounded-2xl p-5"
                  style={{ animationDelay: `${i * 80}ms` }}
                >
                  <Text className="text-sm text-stone-500 mb-1 block">
                    {c.matched_users.join(' 和 ')} 都想
                  </Text>
                  <Text className="text-base font-medium leading-relaxed block">{c.content}</Text>
                  <Text className="text-sm text-stone-500 mt-2 leading-relaxed block">
                    {c.suggestion}
                  </Text>
                  {planEntry ? (
                    <PlanCard plan={planEntry.plan} participants={planEntry.participants} />
                  ) : (
                    <View
                      className={`us-btn-ghost border border-[#264653]/15 text-xs mt-3 ${
                        planningId === c.wish_ids[0] ? 'us-btn-disabled' : ''
                      }`}
                      onClick={
                        planningId === c.wish_ids[0] ? undefined : () => handlePlan(c.wish_ids)
                      }
                    >
                      {planningId === c.wish_ids[0] ? '生成中…' : '生成「一起去」方案'}
                    </View>
                  )}
                </View>
              )
            })}
          </View>
        )}
      </View>

      {/* 我的愿望 */}
      <View className="mb-8">
        <Text className="us-serif text-lg mb-3 block">我的愿望</Text>
        {myWishes.length === 0 ? (
          <Text className="text-sm text-stone-400 block">还没有，写一个呗</Text>
        ) : (
          <View className="flex flex-col">
            {myWishes.map((w, i) => (
              <View
                key={w.id}
                className="us-rise py-3 border-b border-[#264653]/10 flex items-center gap-3"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <Text className="us-chip shrink-0">{CATEGORY_LABEL[w.category] ?? '想做'}</Text>
                <Text className="text-sm leading-relaxed">{w.content}</Text>
              </View>
            ))}
          </View>
        )}
      </View>

      {/* 朋友们的愿望 */}
      {others.length > 0 && (
        <View>
          <Text className="us-serif text-lg mb-3 block">朋友们的愿望</Text>
          <View className="flex flex-col">
            {others.map((w, i) => (
              <View
                key={w.id}
                className="us-rise py-3 border-b border-[#264653]/10 flex items-center gap-3"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <Text className="us-chip shrink-0">{CATEGORY_LABEL[w.category] ?? '想做'}</Text>
                <Text className="text-sm leading-relaxed flex-1">{w.content}</Text>
                <Text className="text-xs text-stone-400 shrink-0">{w.user_nickname}</Text>
              </View>
            ))}
          </View>
        </View>
      )}
    </View>
  )
}
