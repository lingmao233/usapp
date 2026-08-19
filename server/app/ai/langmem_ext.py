"""langmem 集成（仅真实模式）：L1 原子记忆抽取。

模型适配走 settings 的 OpenAI 兼容配置（LLM_BASE_URL/LLM_API_KEY/LLM_MODEL），
经 langchain-openai 的 ChatOpenAI 传给 langmem。本文件只在配好 LLM key 的真实路径
被调用——测试里 ai 门面被 tests/fakes.py 确定性桩整体替换，绝不触达本模块。
"""
import logging

from langchain_openai import ChatOpenAI
from langmem import create_memory_manager
from pydantic import BaseModel, Field

from ..config import settings

logger = logging.getLogger("us.ai.langmem_ext")


class PreferenceMemory(BaseModel):
    """用户的喜好/雷点（一条一个）。"""

    content: str = Field(description="一句话描述的偏好，如「喜欢辣的」「讨厌被说教」")


class FactMemory(BaseModel):
    """关于用户的稳定事实（身份/工作/家庭/住处等）。"""

    content: str = Field(description="一句话描述的事实")


class EventMemory(BaseModel):
    """用户经历的事件（带时间锚点优先）。"""

    content: str = Field(description="一句话描述的事件")


class CommitmentMemory(BaseModel):
    """用户的承诺/打算/决定。"""

    content: str = Field(description="一句话描述的待跟进事项")


_SCHEMA_KINDS = {
    "PreferenceMemory": "preference",
    "FactMemory": "fact",
    "EventMemory": "event",
    "CommitmentMemory": "commitment",
}

_INSTRUCTIONS = """你在为私密树洞对话维护用户的长期记忆。从本轮对话中抽取值得记住的条目：
- 偏好（喜欢/讨厌的东西、沟通方式偏好）→ PreferenceMemory
- 稳定事实（职业、住处、重要的人）→ FactMemory
- 经历的事件（尽量带时间锚点）→ EventMemory
- 承诺与打算 → CommitmentMemory
一条一个事实，用用户的原话风格简述；闲聊客套、情绪波动本身不记。
没有值得记住的就一条都不返回。"""

_manager = None


def _get_manager():
    """懒建全局 manager（真实模式进程内复用；model 配置变化需重启进程）。"""
    global _manager
    if _manager is None:
        llm = ChatOpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            temperature=0,
        )
        _manager = create_memory_manager(
            llm,
            schemas=[PreferenceMemory, FactMemory, EventMemory, CommitmentMemory],
            instructions=_INSTRUCTIONS,
            enable_updates=False,  # L1 只增不改；巩固/去重留给 L2/L3 与后台任务
            enable_deletes=False,
        )
    return _manager


def extract_atoms(user_message: str, assistant_reply: str = "") -> list[dict]:
    """langmem 抽取一轮对话的原子记忆，返回 [{kind, content}]（与 mock 桩同契约）。"""
    messages = [{"role": "user", "content": user_message}]
    if assistant_reply:
        messages.append({"role": "assistant", "content": assistant_reply})
    memories = _get_manager().invoke({"messages": messages})
    atoms = []
    for item in memories:
        content = getattr(item, "content", None)
        text = str(getattr(content, "content", "") or "").strip()
        if not text:
            continue
        atoms.append({"kind": _SCHEMA_KINDS.get(type(content).__name__, "fact"),
                      "content": text})
    return atoms
