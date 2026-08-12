/** 极简 Markdown 渲染：周报用（标题/列表/加粗/段落）。 */
import React from "react"

function inline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((p, i) =>
    p.startsWith("**") && p.endsWith("**") ? (
      <strong key={i} className="font-semibold text-[#264653]">
        {p.slice(2, -2)}
      </strong>
    ) : (
      <React.Fragment key={i}>{p}</React.Fragment>
    ),
  )
}

export default function Markdown({ text }: { text: string }) {
  const lines = text.split("\n")
  return (
    <div className="flex flex-col gap-2 leading-relaxed">
      {lines.map((line, i) => {
        const t = line.trim()
        if (!t) return <div key={i} className="h-1" />
        if (t.startsWith("## "))
          return (
            <h3 key={i} className="us-serif text-lg mt-4">
              {inline(t.slice(3))}
            </h3>
          )
        if (t.startsWith("# "))
          return (
            <h2 key={i} className="us-serif text-2xl">
              {inline(t.slice(2))}
            </h2>
          )
        if (t.startsWith("- "))
          return (
            <div key={i} className="flex gap-2 pl-1">
              <span className="text-[#F4A261] mt-0.5">•</span>
              <span className="flex-1">{inline(t.slice(2))}</span>
            </div>
          )
        return <p key={i}>{inline(t)}</p>
      })}
    </div>
  )
}
