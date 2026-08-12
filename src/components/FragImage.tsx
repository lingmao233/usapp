import { displayUrl } from "@/lib/image"

/** 碎片/愿望配图：列表与卡片用展示图（{uuid}_d.jpg 约定推导），点击新窗口看原图；
 * 既有旧图没有展示副本，加载失败时回退原图。 */
export default function FragImage({
  url,
  className,
  alt,
}: {
  url: string
  className?: string
  alt: string
}) {
  return (
    <a href={url} target="_blank" rel="noreferrer" className="inline-block shrink-0">
      <img
        src={displayUrl(url)}
        onError={(e) => {
          // 旧图无展示副本：回退原图（已指向原图则不再重试，防死循环）
          if (!e.currentTarget.src.endsWith(url)) e.currentTarget.src = url
        }}
        className={className}
        alt={alt}
        loading="lazy"
      />
    </a>
  )
}
