/** 圈子人格（web 端展示元数据）：与后端 PERSONAS 五套预设一一对应。 */

export interface PersonaPreset {
  key: string
  label: string
  desc: string
}

export const DEFAULT_PERSONA_KEY = "observer"

export const PERSONA_PRESETS: PersonaPreset[] = [
  { key: "observer", label: "朋友圈观察员", desc: "轻松幽默，像朋友聊天" },
  { key: "sunshi", label: "损友", desc: "毒舌但暖心，吐槽里全是关心" },
  { key: "shudong", label: "温柔树洞", desc: "细腻共情，像深夜电台" },
  { key: "weekly", label: "编辑部周刊", desc: "正经媒体腔，拿小事当头条" },
  { key: "laba", label: "村口大喇叭", desc: "热情外放爱起哄，自来熟" },
]

export function personaLabel(key: string): string {
  return PERSONA_PRESETS.find((p) => p.key === key)?.label ?? PERSONA_PRESETS[0].label
}
