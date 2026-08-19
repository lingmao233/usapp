"""文本 LLM（OpenAI 兼容 chat/completions），httpx 直连 REST。"""
import json
import logging
import re

import httpx

from ..config import settings

logger = logging.getLogger("us.ai.llm")


def chat(prompt: str, json_mode: bool = False, timeout: float = 60.0, enable_search: bool = False) -> str:
    """调用 LLM_MODEL，返回文本内容。

    enable_search 是厂商相关参数（阿里百炼的联网开关写法：请求体顶层 enable_search=true），
    只在 web_search_food 这类需要联网核验的场景开启；厂商不支持会忽略或报错，
    由调用方走降级，不影响其他调用路径。
    """
    url = f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions"
    payload: dict = {
        "model": settings.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if enable_search:  # 厂商依赖：阿里百炼写法，其他厂商未必识别
        payload["enable_search"] = True
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def chat_json(prompt: str, timeout: float = 60.0, enable_search: bool = False) -> dict:
    """调用并解析 JSON 输出，容错提取第一个 {...} 块。enable_search 见 chat() 注释。"""
    text = chat(prompt, json_mode=True, timeout=timeout, enable_search=enable_search)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
