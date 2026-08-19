"""视觉模型（OpenAI 兼容 chat/completions 多模态消息）：图片 caption 与结构化识别。

开关在调用方（settings.vision_enabled）；VISION_REASONING 非空时传 reasoning_effort 压思考成本。
"""
import base64
import json
import os
import re

import httpx

from ..config import settings


def _apply_reasoning(payload: dict, level: str) -> None:
    """把思考强度写进请求体。off → enable_thinking=false（阿里系关思考）；
    其余档 → reasoning_effort（豆包/OpenAI 系）。两种字段不支持的厂商会忽略，调用方无感。"""
    if not level:
        return
    if level == "off":
        payload["enable_thinking"] = False
    else:
        payload["reasoning_effort"] = level


def vision_caption(image_bytes: bytes, fmt: str = "jpeg", reasoning: str = "") -> str:
    """视觉模型生成检索导向的中文图片描述（caption）。调用方负责开关与失败兜底。

    reasoning 为场景思考强度（off/minimal/low/medium/high），空 = 回退全局 VISION_REASONING。
    caption 不直接展示给用户，而是作为图片内容的文字桥进入向量（embedding 输入 =
    正文 + caption）、参与分类与周报上下文，所以要装满可检索信息：图中文字尽量
    转录，主体/场景/关键物体/氛围都写出来。
    """
    data_url = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode()}"
    url = f"{settings.VISION_BASE_URL.rstrip('/')}/chat/completions"
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
    # 深度思考类模型可压思考强度省 token；未配置时不传该参，对不支持它的模型零风险
    _apply_reasoning(payload, reasoning or settings.VISION_REASONING)
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.VISION_API_KEY}"},
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    return str(resp.json()["choices"][0]["message"]["content"]).strip()


def vision_json(image_path: str, prompt: str, reasoning: str = "") -> dict | list:
    """视觉模型结构化识别：图片 + prompt（要求只输出 JSON），返回解析后的 JSON。

    未配置视觉模型抛 RuntimeError；剥掉可能的 markdown ```json fence 后解析，
    解析失败抛异常（让上层走降级）。reasoning 为场景思考强度，空 = 回退全局。
    """
    if not settings.VISION_MODEL:
        raise RuntimeError("未配置 VISION_MODEL")
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    fmt = os.path.splitext(image_path)[1].lstrip(".").lower().replace("jpg", "jpeg") or "jpeg"
    data_url = f"data:image/{fmt};base64,{base64.b64encode(image_bytes).decode()}"
    url = f"{settings.VISION_BASE_URL.rstrip('/')}/chat/completions"
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
    # 深度思考类模型可压思考强度省 token；未配置时不传该参，对不支持它的模型零风险
    _apply_reasoning(payload, reasoning or settings.VISION_REASONING)
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.VISION_API_KEY}"},
        json=payload,
        timeout=30.0,
    )
    resp.raise_for_status()
    text = str(resp.json()["choices"][0]["message"]["content"]).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
