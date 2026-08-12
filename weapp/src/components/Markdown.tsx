/** 极简 Markdown 渲染：周报用（标题/列表/加粗/段落）。小程序端：View/Text 实现。 */
import React from 'react'
import { Text, View } from '@tarojs/components'

function inline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*)/g)
  return parts.map((p, i) =>
    p.startsWith('**') && p.endsWith('**') ? (
      <Text key={i} className="font-semibold text-[#264653]">
        {p.slice(2, -2)}
      </Text>
    ) : (
      <React.Fragment key={i}>{p}</React.Fragment>
    ),
  )
}

export default function Markdown({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <View className="flex flex-col gap-2 leading-relaxed">
      {lines.map((line, i) => {
        const t = line.trim()
        if (!t) return <View key={i} className="h-1" />
        if (t.startsWith('## '))
          return (
            <Text key={i} className="us-serif text-lg mt-4 block">
              {inline(t.slice(3))}
            </Text>
          )
        if (t.startsWith('# '))
          return (
            <Text key={i} className="us-serif text-2xl block">
              {inline(t.slice(2))}
            </Text>
          )
        if (t.startsWith('- '))
          return (
            <View key={i} className="flex gap-2 pl-1">
              <Text className="text-[#F4A261] mt-0.5">•</Text>
              <Text className="flex-1">{inline(t.slice(2))}</Text>
            </View>
          )
        return (
          <Text key={i} className="block">
            {inline(t)}
          </Text>
        )
      })}
    </View>
  )
}
