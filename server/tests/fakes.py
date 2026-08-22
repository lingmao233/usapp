"""测试专用确定性桩（原 app/ai/mock.py 迁入，生产代码不再引用）。

- 分类桩：关键词规则
- Embedding 桩：字符 n-gram 哈希 512 维向量（相似文本余弦确实更高）
- 周报/方案桩：模板化生成

接线方式：conftest.py 在 import 期调用 install() 把 app.ai 门面函数整体换成本模块桩
（services 一律经 ai.xxx 属性调用，换装即全局生效）；冒烟/评估脚本同款手法。
需要断言真实解析路径的用例可从 REAL_IMPLS 取回原实现显式装回（见 test_amap.py）。
"""
import hashlib
import re
from collections import Counter

import numpy as np

from app.config import settings  # 绑定与 ai 门面同一 settings 单例（reload 场景保持一致）

EMBED_DIM = 512

_WISH_PATTERNS: list[tuple[str, str]] = [
    ("想吃", "eat"),
    ("想去", "go"),
    ("想学", "learn"),
    ("想买", "buy"),
    ("想做", "do"),
    ("想看", "do"),
    ("想玩", "do"),
    ("想养", "do"),
    ("想试", "do"),
]

_URL_RE = re.compile(r"https?://[^\s]+")
_STOP_CHARS = set("的了是在我你他她它们这那有和就都也不与及或着过啊呢吧吗很还又于为到想去要来会把好一个些什怎么没可可以上下中天今明")
_TAG_STOPWORDS = {"我们", "你们", "大家", "觉得", "真的", "什么", "这个", "那个", "就是", "还是", "不是", "没有", "可以", "自己", "现在", "今天", "明天", "昨天", "一下", "时候"}


def _normalize(text: str) -> str:
    return re.sub(r"[^\w一-鿿]+", "", text)


def embed(text: str) -> np.ndarray:
    """字符 n-gram 哈希向量：unigram 权重 1，bigram 权重 2，归一化。"""
    norm = _normalize(text)
    vec = np.zeros(EMBED_DIM, dtype=np.float32)
    if not norm:
        return vec
    grams: list[tuple[str, float]] = [(ch, 1.0) for ch in norm]
    grams += [(norm[i : i + 2], 2.0) for i in range(len(norm) - 1)]
    for gram, weight in grams:
        idx = int.from_bytes(hashlib.md5(gram.encode("utf-8")).digest()[:4], "little") % EMBED_DIM
        vec[idx] += weight
    norm_len = np.linalg.norm(vec)
    if norm_len > 0:
        vec /= norm_len
    return vec


def _extract_tags(text: str) -> list[str]:
    """从高频 bigram 提取标签（去停用词，最多 3 个）。"""
    clean = _normalize(text)
    counter: Counter[str] = Counter()
    for i in range(len(clean) - 1):
        gram = clean[i : i + 2]
        if gram in _TAG_STOPWORDS:
            continue
        if any(ch in _STOP_CHARS for ch in gram):
            continue
        counter[gram] += 1
    tags = [gram for gram, _ in counter.most_common(8)]
    # 去重：剔除相互包含的
    deduped: list[str] = []
    for tag in tags:
        if any(tag in t or t in tag for t in deduped):
            continue
        deduped.append(tag)
        if len(deduped) == 3:
            break
    return deduped or ["日常"]


def classify(content: str) -> dict:
    """关键词规则分类桩。"""
    is_wish = False
    wish_category = ""
    for pattern, category in _WISH_PATTERNS:
        if pattern in content:
            is_wish = True
            wish_category = category
            break

    has_url = bool(_URL_RE.search(content))
    is_knowledge = has_url or len(content) >= 100

    frag_type = "link" if has_url else "text"
    summary_src = _URL_RE.sub("", content).strip() or content
    ai_summary = summary_src[:20] + ("…" if len(summary_src) > 20 else "")

    return {
        "type": frag_type,
        "tags": _extract_tags(content),
        "is_knowledge": is_knowledge,
        "is_wish": is_wish,
        "wish_category": wish_category,
        "ai_summary": ai_summary,
    }


def summarize(text: str) -> str:
    """模板化三句话摘要桩。"""
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return "这条收藏暂时没有正文，先存个链接，回头再补摘要。"
    sentences = re.split(r"(?<=[。！？.!?])\s*", clean)
    sentences = [s for s in sentences if s][:3]
    picked = " ".join(sentences) if sentences else clean[:80]
    return picked[:180] + ("…" if len(picked) > 180 else "")


def weekly_report(fragments_repr: str, week_start: str, week_end: str,
                  stats: dict) -> str:
    """模板化周报桩。stats: {users, top_tags, wishes, knowledge_count}"""
    users = "、".join(stats.get("users", [])) or "大家"
    top_tags = "、".join(f"#{t}" for t in stats.get("top_tags", [])) or "#日常"
    wishes = stats.get("wishes", [])
    wish_lines = "\n".join(f"- {w}" for w in wishes) if wishes else "- 本周还没有新愿望，去丢一条吧"
    knowledge_count = stats.get("knowledge_count", 0)
    connections = stats.get("connections", [])
    connection_lines = (
        "\n".join(f"- {c}" for c in connections)
        if connections
        else "- 本周大家各忙各的，连接还在路上，多丢几条碎片试试"
    )
    return f"""# 本周交集报告（{week_start} - {week_end}）

## 🎯 本周主题
{users} 的碎片里，出现最多的是 {top_tags}。

## 🔗 关键连接
{connection_lines}

## 📚 知识沉淀
- 本周共归档 {knowledge_count} 条收藏，已自动进入知识库
- 热门标签：{top_tags}

## 🎯 愿望动态
{wish_lines}

## 💡 AI 洞察
碎片不多也没关系，随手一句话、一个链接都算数。丢得越多，AI 越能发现你们想到一块去的瞬间。
"""


def extract_plan_query(content: str) -> dict:
    """确定性愿望分析桩：不懂地理与人名，恒为 activity + 需要真实数据（维持旧行为）。"""
    return {
        "kind": "activity",
        "scene": "",
        "city": "",
        "keywords": content[:10] or "周边游",
        "need_real_data": True,
        "mood": "neutral",
    }


def generate_plan(content: str, users: list[str]) -> dict:
    names = "、".join(users)
    return {
        "time": "本周六下午 14:00",
        "location": "大家中间点的咖啡馆碰头，再一起出发",
        "budget": "人均 100 元以内（预估）",
        "steps": [
            f"{names}在群里确认时间，先到先得",
            f"围绕「{content}」各自搜一个备选方案，周四前丢进圈子",
            "周六碰头投票，当场定下来就出发",
        ],
        "links": [],
        "disclaimer": "确定性桩演示方案，地点为经验推荐，价格均为预估",
    }


def user_profile(nickname: str, stats: dict, excerpts: list[str] | None = None) -> dict:
    """确定性画像桩：直接由统计数据拼装结构化画像；style 由公开摘录确定性推导。"""
    top_tags = list(stats.get("top_tags", []))
    excerpts = excerpts or []
    joined = " ".join(excerpts)
    has_emoji = bool(re.search(r"[\U0001F300-\U0001FAFF☀-➿]", joined))
    avg_len = sum(len(e) for e in excerpts) / len(excerpts) if excerpts else 0
    style = {
        "catchphrases": [],
        "wording": "口语化表达为主" if excerpts else "暂无",
        "emoji": ("常带 emoji" if has_emoji else "几乎不用 emoji") if excerpts else "暂无",
        "sentence_length": ("短句为主" if avg_len < 30 else "偏长句") if excerpts else "暂无",
    }
    return {
        "topics": top_tags,
        "habit": stats.get("active_slot") or "还没有明显的活跃规律",
        "wish_leaning": stats.get("wish_leaning") or "暂无",
        "summary": f"{nickname} 最近常聊 {'、'.join(top_tags) or '日常'}，"
                   f"共丢了 {stats.get('fragment_count', 0)} 条碎片。",
        "style": style,
    }


def plan_chat(wish: str, message: str) -> str:
    """确定性追问回复桩：内容可断言，不依赖外部状态。"""
    return f"（fakes 助手）关于「{wish[:10]}」，你问的“{message[:20]}”：建议按方案第一步先走起，细节等接入真实 AI 再细聊。"


def pair_summary(name_a: str, name_b: str, topics: list[str], wish_count: int) -> str:
    """确定性关系摘要桩：正向叙事、不出现分数。"""
    parts = []
    if topics:
        parts.append(f"都关注着「{'」「'.join(topics[:3])}」")
    if wish_count:
        parts.append("还有共同想做的事，正等着一起兑现")
    if not parts:
        return f"{name_a} 和 {name_b} 的交集还在路上，各自随手丢碎片，惊喜会自己长出来。"
    return f"{name_a} 和 {name_b} 很同频：" + "，".join(parts) + "。"


def receipt_recognition() -> list[dict]:
    """确定性账单识别桩：固定两笔账（餐饮 + 交通），schema 与 RECEIPT_PROMPT 对齐。"""
    return [
        {"amount": 35.5, "merchant": "麦当劳", "time": "2026-08-16 12:30", "category": "餐饮", "type": "expense"},
        {"amount": 6.0, "merchant": "地铁", "time": "2026-08-16 09:05", "category": "交通", "type": "expense"},
    ]


def food_recognition() -> dict:
    """确定性食物识别桩：固定一餐（米饭 + 番茄炒蛋），kcal 为该项总热量估算（查表兜底值）。
    confidence 为把握度（0-1），让前端低置信标注/落库路径可测。"""
    return {
        "items": [
            {"name": "米饭", "brand": "", "grams": 200, "kcal": 232, "confidence": 0.9},
            {"name": "番茄炒蛋", "brand": "", "grams": 150, "kcal": 170, "confidence": 0.45},
        ],
        "note": "确定性桩固定估算结果，仅供参考",
    }


def web_search_food(name: str, brand: str = "", model_per_100g=None) -> dict | None:
    """确定性联网搜索桩：名字非空就返回固定营养值（每 100g），空名视为搜不到。
    brand/model_per_100g（搜索端交叉自检锚点）不影响桩值。"""
    if not (name or "").strip():
        return None
    return {
        "kcal_per_100g": 200.0,
        "protein_per_100g": 8.0,
        "fat_per_100g": 5.0,
        "cho_per_100g": 30.0,
        "basis": "确定性桩固定口径",
    }


def daily_plan(goal_type: str, framework: dict | None = None,
               yesterday: str = "", progress: str = "") -> list[dict]:
    """确定性当日计划桩：按目标类型模板拼 2-3 条，框架里有数字就直接用。"""
    framework = framework or {}
    if goal_type == "weight_loss":
        budget = framework.get("budget_kcal") or 1800
        return [
            {"content": f"今日摄入控制在 {budget} kcal 以内", "kind": "daily"},
            {"content": "快走 30 分钟", "kind": "habit"},
            {"content": "记录今日体重", "kind": "task"},
        ]
    if goal_type == "savings":
        budget = framework.get("monthly_spendable_fen")
        spend_line = f"今日花销记进账本（本月可花额度 {budget / 100:.0f} 元）" if budget else "今日花销记进账本"
        return [
            {"content": spend_line, "kind": "daily"},
            {"content": "非必要不下单，想买先放购物车晾一天", "kind": "habit"},
        ]
    if goal_type == "study":
        minutes = framework.get("daily_minutes") or 60
        return [
            {"content": f"专注学习 {minutes} 分钟", "kind": "daily"},
            {"content": "回顾昨天学的内容，花 10 分钟过一遍", "kind": "habit"},
        ]
    return [
        {"content": "把目标往前推一小步，做完打勾", "kind": "task"},
        {"content": "睡前花两分钟复盘今天", "kind": "habit"},
    ]


def savings_advice(settlement: dict) -> str:
    """确定性存款建议桩：f-string 模板把结算数字说成人话。"""
    month = settlement.get("month") or "本月"
    target = settlement.get("target_saved_fen", 0) / 100
    actual = settlement.get("actual_saved_fen", 0) / 100
    monthly = settlement.get("monthly_target_fen", 0) / 100
    return (
        f"（fakes 建议）{month}计划存 {target:.0f} 元，实际存下 {actual:.0f} 元。"
        f"按当前进度，接下来每月存 {monthly:.0f} 元就能如期达成，稳住节奏就好。"
    )


# ---------- 情绪树洞（确定性桩；与真实模式同契约，测试可断言） ----------

_DATA_KEYWORDS = ("多少钱", "花了", "支出", "账单", "记账", "预算", "计划", "打卡", "进度",
                  "热量", "卡路里", "摄入", "目标", "碎片", "记得我", "了解我")
_QUESTION_KEYWORDS = ("？", "?", "吗", "为什么", "怎么办", "如何", "要不要", "该不该")


def treehole_route(message: str) -> str:
    """意图路由桩：数据关键词 → data；疑问词 → question；否则 vent。data 优先于疑问词
    （"我这个月花了多少钱？"带问号但本质是查数据）。"""
    if any(k in message for k in _DATA_KEYWORDS):
        return "data"
    if any(k in message for k in _QUESTION_KEYWORDS):
        return "question"
    return "vent"


def treehole_rewrite(message: str) -> str:
    """查询改写桩：去标点截断，保持确定性（检索层 LIKE 关键词也走同一套 bigram）。"""
    clean = re.sub(r"[，。！？!?,.\s]+", " ", message).strip()
    return clean[:30] or message[:30]


_TOOL_RULES: list[tuple[tuple[str, ...], str]] = [
    (("多少钱", "花了", "支出", "账单", "记账", "预算"), "query_ledger"),
    (("计划", "打卡", "进度", "目标"), "query_today_plan"),
    (("热量", "卡路里", "摄入"), "query_calories"),
    (("碎片",), "search_fragments"),
    (("记得我", "了解我"), "get_memory_profile"),
]


def treehole_tool_plan(message: str, intent: str, results: list[dict]) -> dict:
    """工具决策桩：vent/question 且消息无数据关键词时不调；按关键词规则最多选 2 个，
    已调过的不重复调（单轮收敛，真实模式才有多轮循环）。"""
    called = {r.get("name") for r in results}
    calls: list[dict] = []
    if intent == "data" or any(k in message for k in _DATA_KEYWORDS):
        for keywords, name in _TOOL_RULES:
            if name in called:
                continue
            if any(k in message for k in keywords):
                args = {"keyword": treehole_rewrite(message)} if name == "search_fragments" else {}
                calls.append({"name": name, "args": args})
            if len(calls) >= 2:
                break
    return {"calls": calls}


def treehole_reply(payload: dict) -> str:
    """回复生成桩：模板化拼装，人设名/工具数据/引用全部显式出现，测试可逐点断言。"""
    persona = payload.get("persona") or {}
    name = (persona.get("name") or "").strip() or "树洞"
    intent = payload.get("intent") or "vent"
    message = payload.get("message") or ""
    parts = [f"【{name}】"]
    if intent == "vent":
        parts.append(f"我在呢。你说的这些（{message[:30]}）我都听见了，先别急着责怪自己，慢慢来，想说多少我都听着。")
    elif intent == "question":
        parts.append(f"关于「{message[:30]}」，我的想法是：先把它拆小，一步一步来，你可以先说说最卡住的点。")
    else:
        results = payload.get("tool_results") or []
        if results:
            parts.append("我帮你查了查：")
            parts.extend(f"- {r.get('summary', '')}" for r in results)
        else:
            parts.append("这个问题要查你的数据才能答准，我这边暂时没查到相关记录。")
    citations = payload.get("citations") or []
    if citations:
        quoted = "；".join(f"「{c.get('excerpt', '')}」" for c in citations[:2])
        parts.append(f"我还记得你之前说过：{quoted}。")
    return "\n".join(parts)


def treehole_image_caption(image_bytes: bytes, fmt: str = "jpeg", user_text: str = "") -> str:
    """图片描述桩：确定性文案（含随图文字回声），测试可断言 caption 进了 L0 原文/L1 抽取。"""
    base = f"用户发来的{fmt}图片"
    return f"{base}（随图说：{user_text[:20]}）" if user_text else base


def treehole_compress(old_summary: dict, messages: list[dict]) -> dict:
    """滚动摘要桩：填槽式增量合并——facts 取用户消息首句（带源消息 id），其余槽确定性拼装。"""
    merged = {
        "facts": list((old_summary or {}).get("facts") or []),
        "emotion_trail": (old_summary or {}).get("emotion_trail") or "",
        "followups": list((old_summary or {}).get("followups") or []),
        "time_anchors": list((old_summary or {}).get("time_anchors") or []),
    }
    known = {f.get("text") for f in merged["facts"]}
    for m in messages:
        if m.get("role") != "user":
            continue
        text = re.split(r"[。！？!?\n]", m.get("content") or "", maxsplit=1)[0].strip()[:50]
        if text and text not in known:
            merged["facts"].append({"text": text, "msg_ids": [m.get("id", "")]})
            known.add(text)
        day = str(m.get("created_at") or "")[:10]
        if day and not any(day in a.get("text", "") for a in merged["time_anchors"]):
            merged["time_anchors"].append({"text": f"{day} 有一次倾诉", "msg_ids": [m.get("id", "")]})
    if messages and not merged["emotion_trail"]:
        merged["emotion_trail"] = "从倾诉开始，持续被倾听与接住"
    return merged


_ATOM_RULES: list[tuple[str, str, str]] = [
    # (正则, kind, 内容模板)；只从用户消息抽取，回复不入记忆
    (r"我(?:最)?喜欢(?:吃|喝|看|听|玩)?([^，。！？!?,.\n]{1,20})", "preference", "喜欢{0}"),
    (r"我(?:讨厌|不喜欢|受不了)(?:吃|喝|看|听)?([^，。！？!?,.\n]{1,20})", "preference", "讨厌{0}"),
    (r"我(?:打算|决定|准备|想)([^，。！？!?,.\n]{2,30})", "commitment", "{0}"),
    (r"((?:今天|昨天|上周|这周|最近)[^，。！？!?,.\n]{2,40})", "event", "{0}"),
    (r"我(?:叫|是|在)([^，。！？!?,.\n]{2,20})(?:工作|上班|上学)", "fact", "{0}工作/上学"),
]


def extract_memory_atoms(user_message: str, assistant_reply: str = "") -> list[dict]:
    """L1 原子抽取桩：规则匹配，每条一个事实；assistant_reply 仅占位保持签名一致。"""
    atoms: list[dict] = []
    seen: set[str] = set()
    for pattern, kind, template in _ATOM_RULES:
        m = re.search(pattern, user_message)
        if not m:
            continue
        content = template.format(m.group(1).strip())
        if content and content not in seen:
            seen.add(content)
            atoms.append({"kind": kind, "content": content})
        if len(atoms) >= 3:
            break
    return atoms


# ---------- 门面换装：把 app.ai 的 LLM/embedding 依赖整体换成本模块桩 ----------

def _weekly_report(fragments_repr, week_start, week_end, stats, persona="", quotes=None, styles=None):
    return weekly_report(fragments_repr, week_start, week_end, stats)


def _generate_plan(content, users, real_data=None, analysis=None):
    return generate_plan(content, users)


def _plan_chat(wish, plan, participants, quotes, history, message, viewer_profile=None, member_styles=None):
    return plan_chat(wish, message)


def _pair_summary(name_a, name_b, levels, topics, wish_count):
    return pair_summary(name_a, name_b, topics, wish_count)


def _treehole_tool_plan(message, intent, tools_desc, results):
    return treehole_tool_plan(message, intent, results)


def _daily_plan(goal_type, framework, context):
    context = context or {}
    return daily_plan(goal_type, framework, str(context.get("yesterday") or ""),
                      str(context.get("progress") or ""))


def _web_search_food(name: str, brand: str = "", model_per_100g=None) -> dict | None:
    """保留门面开关语义：LLM_WEB_SEARCH≠on 直接 None（不联网降级），on 才给确定性桩。"""
    if not settings.web_search_enabled:
        return None
    return web_search_food(name, brand)


# 门面函数名 → 桩实现。视觉三件套（image_caption/recognize_receipt/recognize_food）不在此列：
# 它们的「未配置优雅跳过」口径本身就是生产行为，测试按需 monkeypatch 视觉层
PATCHES: dict[str, object] = {
    "classify_fragment": classify,
    "embed_text": embed,
    "embed_texts": lambda texts: [embed(t) for t in texts],
    "embed_food_image": lambda image_bytes, fmt="jpeg": embed(
        hashlib.md5(image_bytes).hexdigest()  # 图片字节哈希 → 同图同向量（以图搜图可测）
    ),
    "summarize_text": summarize,
    "generate_weekly_report": _weekly_report,
    "confirm_common_wishes": lambda *a, **k: [],
    "extract_plan_query": extract_plan_query,
    "generate_plan": _generate_plan,
    "generate_user_profile": user_profile,
    "plan_chat": _plan_chat,
    "generate_pair_summary": _pair_summary,
    "generate_daily_plan": _daily_plan,
    "generate_savings_advice": savings_advice,
    "web_search_food": _web_search_food,
    "treehole_route": treehole_route,
    "treehole_rewrite": treehole_rewrite,
    "treehole_tool_plan": _treehole_tool_plan,
    "treehole_reply": treehole_reply,
    "treehole_image_caption": treehole_image_caption,
    "treehole_compress": treehole_compress,
    "extract_memory_atoms": extract_memory_atoms,
}

# 首次 install 时留存的原实现：需要断言真实解析路径的用例显式装回（monkeypatch.setattr 回去）
REAL_IMPLS: dict[str, object] = {}


def install(ai_module) -> None:
    """把 ai 门面的 LLM/embedding 依赖整体换成本模块确定性桩（幂等）。

    services 一律经 ai.xxx 属性调用门面，setattr 即全局生效；用例内再用 monkeypatch
    覆盖时，teardown 自动还原回本桩（patch 时保存的现值就是桩）。
    """
    for name, fn in PATCHES.items():
        if name not in REAL_IMPLS:
            REAL_IMPLS[name] = getattr(ai_module, name)
        setattr(ai_module, name, fn)
