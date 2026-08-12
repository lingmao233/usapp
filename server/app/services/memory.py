"""记忆层：画像与关系分量的计算、dirty 打点与每晚蒸馏重算。

四分量各自落库（pair_relationships 独立列），总分读取时用 compute_pair_score 现算，
调权重不需要重算历史数据。隐私铁律：隐私碎片照常参与计算，只限制展示。
"""
import json
import logging
from collections import Counter
from datetime import datetime
from itertools import combinations

from .. import ai
from ..db.database import cosine, decode_embedding, get_conn
from . import wishes

logger = logging.getLogger("us.memory")

# 亲密度权重（设计文档 §4）：语义 0.35 / 互动 0.30 / 共同愿望 0.20 / 共同主题 0.15
WEIGHTS = {"semantic": 0.35, "interaction": 0.30, "common_wishes": 0.20, "common_topics": 0.15}

# 共同主题来源可见性标记
PUBLIC_PUBLIC = "public-public"
PRIVATE_PUBLIC = "private-public"
PRIVATE_PRIVATE = "private-private"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def compute_pair_score(components: dict) -> float:
    """亲密度总分：加权求和；无数据（为 0 / 缺失）的信号不参与，按其余信号权重归一化。

    计数型分量（共同愿望数、互动原始分）超过 1 按 1 计；某信号恒为 0 时天然被归一化跳过。
    """
    active = {k: min(float(v), 1.0) for k, v in components.items() if v and k in WEIGHTS}
    if not active:
        return 0.0
    weight_sum = sum(WEIGHTS[k] for k in active)
    return round(sum(WEIGHTS[k] * v for k, v in active.items()) / weight_sum, 4)


# ---------- dirty 打点（写路径调用） ----------

def mark_dirty(circle_id: str, user_id: str) -> None:
    """碎片发布 / 愿望确认等写路径的轻量打点：该用户画像与其所有用户对标 dirty。"""
    conn = get_conn()
    now = _now()
    conn.execute(
        """INSERT INTO user_profiles (circle_id, user_id, updated_at, dirty) VALUES (?, ?, ?, 1)
           ON CONFLICT(circle_id, user_id) DO UPDATE SET dirty=1""",
        (circle_id, user_id, now),
    )
    others = conn.execute(
        "SELECT id FROM users WHERE circle_id = ? AND id != ?", (circle_id, user_id)
    ).fetchall()
    for other in others:
        user_a, user_b = sorted((user_id, other["id"]))
        conn.execute(
            """INSERT INTO pair_relationships (circle_id, user_a, user_b, updated_at, dirty)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(circle_id, user_a, user_b) DO UPDATE SET dirty=1""",
            (circle_id, user_a, user_b, now),
        )
    conn.commit()


# ---------- 分量计算 ----------

def _semantic(frags_a: list, frags_b: list) -> float:
    """语义分量：两人碎片 embedding 跨用户余弦的最高值（沿用周报相似对思路，0.7 阈值留给展示层）。"""
    vecs_b = [decode_embedding(r["embedding"]) for r in frags_b]
    best = 0.0
    for ra in frags_a:
        va = decode_embedding(ra["embedding"])
        if va is None:
            continue
        for vb in vecs_b:
            if vb is not None:
                best = max(best, cosine(va, vb))
    return round(min(max(best, 0.0), 1.0), 4)


def _topics(frags_a: list, frags_b: list) -> tuple[list[dict], float]:
    """共同主题：tags 交集逐条带来源可见性标记；分量为 Jaccard 重合度。隐私碎片照常参与。"""
    def tag_visibility(rows: list) -> dict:
        m: dict[str, set] = {}
        for r in rows:
            for t in json.loads(r["tags"] or "[]"):
                m.setdefault(t, set()).add(r["visibility"])
        return m

    ma, mb = tag_visibility(frags_a), tag_visibility(frags_b)
    union = set(ma) | set(mb)
    if not union:
        return [], 0.0
    topics = []
    for tag in sorted(set(ma) & set(mb)):
        pa, pb = "public" in ma[tag], "public" in mb[tag]
        if pa and pb:
            source = PUBLIC_PUBLIC
        elif not pa and not pb:
            source = PRIVATE_PRIVATE
        else:
            source = PRIVATE_PUBLIC
        topics.append({"tag": tag, "source": source})
    return topics, round(len(topics) / len(union), 4)


def _pair_wish_counts(conn, circle_id: str) -> dict:
    """共同愿望分量：两人经确认（LLM / mock 相似度）的共同愿望数，按用户对计数。

    含隐私来源愿望（算分对称、展示不对称）。返回
    {(user_a, user_b): {"total": 总数, "secret": 双隐数, "public": 双公开数}}；
    双隐的秘密共同愿望只记数量不记内容（§5.1「存在一个共同的秘密愿望」提示的数据来源）。
    """
    stats: dict[tuple[str, str], dict] = {}
    for c in wishes.compute_common_wishes(circle_id, include_private=True):
        ids = c.get("wish_ids") or []
        if not ids:
            continue
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT w.user_id, w.visibility AS vis FROM wishes w WHERE w.id IN ({marks})",
            ids,
        ).fetchall()
        by_user = {r["user_id"]: r["vis"] for r in rows}
        for (a, va), (b, vb) in combinations(sorted(by_user.items()), 2):
            s = stats.setdefault((a, b), {"total": 0, "secret": 0, "public": 0})
            s["total"] += 1
            if va == "private" and vb == "private":
                s["secret"] += 1
            elif va == "public" and vb == "public":
                s["public"] += 1
    return stats


def _interaction_score(conn, circle_id: str, user_a: str, user_b: str) -> float:
    """互动分量原始分（§4）：1 评论 = 3 点赞；单向评论半分、对方回复后补满；
    点赞双向计分、单向打折。

    方向口径：评论/点赞的作者 Y 在 X 的碎片上 → 记一次 Y→X；
    自己评论/点赞自己的碎片不计。原始分可能 >1，封顶在 compute_pair_score 统一处理。
    """
    comments = {
        (r["actor"], r["owner"]): r["c"]
        for r in conn.execute(
            """SELECT c.author_id AS actor, f.user_id AS owner, COUNT(*) AS c
               FROM comments c JOIN fragments f ON f.id = c.fragment_id
               WHERE c.circle_id = ? AND c.author_id IN (?, ?) AND f.user_id IN (?, ?)
                 AND c.author_id != f.user_id
               GROUP BY c.author_id, f.user_id""",
            (circle_id, user_a, user_b, user_a, user_b),
        ).fetchall()
    }
    likes = {
        (r["actor"], r["owner"]): r["c"]
        for r in conn.execute(
            """SELECT l.user_id AS actor, f.user_id AS owner, COUNT(*) AS c
               FROM likes l JOIN fragments f ON f.id = l.fragment_id
               WHERE l.circle_id = ? AND l.user_id IN (?, ?) AND f.user_id IN (?, ?)
                 AND l.user_id != f.user_id
               GROUP BY l.user_id, f.user_id""",
            (circle_id, user_a, user_b, user_a, user_b),
        ).fetchall()
    }
    ca, cb = comments.get((user_a, user_b), 0), comments.get((user_b, user_a), 0)
    la, lb = likes.get((user_a, user_b), 0), likes.get((user_b, user_a), 0)
    # 单向评论 1.5 分/条，双向都有则双方按 3 分/条补满
    comment_score = (ca + cb) * (3.0 if ca and cb else 1.5)
    # 双向点赞 1 分/个，仅单向打五折
    like_score = (la + lb) * (1.0 if la and lb else 0.5)
    return round(comment_score + like_score, 4)


def update_pair_interaction(circle_id: str, user_x: str, user_y: str) -> None:
    """互动写路径实时重算（§4：评论/点赞当场 upsert，图上即时变粗）。

    纯 DB 计算，不走任务层、不调 LLM、不打 dirty；只动 interaction 分量，
    其余三个分量保持原值。用户对可能因本调用首次建行（尚未跑过 nightly 时）。
    """
    if user_x == user_y:
        return  # 自互动不计，也不落自闭合行
    conn = get_conn()
    user_a, user_b = sorted((user_x, user_y))
    score = _interaction_score(conn, circle_id, user_a, user_b)
    conn.execute(
        """INSERT INTO pair_relationships (circle_id, user_a, user_b, interaction, updated_at, dirty)
           VALUES (?, ?, ?, ?, ?, 0)
           ON CONFLICT(circle_id, user_a, user_b) DO UPDATE
           SET interaction = excluded.interaction, updated_at = excluded.updated_at""",
        (circle_id, user_a, user_b, score, _now()),
    )
    conn.commit()


def _active_slot(hours: list[int]) -> str:
    """活跃习惯：碎片发布时段的最高频区间。"""
    if not hours:
        return ""
    slots = Counter(
        "上午" if 6 <= h < 12 else "下午" if 12 <= h < 18 else "晚上" if 18 <= h < 24 else "深夜"
        for h in hours
    )
    return f"常在{slots.most_common(1)[0][0]}丢碎片"


def _level(value: float) -> str:
    """分量转定性等级（摘要 prompt 用，摘要不出现分数）。"""
    if value >= 0.6:
        return "高"
    if value >= 0.3:
        return "中"
    if value > 0:
        return "低"
    return "无"


# ---------- 每晚蒸馏 ----------

def _ensure_rows(conn, circle_id: str) -> None:
    """为全部成员/用户对补建行（已存在不动），新行默认 dirty=1 等待重算。"""
    now = _now()
    user_ids = [
        r["id"] for r in conn.execute("SELECT id FROM users WHERE circle_id = ?", (circle_id,))
    ]
    for uid in user_ids:
        conn.execute(
            "INSERT OR IGNORE INTO user_profiles (circle_id, user_id, updated_at) VALUES (?, ?, ?)",
            (circle_id, uid, now),
        )
    for user_a, user_b in combinations(sorted(user_ids), 2):
        conn.execute(
            """INSERT OR IGNORE INTO pair_relationships (circle_id, user_a, user_b, updated_at)
               VALUES (?, ?, ?, ?)""",
            (circle_id, user_a, user_b, now),
        )


def refresh_dirty(circle_id: str) -> dict:
    """重算本圈 dirty 的画像与用户对：分量落库 + LLM 生成/更新画像与关系摘要，然后清 dirty。

    周报生成前的强制补跑也复用本函数。14 人圈全量上限 14 画像 + 91 对，实际只处理 dirty。
    """
    conn = get_conn()
    _ensure_rows(conn, circle_id)

    nicknames = {
        r["id"]: r["nickname"]
        for r in conn.execute("SELECT id, nickname FROM users WHERE circle_id = ?", (circle_id,))
    }
    # 全圈碎片一次读出（含隐私：只限制展示，不限制计算）
    by_user: dict[str, list] = {}
    for r in conn.execute(
        "SELECT user_id, tags, visibility, embedding, created_at FROM fragments"
        " WHERE circle_id = ? AND processed = 1",
        (circle_id,),
    ).fetchall():
        by_user.setdefault(r["user_id"], []).append(r)
    wish_counts = _pair_wish_counts(conn, circle_id)
    now = _now()

    dirty_pairs = conn.execute(
        "SELECT * FROM pair_relationships WHERE circle_id = ? AND dirty = 1", (circle_id,)
    ).fetchall()
    for p in dirty_pairs:
        a, b = p["user_a"], p["user_b"]
        fa, fb = by_user.get(a, []), by_user.get(b, [])
        semantic = _semantic(fa, fb)
        topics, topic_score = _topics(fa, fb)
        wish = wish_counts.get((a, b), {"total": 0, "secret": 0, "public": 0})
        # 互动分量（第 4 期）：与写路径同一口径现算，nightly 重算幂等收敛
        interaction = _interaction_score(conn, circle_id, a, b)
        components = {
            "semantic": semantic,
            "interaction": interaction,
            "common_wishes": float(wish["total"]),
            "common_topics": topic_score,
        }
        levels = {k: _level(v) for k, v in components.items()}
        # 摘要只基于可展示材料（公开来源主题 + 双方公开的共同愿望数）：
        # 秘密共同愿望只记数量落 secret_common_wishes，内容绝不进摘要文本
        summary = ai.generate_pair_summary(
            nicknames.get(a, ""), nicknames.get(b, ""), levels,
            [t["tag"] for t in topics if t["source"] == PUBLIC_PUBLIC], wish["public"],
        )
        conn.execute(
            """UPDATE pair_relationships
               SET semantic=?, interaction=?, common_wishes=?, secret_common_wishes=?,
                   common_topics=?, topics=?, summary=?, updated_at=?, dirty=0
               WHERE circle_id=? AND user_a=? AND user_b=?""",
            (
                semantic, interaction, float(wish["total"]), wish["secret"], topic_score,
                json.dumps(topics, ensure_ascii=False), summary, now,
                circle_id, a, b,
            ),
        )

    dirty_users = conn.execute(
        "SELECT * FROM user_profiles WHERE circle_id = ? AND dirty = 1", (circle_id,)
    ).fetchall()
    for u in dirty_users:
        uid = u["user_id"]
        frags = by_user.get(uid, [])
        tag_count: Counter[str] = Counter()
        for f in frags:
            for t in json.loads(f["tags"] or "[]"):
                tag_count[t] += 1
        wish_rows = conn.execute(
            "SELECT category, COUNT(*) AS c FROM wishes"
            " WHERE circle_id = ? AND user_id = ? AND status = 'active' GROUP BY category",
            (circle_id, uid),
        ).fetchall()
        stats = {
            "top_tags": [t for t, _ in tag_count.most_common(3)],
            "fragment_count": len(frags),
            "active_slot": _active_slot([int(f["created_at"][11:13]) for f in frags]),
            "wish_leaning": max(wish_rows, key=lambda r: r["c"])["category"] if wish_rows else "",
            "wish_count": sum(r["c"] for r in wish_rows),
        }
        # style 维度的输入：该用户近期公开发言摘录（公开碎片 + 公开碎片下的评论，截 50 字）
        excerpt_rows = conn.execute(
            """SELECT content, created_at FROM fragments
               WHERE circle_id = ? AND user_id = ? AND visibility = 'public' AND content != ''
               UNION ALL
               SELECT c.content, c.created_at FROM comments c
               JOIN fragments f ON f.id = c.fragment_id
               WHERE c.circle_id = ? AND c.author_id = ? AND f.visibility = 'public'
               ORDER BY 2 DESC LIMIT 10""",
            (circle_id, uid, circle_id, uid),
        ).fetchall()
        excerpts = [r["content"][:50] for r in excerpt_rows]
        profile = ai.generate_user_profile(nicknames.get(uid, ""), stats, excerpts)
        conn.execute(
            "UPDATE user_profiles SET profile=?, updated_at=?, dirty=0"
            " WHERE circle_id=? AND user_id=?",
            (json.dumps(profile, ensure_ascii=False), now, circle_id, uid),
        )

    conn.commit()
    result = {"pairs": len(dirty_pairs), "profiles": len(dirty_users)}
    logger.info("圈子 %s 蒸馏完成：%s", circle_id, result)
    return result


# ---------- 关系图（观看者视角，§5/§6） ----------

def _private_source(tag_sets: dict, uid: str, tag: str) -> bool:
    """该用户对这条主题的贡献是否纯隐私来源：隐私 tag 集里有、公开 tag 集里没有。

    private-public 主题只有隐私来源方本人可见；若本人也有公开贡献，
    则隐私来源在对方，本人同样不可见（不能借图反推对方隐私）。
    """
    s = tag_sets.get(uid)
    return bool(s) and tag in s["private"] and tag not in s["public"]


def build_pair_graph(circle_id: str, viewer_id: str) -> dict:
    """观看者视角的关系图：成员节点 + 每个用户对一条边（score 读取时现算）。

    过滤永远在服务端（§5.1）：public-public 主题全员可见；private-public 主题
    仅隐私来源方本人可见；private-private 谁都不展示，当事人双方只凭 has_secret
    知道「存在一个共同的秘密愿望」。摘要生成时只用过可展示材料，全员可见。
    没跑过 nightly 的圈没有 pair 行：返回空 edges，前端显示引导态。
    """
    conn = get_conn()
    members = conn.execute(
        "SELECT id, nickname, avatar, created_at FROM users WHERE circle_id = ? ORDER BY created_at",
        (circle_id,),
    ).fetchall()

    # 每人公开/隐私 tag 集：判定 private-public 主题的隐私来源方
    tag_sets: dict[str, dict[str, set]] = {}
    for r in conn.execute(
        "SELECT user_id, tags, visibility FROM fragments WHERE circle_id = ? AND processed = 1",
        (circle_id,),
    ).fetchall():
        s = tag_sets.setdefault(r["user_id"], {"public": set(), "private": set()})
        for t in json.loads(r["tags"] or "[]"):
            s[r["visibility"]].add(t)

    edges = []
    rows = conn.execute(
        "SELECT * FROM pair_relationships WHERE circle_id = ? ORDER BY user_a, user_b",
        (circle_id,),
    ).fetchall()
    for p in rows:
        a, b = p["user_a"], p["user_b"]
        is_party = viewer_id in (a, b)
        topics = []
        for t in json.loads(p["topics"] or "[]"):
            tag, source = t.get("tag"), t.get("source")
            if source == PUBLIC_PUBLIC or (
                source == PRIVATE_PUBLIC and is_party and _private_source(tag_sets, viewer_id, tag)
            ):
                topics.append({"tag": tag, "source": source})
        edges.append(
            {
                "user_a": a,
                "user_b": b,
                "score": compute_pair_score(
                    {
                        "semantic": p["semantic"],
                        "interaction": p["interaction"],
                        "common_wishes": p["common_wishes"],
                        "common_topics": p["common_topics"],
                    }
                ),
                "topics": topics,
                # 双隐共同愿望只提示存在、不揭晓主题，且仅当事人双方可见
                "has_secret": bool(is_party and p["secret_common_wishes"] > 0),
                "summary": p["summary"] or "",
            }
        )
    return {
        "circle_id": circle_id,
        "nodes": [dict(m) for m in members],
        "edges": edges,
    }
