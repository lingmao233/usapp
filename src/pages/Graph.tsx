import { useEffect, useMemo, useRef, useState } from "react"
import { api, type GraphEdge, type PairGraph, type Session } from "@/lib/api"
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"

// 辐射布局常量（viewBox 固定 400×400，随容器宽度自适应缩放）
const SIZE = 400
const CENTER = SIZE / 2
const R_MAX = 138 // score=0 的最远轨道
const R_MIN = 52 // score=1 的最近轨道（避开中心节点）
const NODE_R = 22
const FOCUS_R = 30

interface Point {
  x: number
  y: number
}

const pairKey = (a: string, b: string) => [a, b].sort().join("|")

/** 亲密度 → 线宽 4 档（护栏：界面任何位置不出现精确分数） */
function edgeWidth(score: number): number {
  if (score >= 0.75) return 5
  if (score >= 0.5) return 3.5
  if (score >= 0.25) return 2.25
  return 1.25
}

export default function Graph({ session }: { session: Session }) {
  const [graph, setGraph] = useState<PairGraph | null>(null)
  const [failed, setFailed] = useState(false)
  const [focus, setFocus] = useState(session.user_id)
  const [selected, setSelected] = useState<GraphEdge | null>(null)
  const [pos, setPos] = useState<Record<string, Point>>({})
  // 动画过程中的最新位置（ref 镜像，切换焦点时作为插值起点）
  const posRef = useRef<Record<string, Point>>({})

  useEffect(() => {
    api
      .pairGraph(session.circle_id, session.user_id)
      .then(setGraph)
      .catch(() => setFailed(true))
  }, [session.circle_id, session.user_id])

  const edgeByPair = useMemo(() => {
    const m = new Map<string, GraphEdge>()
    graph?.edges.forEach((e) => m.set(pairKey(e.user_a, e.user_b), e))
    return m
  }, [graph])

  const nickOf = useMemo(() => {
    const m = new Map<string, string>()
    graph?.nodes.forEach((n) => m.set(n.id, n.nickname))
    return m
  }, [graph])

  // 目标布局：焦点居中，其余成员按成员顺序固定角位分布在圆周上，半径 = 1 - score（越近越亲）
  useEffect(() => {
    if (!graph) return
    const total = graph.nodes.length
    const targets: Record<string, Point> = {}
    graph.nodes.forEach((n, i) => {
      if (n.id === focus) {
        targets[n.id] = { x: CENTER, y: CENTER }
        return
      }
      const score = edgeByPair.get(pairKey(focus, n.id))?.score ?? 0
      const r = R_MIN + (1 - score) * (R_MAX - R_MIN)
      const angle = -Math.PI / 2 + (i * 2 * Math.PI) / total
      targets[n.id] = { x: CENTER + r * Math.cos(angle), y: CENTER + r * Math.sin(angle) }
    })
    // rAF 插值动画：节点与连线共用同一位置数据源，天然同步重排
    const from = posRef.current
    const start = performance.now()
    let raf = 0
    const tick = (now: number) => {
      const t = Math.min((now - start) / 550, 1)
      const k = 1 - Math.pow(1 - t, 3) // easeOutCubic
      const next: Record<string, Point> = {}
      for (const [id, to] of Object.entries(targets)) {
        const f = from[id] ?? to // 新出现的节点直接落位，不飞入
        next[id] = { x: f.x + (to.x - f.x) * k, y: f.y + (to.y - f.y) * k }
      }
      posRef.current = next
      setPos(next)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [graph, focus, edgeByPair])

  if (failed) {
    return (
      <div className="max-w-2xl mx-auto px-5 py-16 text-center text-sm text-stone-400">
        关系图没加载出来，稍后刷新试试
      </div>
    )
  }
  if (!graph) {
    return (
      <div className="max-w-2xl mx-auto px-5 py-16 text-center text-sm text-stone-400">
        加载中…
      </div>
    )
  }

  // 空态：还没跑过 nightly（无 pair 数据）→ 引导文案
  if (graph.edges.length === 0) {
    return (
      <div className="max-w-2xl mx-auto px-5 py-8">
        <h2 className="us-serif text-2xl mb-1">关系图</h2>
        <div className="us-panel rounded-2xl mt-6 px-6 py-14 text-center">
          <p className="us-serif text-lg mb-2">关系图还在生成中</p>
          <p className="text-sm text-stone-500 leading-relaxed">
            每晚 AI 会悄悄读一遍大家的碎片，明天再来看看，就能看见谁和谁走得更近啦
          </p>
        </div>
      </div>
    )
  }

  const nameOf = (id: string) => (id === session.user_id ? "我" : (nickOf.get(id) ?? ""))

  return (
    <div className="max-w-2xl mx-auto px-5 py-8">
      <h2 className="us-serif text-2xl mb-1">关系图</h2>
      <p className="text-xs text-stone-500 mb-4">
        越靠中间越亲近 · 点成员换焦点，点连线看看共同主题
      </p>

      <svg
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        className="w-full max-w-[440px] mx-auto select-none"
        role="img"
        aria-label="圈子关系图"
      >
        {/* 连线：焦点连线醒目，其余淡显；线宽按亲密度分档，不出现精确分数 */}
        {graph.edges.map((e) => {
          const pa = pos[e.user_a]
          const pb = pos[e.user_b]
          if (!pa || !pb) return null
          const isFocusEdge = e.user_a === focus || e.user_b === focus
          const mid = { x: (pa.x + pb.x) / 2, y: (pa.y + pb.y) / 2 }
          return (
            <g key={pairKey(e.user_a, e.user_b)}>
              <line
                x1={pa.x}
                y1={pa.y}
                x2={pb.x}
                y2={pb.y}
                stroke={isFocusEdge ? "#264653" : "#A8A29E"}
                strokeWidth={edgeWidth(e.score)}
                strokeOpacity={isFocusEdge ? 0.75 : 0.25}
                strokeLinecap="round"
              />
              {/* 加宽透明热区，移动端好按 */}
              <line
                x1={pa.x}
                y1={pa.y}
                x2={pb.x}
                y2={pb.y}
                stroke="transparent"
                strokeWidth={20}
                className="cursor-pointer"
                onClick={() => setSelected(e)}
              />
              {/* 秘密共同愿望小标记（服务端保证仅当事人可见） */}
              {e.has_secret && (
                <text
                  x={mid.x}
                  y={mid.y}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={13}
                  className="pointer-events-none"
                >
                  ✨
                </text>
              )}
            </g>
          )
        })}

        {/* 成员节点：焦点大实心，其余白底描边；透明大圆保证触控目标 ≥40px */}
        {graph.nodes.map((n) => {
          const p = pos[n.id]
          if (!p) return null
          const isFocus = n.id === focus
          const r = isFocus ? FOCUS_R : NODE_R
          return (
            <g
              key={n.id}
              transform={`translate(${p.x},${p.y})`}
              className="cursor-pointer"
              onClick={() => setFocus(n.id)}
            >
              <circle r={Math.max(r + 8, 24)} fill="transparent" />
              <circle
                r={r}
                fill={isFocus ? "#264653" : "#FFFFFF"}
                stroke="#264653"
                strokeOpacity={isFocus ? 1 : 0.25}
                strokeWidth={isFocus ? 0 : 1.5}
              />
              <text
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={isFocus ? 16 : 13}
                fill={isFocus ? "#FFFFFF" : "#264653"}
                className="pointer-events-none"
              >
                {n.nickname.slice(0, 1)}
              </text>
              <text
                y={r + 13}
                textAnchor="middle"
                fontSize={11}
                fill={isFocus ? "#264653" : "#78716C"}
                className="pointer-events-none"
              >
                {nameOf(n.id)}
              </text>
            </g>
          )
        })}
      </svg>

      {/* 连线详情抽屉：共同主题（已按观看者过滤）+ 关系摘要；秘密愿望只说存在 */}
      <Sheet open={!!selected} onOpenChange={(open) => !open && setSelected(null)}>
        <SheetContent side="bottom" className="rounded-t-2xl">
          {selected && (
            <div className="w-full max-w-2xl mx-auto px-5 pb-8">
              <SheetHeader className="p-0 mb-4">
                <SheetTitle className="us-serif text-lg">
                  {nameOf(selected.user_a)} 和 {nameOf(selected.user_b)}
                </SheetTitle>
              </SheetHeader>
              {selected.topics.length > 0 ? (
                <div className="flex flex-wrap gap-2 mb-4">
                  {selected.topics.map((t) => (
                    <span key={t.tag} className="us-chip">
                      {t.tag}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-stone-400 mb-4">
                  还没有共同主题，多丢几条碎片试试
                </p>
              )}
              {selected.has_secret && (
                <p className="text-sm text-[#264653] mb-4">✨ 你们还有一个共同的秘密愿望</p>
              )}
              {selected.summary && (
                <p className="text-sm leading-relaxed text-stone-700">{selected.summary}</p>
              )}
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  )
}
