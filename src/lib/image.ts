/** 图片处理（碎片/愿望配图）：客户端双份产出——原图不压 + 1600px 展示图（JPEG 0.8），零依赖。
 *
 * 选图后即压缩，压缩失败（坏图/解码失败）或原图超限时抛中文错误文案，
 * 调用处 catch 后把 message 显示给用户。
 */

/** 与服务端一致的上限：原图 20MB（展示图压缩后远小于此） */
export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024

const MAX_EDGE = 1600
const VISION_EDGE = 800  // 识别副本：认菜名/读品牌 800px 足够，比 1600px 更快
const JPEG_QUALITY = 0.8

export interface PreparedImage {
  /** 原图（不压，直接上传） */
  original: File
  /** 1600px JPEG 展示图（上传为 {uuid}_d.jpg 副本；embedding 也用它） */
  display: Blob
  /** 800px JPEG 识别图（上传为 {uuid}_s.jpg 副本；视觉识别/caption 用它，更快） */
  vision: Blob
}

export async function prepareImage(file: File): Promise<PreparedImage> {
  if (file.size > MAX_UPLOAD_BYTES) throw new Error("图片太大（超过 20MB），换一张试试")
  return {
    original: file,
    display: await compressImage(file, MAX_EDGE),
    vision: await compressImage(file, VISION_EDGE),
  }
}

export async function compressImage(file: File, maxEdge: number = MAX_EDGE): Promise<Blob> {
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file)
  } catch {
    throw new Error("图片读取失败，换一张试试")
  }
  try {
    const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement("canvas")
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const ctx = canvas.getContext("2d")
    if (!ctx) throw new Error("图片处理失败，换一张试试")
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY),
    )
    if (!blob) throw new Error("图片处理失败，换一张试试")
    return blob
  } finally {
    bitmap.close()
  }
}

/** 展示图 URL：按约定从原图 URL 推导（{uuid}_d.jpg）；旧图没有展示副本时由 <img> onError 回退原图 */
export function displayUrl(imageUrl: string): string {
  const m = imageUrl.match(/^(\/api\/uploads\/[0-9a-f]{32})\.[a-z]+$/)
  return m ? `${m[1]}_d.jpg` : imageUrl
}
