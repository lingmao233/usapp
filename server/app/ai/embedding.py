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
