"""视觉模型（OpenAI 兼容 chat/completions 多模态消息）：图片 caption 与结构化识别。"""
import base64
import json
import logging
import os
import re
import time

import httpx

from ..config import settings

logger = logging.getLogger("us.ai")

_NO_THINKING = {"", "0", "false", "off", "none", "disabled", "minimal"}


def _is_dashscope_qwen3_vl() -> bool:
    """阿里 Qwen3-VL 通过 enable_thinking 控制思考，不使用 reasoning_effort。"""
    model = settings.VISION_MODEL.strip().lower()
    base_url = settings.VISION_BASE_URL.strip().lower()
    return "dashscope" in base_url and model.startswith("qwen3-vl-")


def _apply_generation_options(payload: dict, *, json_mode: bool = False) -> None:
    reasoning = settings.VISION_REASONING.strip().lower()
    if _is_dashscope_qwen3_vl() and "thinking" not in settings.VISION_MODEL.lower():
        # Qwen3-VL 的 OpenAI Chat 接口用布尔开关。项目旧文档推荐的 minimal
        # 也映射为关闭，避免看似最低档、实际仍进入思考的高延迟。
        payload["enable_thinking"] = reasoning not in _NO_THINKING
        if json_mode and not payload["enable_thinking"]:
            payload["response_format"] = {"type": "json_object"}
        return
    if settings.VISION_REASONING:
        payload["reasoning_effort"] = settings.VISION_REASONING


def _post(payload: dict, *, operation: str, image_bytes: int):
    started = time.perf_counter()
    try:
        resp = httpx.post(
            f"{settings.VISION_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.VISION_API_KEY}"},
            json=payload,
            timeout=30.0,
        )
    finally:
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        logger.info(
            "vision %s model=%s image_kb=%d elapsed_ms=%d",
            operation,
            settings.VISION_MODEL,
            round(image_bytes / 1024),
            elapsed_ms,
        )
    resp.raise_for_status()
    return resp


def vision_caption(image_bytes: bytes, fmt: str = "jpeg") -> str:
    """视觉模型生成检索导向的中文图片描述（caption）。调用方负责开关与失败兜底。

    caption 不直接展示给用户，而是作为图片内容的文字桥进入向量（embedding 输入 =
    正文 + caption）、参与分类与周报上下文，所以要装满可检索信息：图中文字尽量
    转录，主体/场景/关键物体/氛围都写出来。
    """
    data_url = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode()}"
    payload = {
        "model": settings.VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": (
                        "详细描述这张图片，供语义检索使用，120 字以内："
                        "1) 图中有文字（聊天截图、小票、账单、文档等）就尽量转录关键文字；"
                        "2) 写清画面主体、场景、关键物体；3) 点出情绪或氛围。"
                        "只输出描述本身，不要分点、不要解释。"
                    )},
                ],
            }
        ],
    }
    _apply_generation_options(payload)
    resp = _post(payload, operation="caption", image_bytes=len(image_bytes))
    return str(resp.json()["choices"][0]["message"]["content"]).strip()


def vision_json(image_path: str, prompt: str) -> dict | list:
    """视觉模型结构化识别：图片 + prompt（要求只输出 JSON），返回解析后的 JSON。

    未配置视觉模型抛 RuntimeError；剥掉可能的 markdown ```json fence 后解析，
    解析失败抛异常（让上层走降级）。
    """
    if not settings.VISION_MODEL:
        raise RuntimeError("未配置 VISION_MODEL")
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    fmt = os.path.splitext(image_path)[1].lstrip(".").lower().replace("jpg", "jpeg") or "jpeg"
    data_url = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode()}"
    payload = {
        "model": settings.VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }
    _apply_generation_options(payload, json_mode=True)
    resp = _post(payload, operation="json", image_bytes=len(image_bytes))
    text = str(resp.json()["choices"][0]["message"]["content"]).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
