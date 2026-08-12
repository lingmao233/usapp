"""图片上传（碎片/愿望配图）：multipart 手写最小解析（零新依赖，不装 python-multipart）。

双份存储：原图 {uuid4hex}.{ext}（≤20MB 不压）+ 可选展示图 {uuid4hex}_d.jpg（总是 jpg）。
文件落在 server/data/uploads/（跟随 DB_PATH 所在目录，测试库自动隔离），
经 GET /api/uploads/{filename} 读回；开发模式走 vite 现有 /api 代理，无需改 vite 配置。
"""
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ..config import settings

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

# content-type → 扩展名（不信任用户文件名，扩展名按类型映射）
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_SIZE = 20 * 1024 * 1024  # 20MB（原图不压直传）
_MEDIA_BY_EXT = {v: k for k, v in ALLOWED_TYPES.items()}

# 读回防穿越：只放行本模块生成的文件名形状（_d 是 1600px 展示图副本约定）
_FILENAME_RE = re.compile(r"^[0-9a-f]{32}(_d)?\.(jpg|png|webp|gif)$")


def upload_dir() -> Path:
    """上传目录：跟随 DB_PATH 所在目录（server/data/uploads），不存在则创建。"""
    d = settings.upload_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _parse_parts(request: Request) -> dict[str, tuple[str, bytes]]:
    """最小 multipart 解析：{part 名: (content_type, payload)}，只收带 filename 的文件 part。"""
    content_type = request.headers.get("content-type", "")
    m = re.match(r"multipart/form-data;\s*boundary=(.+)", content_type)
    if not m:
        raise HTTPException(status_code=400, detail="只支持 multipart/form-data 上传")
    boundary = m.group(1).strip().strip('"').encode()
    body = await request.body()
    parts: dict[str, tuple[str, bytes]] = {}
    for part in body.split(b"--" + boundary):
        header, sep, payload = part.partition(b"\r\n\r\n")
        if not sep or b"filename=" not in header:
            continue
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]  # 剥掉分隔符前的 CRLF
        name_m = re.search(rb'name="([^"]+)"', header)
        ct = ""
        hm = re.search(rb"Content-Type:\s*([^\r\n]+)", header, re.IGNORECASE)
        if hm:
            ct = hm.group(1).decode().strip()
        if name_m:
            parts[name_m.group(1).decode()] = (ct, payload)
    return parts


@router.post("")
async def upload_image(request: Request):
    """双份存储（图片智能化）：file=原图（≤20MB 不压），可选 display=1600px 展示图（总是 jpg）。
    原图存 {uuid}.{ext}，展示图存 {uuid}_d.jpg；image_url 只记原图，展示图 URL 按约定推导。"""
    parts = await _parse_parts(request)
    if "file" not in parts:
        raise HTTPException(status_code=400, detail="请求里没有文件")
    ct, payload = parts["file"]
    ext = ALLOWED_TYPES.get(ct)
    if ext is None:
        raise HTTPException(status_code=400, detail="只支持 JPEG/PNG/WebP/GIF 图片")
    if len(payload) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="图片不能超过 20MB")
    stem = uuid.uuid4().hex
    (upload_dir() / f"{stem}{ext}").write_bytes(payload)
    # 展示图副本：只在确为 jpeg 时按约定落盘（异常副本宁可不要，前端 onError 会回退原图）
    display = parts.get("display")
    if display and display[0] == "image/jpeg" and 0 < len(display[1]) <= MAX_SIZE:
        (upload_dir() / f"{stem}_d.jpg").write_bytes(display[1])
    return {"url": f"/api/uploads/{stem}{ext}"}


@router.get("/{filename}")
def get_upload(filename: str):
    if not _FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = upload_dir() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, media_type=_MEDIA_BY_EXT[path.suffix])
