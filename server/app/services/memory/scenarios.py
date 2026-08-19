"""L2 场景记忆：把 L1 原子按主题聚类成场景块（后台异步，对话后触发）。

首版从简（交接文档§四：可先简单实现）——同 kind 内按共享字符 bigram 并查集聚类，
主题名取簇内最高频 bigram，最大的簇置顶（pinned=1）。生成端按置顶主题常驻注入。
"""
import json
import re
import uuid
from collections import Counter
from datetime import datetime

from ...db.database import get_conn

_STOP_CHARS = set("的了是在我你他她它们这那有和就都也不与及或着过啊呢吧吗很还又于为到想去要来会把好一个些什怎么没可可以上下中天今明")
# 抽取模板带来的 kind 样板词：参与聚类但不当主题名（"喜欢辣的"的主题应该是"辣的"）
_TOPIC_STOPWORDS = {"喜欢", "讨厌", "打算", "决定", "准备"}


def _bigrams(text: str) -> list[str]:
    clean = re.sub(r"[^\w一-鿿]+", "", text)
    return [
        clean[i : i + 2]
        for i in range(len(clean) - 1)
        if clean[i : i + 2] not in _TOPIC_STOPWORDS
        and not any(ch in _STOP_CHARS for ch in clean[i : i + 2])
    ]


def refresh_scenarios(account_id: str) -> dict:
    """重算该账号的场景记忆（幂等：先清再聚）。返回 {"scenarios": n}。"""
    conn = get_conn()
    atoms = [
        dict(r)
        for r in conn.execute(
            "SELECT id, kind, content FROM memory_atoms WHERE account_id = ?"
            " ORDER BY created_at, rowid",
            (account_id,),
        ).fetchall()
    ]
    conn.execute("DELETE FROM memory_scenarios WHERE account_id = ?", (account_id,))
    if not atoms:
        conn.commit()
        return {"scenarios": 0}

    # 同 kind 内并查集：两条原子共享任一 bigram 即同簇
    clusters: list[list[dict]] = []
    for atom in atoms:
        grams = set(_bigrams(atom["content"]))
        placed = False
        for cluster in clusters:
            if cluster[0]["kind"] != atom["kind"]:
                continue
            cluster_grams = set().union(*(c["_grams"] for c in cluster))
            if grams & cluster_grams:
                atom["_grams"] = grams
                cluster.append(atom)
                placed = True
                break
        if not placed:
            atom["_grams"] = grams
            clusters.append([atom])

    # 只保留 ≥2 条的簇（单条原子还够不成「场景」）；主题 = 簇内最高频 bigram
    scenarios = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        counter: Counter[str] = Counter()
        for c in cluster:
            counter.update(c["_grams"])
        topic = counter.most_common(1)[0][0]
        scenarios.append((topic, [c["id"] for c in cluster]))

    now = datetime.now().isoformat(timespec="seconds")
    biggest = max((len(ids) for _, ids in scenarios), default=0)
    for topic, ids in scenarios:
        conn.execute(
            "INSERT INTO memory_scenarios (id, account_id, topic, atom_ids, pinned, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], account_id, topic,
             json.dumps(ids, ensure_ascii=False), 1 if len(ids) == biggest else 0, now),
        )
    conn.commit()
    return {"scenarios": len(scenarios)}


def list_scenarios(account_id: str, limit: int = 10) -> list[dict]:
    """读场景记忆（置顶在前），生成端注入用。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT topic, atom_ids, pinned, updated_at FROM memory_scenarios"
        " WHERE account_id = ? ORDER BY pinned DESC, updated_at DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["atom_ids"] = json.loads(d["atom_ids"] or "[]")
        out.append(d)
    return out
