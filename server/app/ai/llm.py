"""文本 LLM（OpenAI 兼容 chat/completions），httpx 直连 REST。"""
import json
import logging
import re

import httpx

from ..config import settings

logger = logging.getLogger("us.ai.llm")

# cfg 三元组：(api_key, base_url, model)；None = 用 LLM_* 组（现状不变）
LlmCfg = tuple[str, str, str]


def _resolve(cfg: LlmCfg | None) -> LlmCfg:
    return cfg or (settings.LLM_API_KEY, settings.LLM_BASE_URL, settings.LLM_MODEL)


def _temperature() -> float:
    """采样温度：LLM_TEMPERATURE 可配（Kimi k3 等推理模型只接受 1），非法值回退 0.7。"""
    try:
        return float(settings.LLM_TEMPERATURE)
    except (TypeError, ValueError):
        return 0.7


def chat(prompt: str, json_mode: bool = False, timeout: float = 60.0,
         enable_search: bool = False, cfg: LlmCfg | None = None) -> str:
    """调用 LLM_MODEL，返回文本内容。

    enable_search 是厂商相关参数（阿里百炼的联网开关写法：请求体顶层 enable_search=true），
    只在 web_search_food 这类需要联网核验的场景开启；厂商不支持会忽略或报错，
    由调用方走降级，不影响其他调用路径。
    """
    api_key, base_url, model = _resolve(cfg)
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": _temperature(),
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    if enable_search:  # 厂商依赖：阿里百炼写法，其他厂商未必识别
        payload["enable_search"] = True
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def chat_json(prompt: str, timeout: float = 60.0, enable_search: bool = False,
              cfg: LlmCfg | None = None) -> dict:
    """调用并解析 JSON 输出，容错提取第一个 {...} 块。enable_search 见 chat() 注释。"""
    text = chat(prompt, json_mode=True, timeout=timeout, enable_search=enable_search, cfg=cfg)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def chat_messages(messages: list[dict], cfg: LlmCfg | None = None, tools: list[dict] | None = None,
                  timeout: float = 120.0, max_tool_rounds: int = 3) -> str:
    """多消息 chat（支持多模态 content parts）+ tool_calls 循环。

    tools 目前服务于 Kimi 内置 $web_search（builtin_function）：模型只生成搜索参数，
    调用方把 arguments 以 role=tool 消息原样回传，由 Kimi 执行搜索并生成最终回复
    （官方回声协议，见 platform.moonshot.cn/docs/guide/use-web-search）。
    """
    api_key, base_url, model = _resolve(cfg)
    url = f"{base_url.rstrip('/')}/chat/completions"
    msgs = list(messages)
    for _ in range(max_tool_rounds + 1):
        payload: dict = {"model": model, "messages": msgs, "temperature": _temperature()}
        if tools:
            payload["tools"] = tools
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        choice = resp.json()["choices"][0]
        msg = choice["message"]
        tool_calls = msg.get("tool_calls") or []
        if choice.get("finish_reason") != "tool_calls" or not tool_calls or not tools:
            return msg.get("content") or ""
        # 回声协议：assistant 的 tool_calls 入列，再逐条回 role=tool（内容=原样 arguments）。
        # tool_calls 的 type 统一落成 OpenAI 线格式的 "function"——
        # Kimi 返回的 "builtin_function" 在 kimi.com/coding 网关上回显会 400（tokenization failed）
        msgs.append({"role": "assistant", "content": msg.get("content") or "",
                     "tool_calls": [{**tc, "type": "function"} for tc in tool_calls]})
        for tc in tool_calls:
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "name": (tc.get("function") or {}).get("name", ""),
                "content": (tc.get("function") or {}).get("arguments", "") or "",
            })
    logger.warning("tool_calls 循环超过 %d 轮仍未 stop，按最后一条内容返回", max_tool_rounds)
    return msg.get("content") or ""
