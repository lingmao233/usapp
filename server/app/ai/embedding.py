"""纯文本 embedding（OpenAI 兼容 /embeddings），httpx 直连 REST。

dimensions 显式传 EMBEDDING_DIM，保证全库同维度；向量维度以实际返回长度为准，不硬编码。
"""
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
        resp.raise_for_status()
        data = sorted(resp.json()["data"], key=lambda d: d["index"])
        out.extend(np.asarray(d["embedding"], dtype=np.float32) for d in data)
    return out
