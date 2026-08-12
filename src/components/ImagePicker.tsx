import { useRef, useState } from "react"
import { ImagePlus, X } from "lucide-react"
import { prepareImage, type PreparedImage } from "@/lib/image"

export interface PickedImage extends PreparedImage {
  /** objectURL 预览，组件内部负责 revoke */
  preview: string
}

/** 图片选择按钮 + 已选缩略图预览（发图片）：选中即压缩出展示图（原图保留不压），
 * 失败/超限就地提示。
 *
 * web 与 PWA 共用：<input type="file" accept="image/*"> 在桌面是本地文件，
 * 在手机浏览器是相册/拍照，无需任何原生开发。
 */
export default function ImagePicker({
  image,
  onChange,
}: {
  image: PickedImage | null
  onChange: (img: PickedImage | null) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = "" // 允许重选同一张图
    if (!file) return
    setBusy(true)
    setError("")
    try {
      const prepared = await prepareImage(file)
      if (image) URL.revokeObjectURL(image.preview)
      onChange({ ...prepared, preview: URL.createObjectURL(prepared.display) })
    } catch (err) {
      setError(err instanceof Error ? err.message : "图片处理失败，换一张试试")
    } finally {
      setBusy(false)
    }
  }

  function clear() {
    if (image) URL.revokeObjectURL(image.preview)
    onChange(null)
    setError("")
  }

  return (
    <span className="inline-flex items-center gap-2">
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={handleFile}
      />
      {image ? (
        <span className="relative inline-block">
          <img src={image.preview} className="h-12 w-12 rounded-lg object-cover" alt="已选图片" />
          <button
            className="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-stone-500/90 text-white flex items-center justify-center"
            onClick={clear}
            aria-label="移除图片"
          >
            <X className="w-3 h-3" />
          </button>
        </span>
      ) : (
        <button
          className="us-btn-ghost text-xs flex items-center gap-1"
          disabled={busy}
          onClick={() => inputRef.current?.click()}
        >
          <ImagePlus className="w-4 h-4" />
          {busy ? "处理中…" : "图片"}
        </button>
      )}
      {error && <span className="text-xs text-red-500">{error}</span>}
    </span>
  )
}
