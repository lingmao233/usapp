"""文本 LLM（OpenAI 兼容 chat/completions），httpx 直连 REST。"""
import json
import logging
import re

import httpx

from ..config import settings
from . import reasoning as reasoning_mod

logger = logging.getLogger("us.ai.llm")

# cfg 三元组：(api_key, base_url, model)；None = 用 LLM_* 组（现状不变）
LlmCfg = tuple[str, str, str]


def _resolve(cfg: LlmCfg | None) -> LlmCfg:
    return cfg or (settings.LLM_API_KEY, settings.LLM_BASE_URL, settings.LLM_MODEL)


def resolve_temperature() -> float:
    """采样温度：LLM_TEMPERATURE 可配（Kimi k3 等推理模型只接受 1），非法值回退 0.7。
    全部 LLM 出口共用这一份解析（llm.chat/chat_messages 与 langmem 的 ChatOpenAI）——
    曾有 langmem 侧写死 0 被 k3 全量 400（BUG-024），温度口径必须单点。"""
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
        "temperature": resolve_temperature(),
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


class _StreamAccum:
    """chat/completions 流式 SSE 增量累积器（纯逻辑，可离线单测）。

    feed(data_str) 返回本段新增的可外发内容；tool_calls 的增量字段（id/name/arguments
    分片到达）按 index 拼接。finalize() 还原成 OpenAI 线格式的 assistant 消息——
    tool_calls 的 type 统一落 "function"（Kimi 回显的 builtin_function 在 kimi.com/coding
    网关上回传会 400，见 BUG-017）。
    """

    def __init__(self) -> None:
        self.content_parts: list[str] = []
        self.tool_calls: dict[int, dict] = {}
        self.finish_reason = ""

    def feed(self, data: str) -> str:
        chunk = json.loads(data)
        choice = (chunk.get("choices") or [{}])[0]
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        out = ""
        if delta.get("content"):
            out = str(delta["content"])
            self.content_parts.append(out)
        for tc in delta.get("tool_calls") or []:
            idx = int(tc.get("index") or 0)
            slot = self.tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            fn = tc.get("function") or {}
            slot["id"] += tc.get("id") or ""
            slot["name"] += fn.get("name") or ""
            slot["arguments"] += fn.get("arguments") or ""
        return out

    @property
    def saw_tool_call(self) -> bool:
        return any(t["name"] for t in self.tool_calls.values())

    def finalize(self) -> dict:
        calls = [
            {"id": t["id"] or f"call_{i}", "type": "function",
             "function": {"name": t["name"], "arguments": t["arguments"]}}
            for i, t in sorted(self.tool_calls.items()) if t["name"]
        ]
        return {"role": "assistant", "content": "".join(self.content_parts),
                "tool_calls": calls}


def _post_streaming(url: str, headers: dict, payload: dict, timeout: float,
                    on_delta) -> tuple[dict, str]:
    """流式请求一轮：返回 (assistant 消息, finish_reason)。内容增量经 on_delta 外发——
    本轮已出现 tool_calls 后的内容（罕见的前置叙述）缓冲到轮末不再外发，避免把
    「让我查查」这类过渡文本当成回复主体推给用户。"""
    accum = _StreamAccum()
    with httpx.stream("POST", url, headers=headers, json=payload, timeout=timeout) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                piece = accum.feed(data)
            except json.JSONDecodeError:
                continue  # 半截行/心跳注释，跳过
            if piece and not accum.saw_tool_call:
                on_delta(piece)
    return accum.finalize(), accum.finish_reason


def _one_round(url: str, headers: dict, payload: dict, timeout: float,
               on_delta) -> tuple[dict, str]:
    """打一轮请求（流式/整包），带「厂商拒思考参数 → 剥掉重试一次」容错。
    返回 (assistant 消息, finish_reason)。"""
    try:
        return _round_once(url, headers, payload, timeout, on_delta)
    except httpx.HTTPStatusError as exc:
        body = getattr(exc.response, "text", "") or ""
        if (getattr(exc.response, "status_code", None) == 400
                and reasoning_mod.is_set(payload) and reasoning_mod.maybe_retry_note(body)):
            logger.warning("LLM 厂商不接受思考参数（%s），剥掉重试一次", body[:120])
            return _round_once(url, headers, reasoning_mod.strip(payload), timeout, on_delta)
        raise


def _round_once(url: str, headers: dict, payload: dict, timeout: float,
                on_delta) -> tuple[dict, str]:
    if on_delta:
        return _post_streaming(url, headers, payload, timeout, on_delta)
    resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    choice = resp.json()["choices"][0]
    return choice["message"], choice.get("finish_reason") or ""


def chat_messages(messages: list[dict], cfg: LlmCfg | None = None, tools: list[dict] | None = None,
                  timeout: float = 120.0, max_tool_rounds: int = 3,
                  on_delta=None, reasoning: str = "") -> str:
    """多消息 chat（支持多模态 content parts）+ tool_calls 循环 + 可选流式。

    tools 目前服务于 Kimi 内置 $web_search（builtin_function）：模型只生成搜索参数，
    调用方把 arguments 以 role=tool 消息原样回传，由 Kimi 执行搜索并生成最终回复
    （官方回声协议，见 platform.moonshot.cn/docs/guide/use-web-search）。

    on_delta(text) 给定时整轮走流式：内容增量实时回调（工具轮的内容不外发），返回值
    仍为最终轮完整内容（与非流式口径一致，写入 L0 的与展示的由 done 事件对齐）。
    reasoning 为思考强度档位（off/on/on:N/minimal/low/medium/high/max，见 ai/reasoning.py，
    树洞的「思考程度」参数走这里）：厂商不认会 400，剥掉思考参数重试一次——
    不思考好过整个调用不可用（BUG-018 手法）。
    """
    api_key, base_url, model = _resolve(cfg)
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}"}
    msgs = list(messages)
    msg: dict = {}
    for _ in range(max_tool_rounds + 1):
        payload: dict = {"model": model, "messages": msgs, "temperature": resolve_temperature()}
        if tools:
            payload["tools"] = tools
        if on_delta:
            payload["stream"] = True
        reasoning_mod.apply(payload, reasoning)
        msg, finish = _one_round(url, headers, payload, timeout, on_delta)
        tool_calls = msg.get("tool_calls") or []
        if finish != "tool_calls" or not tool_calls or not tools:
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
