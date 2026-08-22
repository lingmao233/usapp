"""AI 统一接口：直连真实 provider，未配置即报清晰错误，不再回退桩数据。

- LLM/EMBEDDING 未配置（缺 key）：抛 AINotConfiguredError——同步路由由 main.py 转 503，
  后台任务经 tasks.run_task 记 failed（error 写明未配置）
- 视觉未配置（VISION_MODEL 空）：优雅跳过（caption 返回 ""，识别返回 None → 路由 400）
- 真实调用失败：按场景抛错（任务层记 failed）或走「无桩数据」的降级路径并记 degraded
  （视觉跳过、共同愿望仅相似度、记忆写回跳过——均不产假数据）
- 测试不经过本模块的真实分支：conftest.py 把门面函数整体换成 tests/fakes.py 确定性桩
"""
import base64
import json
import logging
import re
import threading

import numpy as np

from ..config import settings
from . import embedding, llm, vision
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
    WEB_SEARCH_FOOD_PROMPT,
    WEEKLY_REPORT_PROMPT,
    WISH_MATCH_PROMPT,
)

logger = logging.getLogger("us.ai")


class AINotConfiguredError(RuntimeError):
    """LLM/EMBEDDING 未配置（缺 API key）：同步路由 503，后台任务记 failed。"""


def _require_llm() -> None:
    if not settings.LLM_API_KEY:
        raise AINotConfiguredError("未配置 LLM（LLM_API_KEY 为空），AI 文本能力不可用")


def _require_embedding() -> None:
    if not settings.EMBEDDING_API_KEY:
        raise AINotConfiguredError(
            "未配置 EMBEDDING（EMBEDDING_API_KEY 与回退的 LLM_API_KEY 均为空），向量能力不可用"
        )


def _require_treehole() -> None:
    """树洞专属配置（treehole_llm()：TREEHOLE_* 非空走 Kimi，空回退 LLM_*）。"""
    if not settings.treehole_llm()[0]:
        raise AINotConfiguredError(
            "未配置树洞模型（TREEHOLE_API_KEY 与回退的 LLM_API_KEY 均为空），树洞不可用"
        )


# 降级标记（线程本地）：走了「无桩数据」的降级路径时置位，任务层据此把运行记为 degraded
_state = threading.local()


def reset_degraded_signal() -> None:
    """任务层在每次尝试前清零降级标记。"""
    _state.degraded = False


def last_call_degraded() -> bool:
    """自上次 reset_degraded_signal() 以来，是否有调用走了降级路径（视觉跳过/仅相似度等）。"""
    return getattr(_state, "degraded", False)


_DEFAULT_CLASSIFY = {
    "type": "text",
    "tags": ["日常"],
    "is_knowledge": False,
    "is_wish": False,
    "wish_category": "",
    "ai_summary": "",
}


def mode() -> dict:
    """配置巡检：各组 configured/missing（视觉报 on/off）。只报配置与否，不代表已连通。"""
    return {"llm": "configured" if settings.LLM_API_KEY else "missing",
            "embedding": "configured" if settings.EMBEDDING_API_KEY else "missing",
            "vision": "on" if settings.vision_enabled else "off"}


def classify_fragment(content: str) -> dict:
    _require_llm()
    result = llm.chat_json(FRAGMENT_CLASSIFY_PROMPT.format(content=content))
    merged = {**_DEFAULT_CLASSIFY, **{k: v for k, v in result.items() if k in _DEFAULT_CLASSIFY}}
    merged["wish_category"] = merged["wish_category"] or ""
    merged["tags"] = list(merged.get("tags") or [])[:3]
    return merged


def embed_text(text: str) -> np.ndarray:
    _require_embedding()
    return embedding.embed(text)


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    """批量文本向量（启动灌库等批量场景）：一次请求多条，逐条调会把启动卡到分钟级。"""
    _require_embedding()
    return embedding.embed_batch(texts)


def embed_food_image(image_bytes: bytes, fmt: str = "jpeg") -> np.ndarray:
    """食物图片向量（图片型 RAG）：多模态 embedding，与文本向量同空间。
    未配置/调用失败抛错，调用方自行降级（识别走常管线、确认跳过入库）。"""
    _require_embedding()
    return embedding.embed_image(image_bytes, fmt)


def image_caption(image_bytes: bytes, fmt: str = "jpeg") -> str:
    """图片 caption：视觉关闭（未配 key / 未配 VISION_MODEL）返回空跳过；
    调用失败同样优雅跳过（记 degraded，不影响任何现有功能）。"""
    if not settings.vision_enabled:
        return ""
    try:
        return vision.vision_caption(image_bytes, fmt, reasoning=settings.vision_reasoning("caption"))
    except Exception as exc:  # noqa: BLE001
        _state.degraded = True
        logger.warning("vision caption 失败，跳过：%s", exc)
        return ""


def summarize_text(text: str) -> str:
    _require_llm()
    return llm.chat(SUMMARY_PROMPT.format(text=text[:4000])).strip()


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
    _require_llm()
    prompt = build_weekly_prompt(fragments_repr, week_start, week_end, persona, quotes, styles)
    return llm.chat(prompt, timeout=120.0)


def confirm_common_wishes(wishes_repr: str) -> list[dict]:
    """LLM 按 PRD 6.3 确认共同愿望；未配置或调用失败返回空列表（调用方只用相似度，记 degraded）。"""
    if not settings.LLM_API_KEY:
        _state.degraded = True
        return []
    try:
        # 确认在后台重算里跑，放宽到 120s（默认 60s 曾 read timeout 回退相似度，BUG-008）
        result = llm.chat_json(WISH_MATCH_PROMPT.format(wishes=wishes_repr), timeout=120.0)
        return list(result.get("common_wishes", []))
    except Exception as exc:  # noqa: BLE001
        _state.degraded = True
        logger.warning("LLM 愿望匹配失败，回退仅用相似度：%s", exc)
        return []


def wish_suggestion(content: str, users: list[str]) -> str:
    """共同愿望建议：纯模板拼装，不过 LLM。"""
    names = "和".join(users)
    return f"{names}可以这周末先约个时间碰头，把「{content}」具体聊一聊，定个小目标就开始。"


def extract_plan_query(content: str) -> dict:
    """愿望分析：判定类型（kind 枚举）+ 提取地图查询参数（city/keywords）。

    实现路径由 PLAN_KINDS 按 kind 写死，LLM 只选类型。kind 非法回退 activity；
    need_real_data 与 keywords 联动——没有可搜的品类词就不查（防人名当 POI）。
    """
    _require_llm()
    result = llm.chat_json(PLAN_EXTRACT_PROMPT.format(wish=content))
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
    _require_llm()
    prompt = build_plan_prompt(content, users, real_data, analysis)
    result = llm.chat_json(prompt)
    return {
        "time": result.get("time", ""),
        "location": result.get("location", ""),
        "budget": result.get("budget", ""),
        "steps": list(result.get("steps", [])),
        "links": [x for x in result.get("links", []) if isinstance(x, dict) and x.get("url")],
        "disclaimer": result.get("disclaimer", ""),
    }


def generate_user_profile(nickname: str, stats: dict, excerpts: list[str] | None = None) -> dict:
    """画像蒸馏：LLM 生成结构化画像 JSON（含 style 说话风格）；未配置/失败直接抛错。"""
    excerpts = excerpts or []
    _require_llm()
    prompt = USER_PROFILE_PROMPT.format(
        nickname=nickname,
        stats=json.dumps(stats, ensure_ascii=False),
        excerpts="\n".join(f"- {e}" for e in excerpts) or "（暂无公开发言摘录）",
    )
    return dict(llm.chat_json(prompt))


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
    _require_llm()
    prompt = build_plan_chat_prompt(
        wish, participants, plan, quotes, history, message, viewer_profile, member_styles
    )
    return llm.chat(prompt).strip()


def generate_pair_summary(name_a: str, name_b: str, levels: dict, topics: list[str], wish_count: int) -> str:
    """关系摘要：基于分量等级与共同主题生成，正向叙事、不出现分数。"""
    _require_llm()
    prompt = PAIR_SUMMARY_PROMPT.format(
        name_a=name_a,
        name_b=name_b,
        topics="、".join(topics) or "（还没有共同主题）",
        wish_count=wish_count,
        levels=json.dumps(levels, ensure_ascii=False),
    )
    return llm.chat(prompt).strip()


# ---------- 个人功能：账单识别 / 热量识别 / 当日计划 / 存款建议 ----------


def recognize_receipt(image_path: str) -> list[dict] | None:
    """小票/支付截图识别（一图多笔）：视觉关闭返回 None 优雅跳过（配置使然，不算降级）；
    调用失败同样返回 None 并记 degraded（调用方按「识别不可用」提示手动录入）。"""
    if not settings.vision_enabled:
        return None
    try:
        result = vision.vision_json(image_path, RECEIPT_PROMPT, reasoning=settings.vision_reasoning("receipt"))
        if not isinstance(result, list):
            raise ValueError(f"账单识别应返回数组，实际为 {type(result).__name__}")
        return result
    except Exception as exc:  # noqa: BLE001
        _state.degraded = True
        logger.warning("账单识别失败，按未配置口径跳过：%s", exc)
        return None


def recognize_food(image_path: str, hint: str = "", calibration: list[dict] | None = None,
                   bias: float | None = None) -> dict | None:
    """食物照片识别 + 热量估算：开关与降级口径同 recognize_receipt。

    hint 为用户补充描述（如"红烧肉一碗约 300g"），经 FOOD_PROMPT 的 {hint} 占位注入，可空。
    calibration 为该用户的历史克数纠正（[{name, ai_grams, user_grams}]），注入 prompt
    让分量估计贴合用户习惯（在线校准，非模型微调）。
    bias 为全局偏置系数（用户实际/模型估计的中位比值，服务层按历史纠正算出）：
    模型对这个用户历来的系统性高/低估，校准所有克数——比逐条样例更稳的用户级信号。
    """
    if not settings.vision_enabled:
        return None
    calib_lines = [
        f"- {c['name']}：你上次估 {c['ai_grams']:g}g，用户实际是 {c['user_grams']:g}g"
        for c in calibration or []
    ]
    if bias and abs(bias - 1) >= 0.05:  # 偏离 <5% 视为噪声不注入
        direction = "偏低" if bias > 1 else "偏高"
        calib_lines.append(
            f"- 整体校准：你历来的分量估计比该用户实际平均{direction}约 {abs(bias - 1):.0%}，"
            f"本次所有克数按 ×{bias:.2f} 修正")
    calib_text = "\n".join(calib_lines) or "（无）"
    prompt = FOOD_PROMPT.format(hint=hint, calibration=calib_text)
    try:
        result = vision.vision_json(
            image_path, prompt, reasoning=settings.vision_reasoning("food")
        )
        if not isinstance(result, dict):
            raise ValueError(f"食物识别应返回对象，实际为 {type(result).__name__}")
        # 两级策略：快档（food 默认 off）先出；模型自报包装食品但没读出品牌时
        # 带思考重试一次（读包装小字是精细活，实测 off 会漏品牌，见 BUG-018 排查记录）
        items = result.get("items") or []
        need_retry = any(
            isinstance(i, dict)
            and i.get("packaged")
            and not str(i.get("brand") or "").strip()
            for i in items
        )
        if need_retry and settings.vision_reasoning("food") != "on":
            retried = vision.vision_json(image_path, prompt, reasoning="on")
            if isinstance(retried, dict):
                result = retried
        return result
    except Exception as exc:  # noqa: BLE001
        _state.degraded = True
        logger.warning("食物识别失败：%s", exc)
        # 抛错而非返回 None：None 的语义是「未配置」，调用失败要说真话（曾误报"未配置视觉模型"）
        raise RuntimeError(f"视觉模型调用失败：{exc}") from exc


def _opt_macro(raw) -> float | None:
    """宏量营养素可空值规整：非法/越界（>100g）一律当缺失。"""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if 0 <= v <= 100 else None


def web_search_food(name: str, brand: str = "", model_per_100g=None) -> dict | None:
    """联网查食物每 100 单位营养（营养共建信任管线的核验/兜底数据源）。

    可靠性在源头掐（BUG-022 复盘：搜「红茶」回干茶叶 294 kcal/100g，形态/单位错配）：
    - form_hint：饮品名（结尾词判断，与 nutrition.drink 先验同源）明确要求「即饮/冲泡后」
      口径，不给干料/原料值
    - model_per_100g：视觉模型对该食物的隐含单价，作为交叉自检锚点写进 prompt——
      让模型在回答前自己发现「差 10 倍 = 形态/单位搞错了」（入库前的 sanitize 仍兜底）

    联网通道二选一：树洞配置是 Kimi 系（moonshot/kimi.com）→ 内置 $web_search 真联网
    （回声协议见 llm.chat_messages）；否则回退 LLM_* + enable_search（阿里百炼写法，
    厂商不认会忽略→模型硬答，不算真联网）。降级链：LLM_WEB_SEARCH≠on（默认 off）→ None；
    未配置 / 调用失败 / 结果非法 → None。
    """
    name = (name or "").strip()
    brand = (brand or "").strip()
    if not name or not settings.web_search_enabled:
        return None
    # 延迟导入：nutrition 依赖 ai 门面，模块级会成环；form 先验与匹配层同源（一个词表）
    from ..services.nutrition import is_drink_name

    brand_hint = f"品牌：{brand}。" if brand else ""
    form_hint = ("口径提示：这是用户喝的饮品，请查「冲泡后/即饮」液体的值（每 100ml≈100g），"
                 "不要给干茶叶/粉剂/浓缩原料的值。" if is_drink_name(name) else "")
    try:
        ref = float(model_per_100g) if model_per_100g is not None else None
    except (TypeError, ValueError):
        ref = None
    if ref and ref > 0:
        ref_hint = f"\n背景参考：视觉模型对这项食物的估算约为 {ref:g} kcal/100（仅供核对，不作为数据来源）。"
        ref_check = (f"视觉模型对它的估算约为 {ref:g} kcal/100；你查到的值若与它差 10 倍以上，"
                     "先怀疑自己搞错了形态或单位，重新检索确认后再回答")
    else:
        ref_hint = ""
        ref_check = ("你查到的值若明显超出同类食物的常识量级（如饮品查出三位数 kcal/100），"
                     "先怀疑形态或单位搞错，重新检索确认后再回答")
    prompt = WEB_SEARCH_FOOD_PROMPT.format(name=name, brand_hint=brand_hint,
                                           form_hint=form_hint, ref_hint=ref_hint,
                                           ref_check=ref_check)
    try:
        cfg = settings.treehole_llm()
        kimi_hosted = "moonshot" in cfg[1] or "kimi.com" in cfg[1]
        if cfg[0] and kimi_hosted:
            text = llm.chat_messages(
                [{"role": "user", "content": prompt}],
                cfg=cfg,
                tools=[{"type": "builtin_function", "function": {"name": "$web_search"}}],
                timeout=90.0,
            )
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise ValueError(f"联网返回非 JSON：{text[:80]}")
            result = json.loads(match.group(0))
        else:
            if not settings.LLM_API_KEY:
                return None
            # enable_search 是厂商相关参数（阿里百炼写法），厂商不支持会忽略/报错 → 走降级
            result = llm.chat_json(prompt, enable_search=True)
        kcal = float(result.get("kcal_per_100g"))
        if not 0 < kcal <= 1000:
            raise ValueError(f"联网返回的 kcal_per_100g 越界：{kcal}")
        return {
            "kcal_per_100g": kcal,
            "protein_per_100g": _opt_macro(result.get("protein_per_100g")),
            "fat_per_100g": _opt_macro(result.get("fat_per_100g")),
            "cho_per_100g": _opt_macro(result.get("cho_per_100g")),
            "basis": str(result.get("basis") or "").strip()[:60],  # 口径说明（观测/审计用）
        }
    except Exception as exc:  # noqa: BLE001
        # 联网核验失败不算 degraded：off/失败都有明确降级路径（模型估值/待核实），不污染任务层标记
        logger.warning("联网查询食物营养失败，降级：%s", exc)
        return None


def generate_daily_plan(goal_type: str, framework: dict, context: dict) -> list[dict]:
    """当日计划：周期框架（规则算好的数字）+ 昨日完成 + 剩余进度 → 当日条目数组。

    context 键：yesterday（昨日完成情况，一句话）、progress（剩余目标进度，一句话），均可空。
    """
    yesterday = str((context or {}).get("yesterday") or "")
    progress = str((context or {}).get("progress") or "")
    _require_llm()
    prompt = DAILY_PLAN_PROMPT.format(
        goal_type=goal_type,
        framework=json.dumps(framework, ensure_ascii=False),
        yesterday=yesterday or "（无记录）",
        progress=progress or "（暂无）",
    )
    result = llm.chat_json(prompt)
    # prompt 要求裸数组，但 json_object 模式下模型可能包一层对象，兼容取第一个数组值
    items = result if isinstance(result, list) else next(
        (v for v in result.values() if isinstance(v, list)), None
    )
    if items is None:
        raise ValueError("当日计划返回中找不到条目数组")
    return [item for item in items if isinstance(item, dict)]


def generate_savings_advice(settlement: dict) -> str:
    """存款月度结算建议：数字全由规则算好，LLM 只说人话；未配置/失败直接抛错。"""
    _require_llm()
    prompt = SAVINGS_ADVICE_PROMPT.format(settlement=json.dumps(settlement, ensure_ascii=False))
    return llm.chat(prompt).strip()


# ---------- 情绪树洞（Agent 化改造）：真实模式走 langgraph/langmem，测试由 fakes 接管 ----------

from .prompts import (  # noqa: E402
    TREEHOLE_COMPRESS_PROMPT,
    TREEHOLE_IMAGE_PROMPT,
    TREEHOLE_REPLY_PROMPT,
    TREEHOLE_REWRITE_PROMPT,
    TREEHOLE_ROUTE_PROMPT,
    TREEHOLE_TOOLS_PROMPT,
)

TREEHOLE_INTENTS = ("vent", "question", "data")


def treehole_route(message: str) -> str:
    """① 意图路由：vent/question/data；非法输出回退 vent（倾诉是最安全的口径）。"""
    _require_treehole()
    intent = llm.chat(TREEHOLE_ROUTE_PROMPT.format(message=message),
                      timeout=30.0, cfg=settings.treehole_llm()).strip().lower()
    return intent if intent in TREEHOLE_INTENTS else "vent"


def treehole_rewrite(message: str) -> str:
    """② 查询改写：情绪化输入 → 检索友好 query。"""
    _require_treehole()
    return llm.chat(TREEHOLE_REWRITE_PROMPT.format(message=message),
                    timeout=30.0, cfg=settings.treehole_llm()).strip()[:50]


def treehole_tool_plan(message: str, intent: str, tools_desc: str, results: list[dict]) -> dict:
    """③ 工具决策（tool calling 循环的一轮）：返回 {"calls": [{"name", "args"}]}。"""
    _require_treehole()
    result = llm.chat_json(TREEHOLE_TOOLS_PROMPT.format(
        tools=tools_desc, message=message, intent=intent,
        results=json.dumps(results, ensure_ascii=False) or "（无）",
    ), timeout=30.0, cfg=settings.treehole_llm())
    calls = [
        {"name": str(c.get("name", "")), "args": dict(c.get("args") or {})}
        for c in result.get("calls") or []
        if isinstance(c, dict) and c.get("name")
    ]
    return {"calls": calls}


def treehole_reply(payload: dict) -> str:
    """④ 人设扮演生成（消息制）：system 装人设/画像/记忆/检索/工具结果，history 与当前消息
    走 messages；当前消息可带图片 part（payload["image"] = data URL）；
    树洞联网开启时挂 Kimi $web_search（回声协议在 llm.chat_messages）。"""
    _require_treehole()
    intent_labels = {"vent": "倾诉（先共情接住）", "question": "提问（给具体建议）",
                     "data": "查数据（如实使用工具结果）"}
    persona = payload.get("persona") or {}
    custom = (persona.get("custom_prompt") or "").strip()
    if custom:
        # 整段人设优先：模板字段不再注入，仅保留名字供称呼（见 docs/交接文档-Agent化改造.md）
        persona_text = f"名字：{persona.get('name') or '树洞'}\n{custom}"
    else:
        persona_text = "\n".join(
            f"- {label}：{persona.get(key)}" for key, label in (
                ("name", "名字"), ("personality", "性格"), ("speaking_style", "说话风格"),
                ("relationship", "与用户的关系"), ("background", "背景设定"),
            ) if persona.get(key)
        ) or "名字：树洞；性格：温和耐心的倾听者；与用户的关系：最信得过的朋友"
    system = TREEHOLE_REPLY_PROMPT.format(
        persona=persona_text,
        profile=json.dumps(payload.get("profile") or {}, ensure_ascii=False) or "（暂无画像）",
        atoms="\n".join(f"- {a['content']}" for a in payload.get("atoms") or []) or "（暂无）",
        hits="\n".join(
            f"- [{h.get('created_at', '')[:10]}] {h.get('excerpt', '')}（来源 id：{h.get('id', '')}）"
            for h in payload.get("hits") or []
        ) or "（本次未命中）",
        summary_block=(
            "【较早历史的滚动摘要】\n" + json.dumps(payload["summary"], ensure_ascii=False)
            if payload.get("summary") else ""
        ),
        tool_results=json.dumps(payload.get("tool_results") or [], ensure_ascii=False),
        intent_label=intent_labels.get(payload.get("intent") or "vent", "倾诉"),
    )
    messages: list[dict] = [{"role": "system", "content": system}]
    for h in payload.get("history") or []:
        role = "user" if h.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": h.get("content", "")})
    if payload.get("image"):
        current_content: object = [
            {"type": "text", "text": payload.get("message") or ""},
            {"type": "image_url", "image_url": {"url": payload["image"]}},
        ]
    else:
        current_content = payload.get("message") or ""
    messages.append({"role": "user", "content": current_content})
    tools = ([{"type": "builtin_function", "function": {"name": "$web_search"}}]
             if settings.treehole_web_search_enabled else None)
    return llm.chat_messages(messages, cfg=settings.treehole_llm(), tools=tools,
                             timeout=120.0).strip()


def treehole_image_caption(image_bytes: bytes, fmt: str = "jpeg", user_text: str = "") -> str:
    """④b 图片描述：人像详述外貌特征供日后「认出这个人」；写入 L0/L1 记忆管线。

    通道选择：优先 VISION 组（qwen-vl 等对公众人物识别更开放，能答"这是谁"）；
    未配视觉时回退树洞模型（Kimi 多模态，人脸指认偏保守）。失败记 degraded 返回空。
    """
    prompt = TREEHOLE_IMAGE_PROMPT.format(user_text=user_text or "（无）")
    try:
        if settings.vision_enabled:
            return vision.vision_ask(
                image_bytes, prompt, fmt, reasoning=settings.vision_reasoning("caption")
            ).strip()[:200]
        _require_treehole()
        b64 = base64.b64encode(image_bytes).decode()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": f"data:image/{fmt};base64,{b64}"}},
        ]}]
        return llm.chat_messages(messages, cfg=settings.treehole_llm(),
                                 timeout=60.0).strip()[:200]
    except Exception as exc:  # noqa: BLE001
        _state.degraded = True
        logger.warning("树洞图片 caption 失败，当轮降级为纯文本：%s", exc)
        return ""


def treehole_compress(old_summary: dict, messages: list[dict]) -> dict:
    """⑥ 滚动增量摘要（填槽式：facts/emotion_trail/followups/time_anchors，带源消息 id）。

    压缩失败保留旧摘要并记 degraded（回复已生成，不为写回牺牲当轮），下轮攒够再压。
    """
    _require_treehole()
    try:
        result = llm.chat_json(TREEHOLE_COMPRESS_PROMPT.format(
            old_summary=json.dumps(old_summary or {}, ensure_ascii=False),
            messages=json.dumps(messages, ensure_ascii=False),
        ), timeout=60.0, cfg=settings.treehole_llm())
        return {
            "facts": [f for f in result.get("facts") or [] if isinstance(f, dict)][:20],
            "emotion_trail": str(result.get("emotion_trail") or "")[:200],
            "followups": [f for f in result.get("followups") or [] if isinstance(f, dict)][:10],
            "time_anchors": [f for f in result.get("time_anchors") or [] if isinstance(f, dict)][:10],
        }
    except Exception as exc:  # noqa: BLE001
        _state.degraded = True
        logger.warning("LLM 树洞滚动摘要失败，保留旧摘要：%s", exc)
        return dict(old_summary or {})


def extract_memory_atoms(user_message: str, assistant_reply: str = "") -> list[dict]:
    """⑥ L1 原子记忆抽取：真实模式走 langmem（模型适配见 ai/langmem_ext）。

    抽取失败记 degraded 并返回空（记忆写回跳过，不拖垮当轮回复），不产桩数据。
    """
    _require_llm()
    try:
        from . import langmem_ext

        return langmem_ext.extract_atoms(user_message, assistant_reply)
    except Exception as exc:  # noqa: BLE001
        _state.degraded = True
        logger.warning("langmem 记忆抽取失败，本轮跳过写回：%s", exc)
        return []
