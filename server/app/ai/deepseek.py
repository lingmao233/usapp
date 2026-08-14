"""DeepSeek API（OpenAI 兼容格式），httpx 直连 REST。"""
import json
import logging
import re

import httpx

from ..config import settings

logger = logging.getLogger("us.ai.deepseek")


def chat(prompt: str, json_mode: bool = False, timeout: float = 60.0) -> str:
    """调用 deepseek-chat，返回文本内容。"""
    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    payload: dict = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def chat_json(prompt: str, timeout: float = 60.0) -> dict:
    """调用并解析 JSON 输出，容错提取第一个 {...} 块。"""
    text = chat(prompt, json_mode=True, timeout=timeout)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
