"""向量调用：httpx 直连 REST，URL 一律取配置里的完整端点（EMBEDDING_URL），
代码不做路径拼接——换厂商只需把控制台的完整 URL 抄进 .env。
文本与图片共用一个端点：支持图片向量的多模态模型本身就是单端点，纯文本模型没有图片能力。

payload/响应格式由模型名决定（含 "vision" = 多模态格式：input 段数组、响应 data 单对象、
无批量语义；否则 OpenAI 兼容格式：input 纯文本/数组、响应 data 数组）。
dimensions 显式传 EMBEDDING_DIM，保证全库同维度；向量维度以实际返回长度为准，不硬编码。
"""
import base64
import time

import numpy as np
import httpx

from ..config import settings


def _vision_model() -> bool:
    """vision 类多模态 embedding 模型（doubao-embedding-vision）：文本与图片走同一个
    多模态端点，文本包成 [{"type":"text",...}] 段。这类模型在标准网关不挂纯文本端点
    （"model does not support this api"），所以 EMBEDDING_URL 要配成多模态端点。"""
    return "vision" in settings.EMBEDDING_MODEL


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"}


def _embed_text_mm(text: str) -> np.ndarray:
    """vision 模型的文本向量：input 为 text 段；响应的 data 是单个对象（与图片版一致），不是数组。"""
    resp = httpx.post(
        settings.EMBEDDING_URL,
        headers=_headers(),
        json={
            "model": settings.EMBEDDING_MODEL,
            "input": [{"type": "text", "text": text}],
            "dimensions": settings.EMBEDDING_DIM,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return np.asarray(resp.json()["data"]["embedding"], dtype=np.float32)


def embed(text: str) -> np.ndarray:
    """文本向量：OpenAI 兼容格式，dimensions 显式指定。"""
    if _vision_model():
        return _embed_text_mm(text)
    resp = httpx.post(
        settings.EMBEDDING_URL,
        headers=_headers(),
        json={
            "model": settings.EMBEDDING_MODEL,
            "input": text,
            "dimensions": settings.EMBEDDING_DIM,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    return np.asarray(resp.json()["data"][0]["embedding"], dtype=np.float32)


def embed_image(image_bytes: bytes, fmt: str = "jpeg") -> np.ndarray:
    """图片向量：多模态 embedding，响应的 data 是单个对象而非数组。"""
    data_url = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode()}"
    resp = httpx.post(
        settings.EMBEDDING_URL,
        headers=_headers(),
        json={
            "model": settings.EMBEDDING_MODEL,
            "input": [{"type": "image_url", "image_url": {"url": data_url}}],
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return np.asarray(resp.json()["data"]["embedding"], dtype=np.float32)


def embed_batch(texts: list[str]) -> list[np.ndarray]:
    """批量文本向量：OpenAI 兼容格式的 input 支持数组，一次请求多条。

    分块 10 条/次：火山的 doubao-embedding 单次 input 上限就是 10
    （"max 10, got 64"，见 docs/BUG记录.md BUG-015）；按返回的 index 对齐入参顺序。
    启动灌库（几百条食物名）走这里，逐条调会把启动卡到分钟级。
    """
    if not texts:
        return []
    if _vision_model():
        # 多模态端点无批量语义（多段 input 会融合成单个向量），逐条调
        return [embed(t) for t in texts]
    out: list[np.ndarray] = []
    for i in range(0, len(texts), 10):
        chunk = texts[i : i + 10]
        # 火山等厂商有限流（429）：短暂退避重试，灌库场景宁可慢也别断
        resp = None
        for attempt in range(3):
            resp = httpx.post(
                settings.EMBEDDING_URL,
                headers=_headers(),
                json={
                    "model": settings.EMBEDDING_MODEL,
                    "input": chunk,
                    "dimensions": settings.EMBEDDING_DIM,
                },
                timeout=60.0,
            )
            if resp.status_code != 429:
                break
            time.sleep(2 * (attempt + 1))
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        out.extend(np.asarray(d["embedding"], dtype=np.float32) for d in data)
    return out
