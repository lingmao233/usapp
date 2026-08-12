"""确定性本地桩：没有 API key 时保证开箱即跑。

- 分类桩：关键词规则
- Embedding 桩：字符 n-gram 哈希 512 维向量（相似文本余弦确实更高）
- 周报/方案桩：模板化生成
"""
import hashlib
import re
from collections import Counter

import numpy as np

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


def embed_image(data: bytes, fmt: str = "jpeg") -> np.ndarray:
    """图片桩：字节分块哈希撒点，与文本向量同维度（EMBED_DIM），确定性。"""
    vec = np.zeros(EMBED_DIM, dtype=np.float32)
    if not data:
        return vec
    for i in range(0, len(data), 64):
        idx = int.from_bytes(hashlib.md5(data[i : i + 64]).digest()[:4], "little") % EMBED_DIM
        vec[idx] += 1.0
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


def wish_suggestion(content: str, users: list[str]) -> str:
    names = "和".join(users)
    return f"{names}可以这周末先约个时间碰头，把「{content}」具体聊一聊，定个小目标就开始。"


def generate_plan(content: str, users: list[str]) -> dict:
    names = "、".join(users)
    return {
        "time": "本周六下午 14:00",
        "location": "大家中间点的咖啡馆碰头，再一起出发",
        "budget": "人均 100 元以内",
        "steps": [
            f"{names}在群里确认时间，先到先得",
            f"围绕「{content}」各自搜一个备选方案，周四前丢进圈子",
            "周六碰头投票，当场定下来就出发",
        ],
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
