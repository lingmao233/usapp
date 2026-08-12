"""豆包多模态 embedding 与视觉模型，火山方舟接口。

文字与图片统一走 /embeddings/multimodal（图文同空间同维度，dimensions 显式指定），
旧的 /embeddings 纯文本端点已废弃。向量维度用实际返回长度，不硬编码。
参考：https://docs.volcengine.com/docs/82379/1523520
"""
import base64

import numpy as np
import httpx

from ..config import settings


def _embed_input(item: dict) -> np.ndarray:
    """多模态端点单条输入：dimensions 显式指定，文字/图片调用同值，保证同维度。"""
    url = f"{settings.DOUBAO_BASE_URL.rstrip('/')}/embeddings/multimodal"
    payload = {
        "model": settings.DOUBAO_EMBEDDING_MODEL,
        "input": [item],
        "dimensions": settings.DOUBAO_EMBED_DIM,
    }
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.DOUBAO_API_KEY}"},
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    # 多模态端点的 data 是单对象（旧文本端点是数组），兼容两种形状
    data = resp.json()["data"]
    vec = data["embedding"] if isinstance(data, dict) else data[0]["embedding"]
    return np.asarray(vec, dtype=np.float32)


def embed(text: str) -> np.ndarray:
    """文本向量：多模态端点文字入参。"""
    return _embed_input({"type": "text", "text": text})


def embed_image(image_bytes: bytes, fmt: str = "jpeg") -> np.ndarray:
    """图片向量：多模态端点图片入参（data URL base64，单图 ≤10MB）。"""
    data_url = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode()}"
    return _embed_input({"type": "image_url", "image_url": {"url": data_url}})


def vision_caption(image_bytes: bytes, fmt: str = "jpeg") -> str:
    """视觉模型生成一句中文图片描述（caption）。调用方负责开关与失败兜底。"""
    data_url = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode()}"
    url = f"{settings.DOUBAO_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.DOUBAO_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": "用一句中文描述这张图片，30 字以内，只输出描述本身。"},
                ],
            }
        ],
    }
    # 深度思考类模型（如 doubao-seed-evolving 默认 thinking=high）可压思考强度省 token；
    # 未配置时不传该参，对不支持它的模型零风险
    if settings.DOUBAO_VISION_REASONING:
        payload["reasoning_effort"] = settings.DOUBAO_VISION_REASONING
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.DOUBAO_API_KEY}"},
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    return str(resp.json()["choices"][0]["message"]["content"]).strip()
