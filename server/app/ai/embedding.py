"""纯文本 embedding（OpenAI 兼容 /embeddings）+ 图片向量（/embeddings/multimodal 子路径），
httpx 直连 REST。

dimensions 显式传 EMBEDDING_DIM，保证全库同维度；向量维度以实际返回长度为准，不硬编码。
"""
import base64
import time

import numpy as np
import httpx

from ..config import settings


def embed(text: str) -> np.ndarray:
    """文本向量：OpenAI 兼容 /embeddings，dimensions 显式指定。"""
    url = f"{settings.EMBEDDING_BASE_URL.rstrip('/')}/embeddings"
    payload = {
        "model": settings.EMBEDDING_MODEL,
        "input": text,
        "dimensions": settings.EMBEDDING_DIM,
    }
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"},
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    return np.asarray(resp.json()["data"][0]["embedding"], dtype=np.float32)


def embed_image(image_bytes: bytes, fmt: str = "jpeg") -> np.ndarray:
    """图片向量：多模态 embedding（doubao-embedding-vision），走 /embeddings/multimodal
    子路径（响应的 data 是单个对象而非数组，与文本版不同）。

    注意：部分网关（如火山 plan 网关）的 /embeddings 只收纯文本，图片必须走子路径。
    """
    url = f"{settings.EMBEDDING_BASE_URL.rstrip('/')}/embeddings/multimodal"
    data_url = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode()}"
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"},
        json={
            "model": settings.EMBEDDING_MODEL,
            "input": [{"type": "image_url", "image_url": {"url": data_url}}],
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    return np.asarray(resp.json()["data"]["embedding"], dtype=np.float32)


def embed_batch(texts: list[str]) -> list[np.ndarray]:
    """批量文本向量：OpenAI 兼容 /embeddings 的 input 支持数组，一次请求多条。

    分块 10 条/次：火山的 doubao-embedding 单次 input 上限就是 10
    （"max 10, got 64"，见 docs/BUG记录.md BUG-015）；按返回的 index 对齐入参顺序。
    启动灌库（几百条食物名）走这里，逐条调会把启动卡到分钟级。
    """
    if not texts:
        return []
    url = f"{settings.EMBEDDING_BASE_URL.rstrip('/')}/embeddings"
    out: list[np.ndarray] = []
    for i in range(0, len(texts), 10):
        chunk = texts[i : i + 10]
        # 火山等厂商有限流（429）：短暂退避重试，灌库场景宁可慢也别断
        resp = None
        for attempt in range(3):
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"},
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
