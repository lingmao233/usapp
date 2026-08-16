"""AI 统一接口：按 key 是否存在自动选择真实 API 或 mock 桩。"""
import json
import logging
import threading

import numpy as np

from ..config import settings
from . import deepseek, doubao, mock
from .prompts import (
    DAILY_PLAN_PROMPT,
    DEFAULT_PERSONA,
    FOOD_PROMPT,
    FRAGMENT_CLASSIFY_PROMPT,
    PAIR_SUMMARY_PROMPT,
    PERSONAS,
    PLAN_CHAT_PROMPT,
    PLAN_EXTRACT_PROMPT,
    PLAN_KIND_PERSONAS,
    PLAN_KINDS,
    PLAN_PROMPT,
    RECEIPT_PROMPT,
    SAVINGS_ADVICE_PROMPT,
    SUMMARY_PROMPT,
    USER_PROFILE_PROMPT,
    VENTING_NEGATIVE_PERSONA,
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


def format_style_digest(nickname: str, profile: dict) -> str:
    """把画像的 style 维渲染成一行 prompt 摘录；无有效字段时返回空串（调用方过滤）。

    style 只从公开发言蒸馏（memory.refresh_dirty 的公开摘录口径），可进圈级 prompt。
    """
    style = (profile or {}).get("style") or {}
    catch = [str(c) for c in (style.get("catchphrases") or []) if c][:2]
    values = [f"口头禅「{'」「'.join(catch)}」"] if catch else []
    values += [str(style.get(k) or "").strip() for k in ("wording", "emoji", "sentence_length")]
    parts = [v for v in values if v and v != "暂无"]
    return f"{nickname}：{'，'.join(parts)}" if parts else ""


def build_weekly_prompt(
    fragments_repr: str,
    week_start: str,
    week_end: str,
    persona: str = "",
    quotes: list[str] | None = None,
    styles: list[str] | None = None,
) -> str:
    """周报 prompt 组装（纯函数，便于直接断言人格、语录与风格注入）。

    人格管语气（开头 persona 段），"事实与猜测的分寸"规则管事实（模板内原样保留）。
    """
    quotes_repr = "\n".join(f"- {q}" for q in quotes) if quotes else "（本周还没有可引用的发言）"
    styles_repr = "\n".join(f"- {s}" for s in styles) if styles else "（暂无成员风格画像）"
    return WEEKLY_REPORT_PROMPT.format(
        persona=persona or PERSONAS[DEFAULT_PERSONA],
        fragments=fragments_repr,
        quotes=quotes_repr,
        styles=styles_repr,
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
    styles: list[str] | None = None,
) -> str:
    if settings.llm_mock:
        return mock.weekly_report(fragments_repr, week_start, week_end, stats)
    try:
        prompt = build_weekly_prompt(fragments_repr, week_start, week_end, persona, quotes, styles)
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
        # 确认在后台重算里跑，放宽到 120s（默认 60s 曾 read timeout 回退相似度，BUG-008）
        result = deepseek.chat_json(WISH_MATCH_PROMPT.format(wishes=wishes_repr), timeout=120.0)
        return list(result.get("common_wishes", []))
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 愿望匹配失败，回退仅用相似度：%s", exc)
        return []


def wish_suggestion(content: str, users: list[str]) -> str:
    return mock.wish_suggestion(content, users)


def extract_plan_query(content: str) -> dict:
    """愿望分析：判定类型（kind 枚举）+ 提取地图查询参数（city/keywords）。

    实现路径由 PLAN_KINDS 按 kind 写死，LLM 只选类型。kind 非法回退 activity；
    need_real_data 与 keywords 联动——没有可搜的品类词就不查（防人名当 POI）。
    失败回退「原文当关键词 + activity」的旧行为。
    """
    if settings.llm_mock:
        return mock.extract_plan_query(content)
    try:
        result = deepseek.chat_json(PLAN_EXTRACT_PROMPT.format(wish=content))
        need = result.get("need_real_data", True)
        if isinstance(need, str):  # 防御 LLM 把布尔写成字符串
            need = need.strip().lower() in ("true", "1", "是")
        keywords = str(result.get("keywords", "")).strip()[:20]
        kind = str(result.get("kind", "")).strip().lower()
        if kind not in PLAN_KINDS:
            kind = "activity"
        mood = str(result.get("mood", "")).strip().lower()
        if mood not in ("negative", "neutral", "playful"):
            mood = "neutral"
        return {
            "kind": kind,
            "scene": str(result.get("scene", "")).strip()[:50],
            "city": str(result.get("city", "")).strip(),
            "keywords": keywords,
            "need_real_data": bool(need) and bool(keywords),
            "mood": mood,
        }
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 愿望分析失败，回退原文关键词：%s", exc)
        return mock.extract_plan_query(content)


def _format_real_data(real_data: dict | None) -> str:
    """把高德查询结果排版成 prompt 里的「真实数据」段；空段略去，整体为空给兜底文案。"""
    if not real_data:
        return "（本次没有可调用的真实数据，按规则 5 处理）"
    lines: list[str] = []
    spots = real_data.get("spots") or []
    if spots:
        lines.append("候选地点（高德真实 POI）：")
        lines += [
            f"- {s['name']}（{s['address'] or '地址见地图'}，评分 {s['rating'] or '暂无'}）"
            for s in spots
        ]
    hotels = real_data.get("hotels") or []
    if hotels:
        lines.append("候选酒店（高德真实 POI）：")
        lines += [
            f"- {h['name']}（{h['address'] or '地址见地图'}，参考价 {h['cost'] or '暂无'}）"
            for h in hotels
        ]
    weather = real_data.get("weather") or []
    if weather:
        lines.append("天气预报：")
        lines += [f"- {w['date']} {w['dayweather']}，{w['nighttemp']}~{w['daytemp']}℃" for w in weather]
    legs = real_data.get("legs") or []
    if legs:
        lines.append("相邻地点驾车通勤：")
        lines += [f"- {leg['distance_km']} 公里，约 {leg['duration_min']} 分钟" for leg in legs]
    if not lines:
        return "（本次没有可调用的真实数据，按规则 5 处理）"
    return "真实数据（高德地图查询结果，可直接采信）：\n" + "\n".join(lines)


def build_plan_prompt(
    content: str,
    users: list[str],
    real_data: dict | None = None,
    analysis: dict | None = None,
) -> str:
    """方案 prompt 组装（纯函数）：kind 决定实现路径与类型人格；非法 kind 回退 activity。

    venting 双形态：analysis.mood == 'negative' 换树洞人格（消极情绪不玩梗）。
    """
    analysis = analysis or {}
    kind = analysis.get("kind") or ""
    kind_label, kind_strategy = PLAN_KINDS.get(kind, PLAN_KINDS["activity"])
    if kind == "venting" and analysis.get("mood") == "negative":
        persona = VENTING_NEGATIVE_PERSONA
    else:
        persona = PLAN_KIND_PERSONAS.get(kind, PLAN_KIND_PERSONAS["activity"])
    return PLAN_PROMPT.format(
        wish=content,
        users="、".join(users),
        persona=persona,
        kind_label=kind_label,
        scene=analysis.get("scene") or "（未预判）",
        kind_strategy=kind_strategy,
        real_data=_format_real_data(real_data),
    )


def generate_plan(
    content: str,
    users: list[str],
    real_data: dict | None = None,
    analysis: dict | None = None,
) -> dict:
    if settings.llm_mock:
        return mock.generate_plan(content, users)
    try:
        prompt = build_plan_prompt(content, users, real_data, analysis)
        result = deepseek.chat_json(prompt)
        return {
            "time": result.get("time", ""),
            "location": result.get("location", ""),
            "budget": result.get("budget", ""),
            "steps": list(result.get("steps", [])),
            "links": [x for x in result.get("links", []) if isinstance(x, dict) and x.get("url")],
            "disclaimer": result.get("disclaimer", ""),
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


def build_plan_chat_prompt(
    wish: str,
    participants: list[str],
    plan: dict,
    quotes: list[str],
    history: list[dict],
    message: str,
    viewer_profile: dict | None = None,
    member_styles: list[str] | None = None,
) -> str:
    """方案追问 prompt 组装（纯函数）：画像注入 viewer-relative——自己全量、他人仅 style。"""
    history_text = "\n".join(
        f"{'用户' if h.get('role') == 'user' else '助手'}：{h.get('content', '')}"
        for h in history[-10:]
    ) or "（还没有对话）"
    return PLAN_CHAT_PROMPT.format(
        wish=wish,
        participants="、".join(participants),
        plan=json.dumps(plan, ensure_ascii=False),
        quotes="\n".join(f"- {q}" for q in quotes) or "（暂无语录）",
        viewer_profile=json.dumps(viewer_profile, ensure_ascii=False) if viewer_profile else "（暂无画像）",
        member_styles="\n".join(f"- {s}" for s in member_styles) if member_styles else "（暂无）",
        history=history_text,
        message=message,
    )


def plan_chat(
    wish: str,
    plan: dict,
    participants: list[str],
    quotes: list[str],
    history: list[dict],
    message: str,
    viewer_profile: dict | None = None,
    member_styles: list[str] | None = None,
) -> str:
    """方案追问：上下文 = 愿望 + 已定方案 + 圈内公开语录 + 画像（viewer-relative）+ 近 10 条对话。"""
    if settings.llm_mock:
        return mock.plan_chat(wish, message)
    try:
        prompt = build_plan_chat_prompt(
            wish, participants, plan, quotes, history, message, viewer_profile, member_styles
        )
        return deepseek.chat(prompt).strip()
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 方案追问失败，回退 mock：%s", exc)
        return mock.plan_chat(wish, message)


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


# ---------- 个人功能：账单识别 / 热量识别 / 当日计划 / 存款建议 ----------


def recognize_receipt(image_path: str) -> list[dict] | None:
    """小票/支付截图识别（一图多笔）：未配视觉模型返回 None 优雅跳过（配置使然，不算降级）；
    调用失败回退 mock 桩并记 degraded。"""
    if settings.embed_mock or not settings.DOUBAO_VISION_MODEL:
        return None
    try:
        result = doubao.vision_json(image_path, RECEIPT_PROMPT)
        if not isinstance(result, list):
            raise ValueError(f"账单识别应返回数组，实际为 {type(result).__name__}")
        return result
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("豆包账单识别失败，回退 mock：%s", exc)
        return mock.receipt_recognition()


def recognize_food(image_path: str, hint: str = "") -> dict | None:
    """食物照片识别 + 热量估算：开关与降级口径同 recognize_receipt。

    hint 为用户补充描述（如"红烧肉一碗约 300g"），经 FOOD_PROMPT 的 {hint} 占位注入，可空。
    """
    if settings.embed_mock or not settings.DOUBAO_VISION_MODEL:
        return None
    try:
        result = doubao.vision_json(image_path, FOOD_PROMPT.format(hint=hint))
        if not isinstance(result, dict):
            raise ValueError(f"食物识别应返回对象，实际为 {type(result).__name__}")
        return result
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("豆包食物识别失败，回退 mock：%s", exc)
        return mock.food_recognition()


def generate_daily_plan(goal_type: str, framework: dict, context: dict) -> list[dict]:
    """当日计划：周期框架（规则算好的数字）+ 昨日完成 + 剩余进度 → 当日条目数组。

    context 键：yesterday（昨日完成情况，一句话）、progress（剩余目标进度，一句话），均可空。
    """
    yesterday = str((context or {}).get("yesterday") or "")
    progress = str((context or {}).get("progress") or "")
    if settings.llm_mock:
        return mock.daily_plan(goal_type, framework, yesterday, progress)
    try:
        prompt = DAILY_PLAN_PROMPT.format(
            goal_type=goal_type,
            framework=json.dumps(framework, ensure_ascii=False),
            yesterday=yesterday or "（无记录）",
            progress=progress or "（暂无）",
        )
        result = deepseek.chat_json(prompt)
        # prompt 要求裸数组，但 json_object 模式下模型可能包一层对象，兼容取第一个数组值
        items = result if isinstance(result, list) else next(
            (v for v in result.values() if isinstance(v, list)), None
        )
        if items is None:
            raise ValueError("当日计划返回中找不到条目数组")
        return [item for item in items if isinstance(item, dict)]
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 当日计划失败，回退 mock：%s", exc)
        return mock.daily_plan(goal_type, framework, yesterday, progress)


def generate_savings_advice(settlement: dict) -> str:
    """存款月度结算建议：数字全由规则算好，LLM 只说人话；失败回退 mock 模板文案。"""
    if settings.llm_mock:
        return mock.savings_advice(settlement)
    try:
        prompt = SAVINGS_ADVICE_PROMPT.format(settlement=json.dumps(settlement, ensure_ascii=False))
        return deepseek.chat(prompt).strip()
    except Exception as exc:  # noqa: BLE001
        _state.used_mock = True
        logger.warning("DeepSeek 存款建议失败，回退 mock：%s", exc)
        return mock.savings_advice(settlement)
