"""AI 统一接口：按 key 是否存在自动选择真实 API 或 mock 桩。"""
import json
import logging
import threading

import numpy as np

from ..config import settings
from . import deepseek, doubao, mock
from .prompts import (
    DEFAULT_PERSONA,
    FRAGMENT_CLASSIFY_PROMPT,
    PAIR_SUMMARY_PROMPT,
    PERSONAS,
    PLAN_PROMPT,
    SUMMARY_PROMPT,
    USER_PROFILE_PROMPT,
    WEEKLY_REPORT_PROMPT,
    WISH_MATCH_PROMPT,
)

logger = logging.getLogger("us.ai")

# 真实调用失败回退 mock 的标记（线程本地）：任务层据此把运行记为 degraded，不再静默
_state = threading.local()


def reset_mock_signal() -> None:
    """任务层在每次尝试前清零回退标记。"""
    _state.used_mock = False


def last_call_used_mock() -> bool:
    """自上次 reset_mock_signal() 以来，是否有真实 AI 调用失败回退 mock。

    只统计「配了 key 但调用失败」的回退路径；纯 mock 模式（没配 key）是配置使然，不算降级。
    """
    return getattr(_state, "used_mock", False)

_DEFAULT_CLASSIFY = {
    "type": "text",
    "tags": ["日常"],
    "is_knowledge": False,
    "is_wish": False,
    "wish_category": "",
    "ai_summary": "",
}


def mode() -> dict:
    return {"llm": "mock" if settings.llm_mock else "deepseek",
            "embedding": "mock" if settings.embed_mock else "doubao"}


def classify_fragment(content: str) -> dict:
    if settings.llm_mock:
        return mock.classify(content)
    try:
        result = deepseek.chat_json(FRAGMENT_CLASSIFY_PROMPT.format(content=content))
        merged = {**_DEFAULT_CLASSIFY, **{k: v for k, v in result.items() if k in _DEFAULT_CLASSIFY}}
        merged["wish_category"] = merged["wish_category"] or ""
        merged["tags"] = list(merged.get("tags") or [])[:3]
        return merged
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 分类失败，回退 mock：%s", exc)
        return mock.classify(content)


def embed_text(text: str) -> np.ndarray:
    if settings.embed_mock:
        return mock.embed(text)
    try:
        return doubao.embed(text)
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("豆包 embedding 失败，回退 mock：%s", exc)
        return mock.embed(text)


def embed_image(image_bytes: bytes, fmt: str = "jpeg") -> np.ndarray:
    """图片向量：与文本同一多模态端点（图文同空间同维度）；mock 用字节哈希桩。"""
    if settings.embed_mock:
        return mock.embed_image(image_bytes, fmt)
    try:
        return doubao.embed_image(image_bytes, fmt)
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("豆包图片 embedding 失败，回退 mock：%s", exc)
        return mock.embed_image(image_bytes, fmt)


def image_caption(image_bytes: bytes, fmt: str = "jpeg") -> str:
    """图片 caption：未配 key / 未配视觉模型（含 mock 模式）返回空跳过；
    调用失败同样优雅跳过（记 degraded，不影响任何现有功能）。"""
    if settings.embed_mock or not settings.DOUBAO_VISION_MODEL:
        return ""
    try:
        return doubao.vision_caption(image_bytes, fmt)
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("豆包 vision caption 失败，跳过：%s", exc)
        return ""


def summarize_text(text: str) -> str:
    if settings.llm_mock:
        return mock.summarize(text)
    try:
        return deepseek.chat(SUMMARY_PROMPT.format(text=text[:4000])).strip()
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 摘要失败，回退 mock：%s", exc)
        return mock.summarize(text)


def build_weekly_prompt(
    fragments_repr: str,
    week_start: str,
    week_end: str,
    persona: str = "",
    quotes: list[str] | None = None,
) -> str:
    """周报 prompt 组装（纯函数，便于直接断言人格与语录注入）。

    人格管语气（开头 persona 段），"事实与猜测的分寸"规则管事实（模板内原样保留）。
    """
    quotes_repr = "\n".join(f"- {q}" for q in quotes) if quotes else "（本周还没有可引用的发言）"
    return WEEKLY_REPORT_PROMPT.format(
        persona=persona or PERSONAS[DEFAULT_PERSONA],
        fragments=fragments_repr,
        quotes=quotes_repr,
        week_start=week_start,
        week_end=week_end,
    )


def generate_weekly_report(
    fragments_repr: str,
    week_start: str,
    week_end: str,
    stats: dict,
    persona: str = "",
    quotes: list[str] | None = None,
) -> str:
    if settings.llm_mock:
        return mock.weekly_report(fragments_repr, week_start, week_end, stats)
    try:
        prompt = build_weekly_prompt(fragments_repr, week_start, week_end, persona, quotes)
        return deepseek.chat(prompt, timeout=120.0)
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 周报失败，回退 mock：%s", exc)
        return mock.weekly_report(fragments_repr, week_start, week_end, stats)


def confirm_common_wishes(wishes_repr: str) -> list[dict]:
    """LLM 按 PRD 6.3 确认共同愿望；mock 模式返回空列表（调用方只用相似度）。"""
    if settings.llm_mock:
        return []
    try:
        result = deepseek.chat_json(WISH_MATCH_PROMPT.format(wishes=wishes_repr))
        return list(result.get("common_wishes", []))
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 愿望匹配失败，回退仅用相似度：%s", exc)
        return []


def wish_suggestion(content: str, users: list[str]) -> str:
    return mock.wish_suggestion(content, users)


def generate_plan(content: str, users: list[str]) -> dict:
    if settings.llm_mock:
        return mock.generate_plan(content, users)
    try:
        prompt = PLAN_PROMPT.format(wish=content, users="、".join(users))
        result = deepseek.chat_json(prompt)
        return {
            "time": result.get("time", ""),
            "location": result.get("location", ""),
            "budget": result.get("budget", ""),
            "steps": list(result.get("steps", [])),
        }
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 方案失败，回退 mock：%s", exc)
        return mock.generate_plan(content, users)


def generate_user_profile(nickname: str, stats: dict, excerpts: list[str] | None = None) -> dict:
    """画像蒸馏：LLM 生成结构化画像 JSON（含 style 说话风格），失败回退 mock 模板拼装。"""
    excerpts = excerpts or []
    if settings.llm_mock:
        return mock.user_profile(nickname, stats, excerpts)
    try:
        prompt = USER_PROFILE_PROMPT.format(
            nickname=nickname,
            stats=json.dumps(stats, ensure_ascii=False),
            excerpts="\n".join(f"- {e}" for e in excerpts) or "（暂无公开发言摘录）",
        )
        return dict(deepseek.chat_json(prompt))
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 画像失败，回退 mock：%s", exc)
        return mock.user_profile(nickname, stats, excerpts)


def generate_pair_summary(name_a: str, name_b: str, levels: dict, topics: list[str], wish_count: int) -> str:
    """关系摘要：基于分量等级与共同主题生成，正向叙事、不出现分数。"""
    if settings.llm_mock:
        return mock.pair_summary(name_a, name_b, topics, wish_count)
    try:
        prompt = PAIR_SUMMARY_PROMPT.format(
            name_a=name_a,
            name_b=name_b,
            topics="、".join(topics) or "（还没有共同主题）",
            wish_count=wish_count,
            levels=json.dumps(levels, ensure_ascii=False),
        )
        return deepseek.chat(prompt).strip()
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 关系摘要失败，回退 mock：%s", exc)
        return mock.pair_summary(name_a, name_b, topics, wish_count)
