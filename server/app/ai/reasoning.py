"""思考强度参数（跨厂商写法）：vision 与 llm（树洞）共用，避免两处漂移。

档位语义：
- "off"  → enable_thinking=false（阿里 Qwen3 系开关写法）
- "on"   → enable_thinking=true
- "on:N" → enable_thinking=true + thinking_budget=N（阿里 Qwen3.x 思考预算 1~32768）
- minimal/low/medium/high/max → reasoning_effort（豆包/OpenAI 系；Kimi k3 为 low/high/max）

不支持的厂商会忽略或直接 400——调用层带「剥思考参数重试一次」容错（BUG-018 手法，
见 llm._one_round / vision._post）：不思考好过整个功能不可用。
"""
import logging

logger = logging.getLogger("us.ai.reasoning")


def is_set(payload: dict) -> bool:
    """payload 里是否带了思考参数（400 容错重试的判定条件）。"""
    return "reasoning_effort" in payload or "enable_thinking" in payload


def apply(payload: dict, level: str) -> None:
    """把思考强度写进请求体（level 空串不动）。"""
    if not level:
        return
    if level == "on":
        payload["enable_thinking"] = True
    elif level == "off":
        payload["enable_thinking"] = False
    elif level.startswith("on:"):
        # 阿里 Qwen3.x 原生强度写法：enable_thinking + thinking_budget（非法预算只开不限额）
        payload["enable_thinking"] = True
        try:
            budget = int(level[3:])
            if 1 <= budget <= 32768:
                payload["thinking_budget"] = budget
        except ValueError:
            pass
    else:
        payload["reasoning_effort"] = level


def strip(payload: dict) -> dict:
    """返回剔除思考参数的新 payload（400 容错重试用）。"""
    return {k: v for k, v in payload.items()
            if k not in ("reasoning_effort", "enable_thinking", "thinking_budget")}


def maybe_retry_note(status_body: str) -> bool:
    """400 响应体是否指向思考参数（剥参重试的触发判定，与 vision._post 同口径）。"""
    return "thinking" in status_body or "reasoning" in status_body
