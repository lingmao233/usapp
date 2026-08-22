"""视觉模型（OpenAI 兼容 chat/completions 多模态消息）：图片 caption 与结构化识别。

开关在调用方（settings.vision_enabled）；VISION_REASONING 非空时传 reasoning_effort 压思考成本。
"""
import base64
import json
import logging
import os
import re

import httpx

from ..config import settings

logger = logging.getLogger("us.ai.vision")


def _post(payload: dict, timeout: float = 60.0) -> dict:
    """POST chat/completions。识别类任务统一 temperature=0（压采样波动，同图同结果）。

    厂商容错：阿里部分端点只认 minimal/off，把 low/medium/high 映射成非法 thinking_budget
    直接 400（"The thinking_budget parameter must be..."）——剥掉思考参数重试一次，
    不思考好过整个功能不可用（见 docs/BUG记录.md BUG-018）。
    """
    payload.setdefault("temperature", 0)
    url = f"{settings.VISION_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.VISION_API_KEY}"}
    resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    # getattr 兼容测试桩（桩的 Resp 没有 status_code）
    if getattr(resp, "status_code", None) == 400:
        from . import reasoning

        if reasoning.is_set(payload) and reasoning.maybe_retry_note(resp.text):
            logger.warning("视觉厂商不接受思考参数（%s），剥掉重试一次", resp.text[:120])
            resp = httpx.post(url, headers=headers, json=reasoning.strip(payload), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _apply_reasoning(payload: dict, level: str) -> None:
    """把思考强度写进请求体。档位语义与跨厂商写法统一在 ai/reasoning.py（与 llm 层共用）。"""
    from . import reasoning

    reasoning.apply(payload, level)


def vision_ask(image_bytes: bytes, prompt: str, fmt: str = "jpeg", reasoning: str = "",
               timeout: float = 60.0) -> str:
    """视觉模型单轮问答：图片（base64 内嵌）+ 自定义 prompt。调用方负责开关与失败兜底。"""
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
    _apply_reasoning(payload, reasoning or settings.VISION_REASONING)
    return str(_post(payload, timeout)["choices"][0]["message"]["content"]).strip()


def vision_caption(image_bytes: bytes, fmt: str = "jpeg", reasoning: str = "") -> str:
    """视觉模型生成检索导向的中文图片描述（caption）。调用方负责开关与失败兜底。

    reasoning 为场景思考强度（off/minimal/low/medium/high），空 = 回退全局 VISION_REASONING。
    caption 不直接展示给用户，而是作为图片内容的文字桥进入向量（embedding 输入 =
    正文 + caption）、参与分类与周报上下文，所以要装满可检索信息：图中文字尽量
    转录，主体/场景/关键物体/氛围都写出来。
    """
    return vision_ask(
        image_bytes,
        "详细描述这张图片，供语义检索使用，120 字以内："
        "1) 图中有文字（聊天截图、小票、账单、文档等）就尽量转录关键文字；"
        "2) 写清画面主体、场景、关键物体；3) 点出情绪或氛围。"
        "只输出描述本身，不要分点、不要解释。",
        fmt,
        reasoning,
    )


def vision_json(image_path: str, prompt: str, reasoning: str = "",
                 timeout: float = 60.0) -> dict | list:
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
    text = str(_post(payload, timeout)["choices"][0]["message"]["content"]).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\[.*\]|\{.*\})", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
