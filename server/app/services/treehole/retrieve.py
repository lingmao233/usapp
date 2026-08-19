"""树洞混合检索（RAG 的 R）：向量召回 + 关键词 LIKE 双路，RRF 融合，留 rerank 接口位。

工期取舍（交接文档§二）：FTS5 BM25 暂缓，先 numpy 暴力余弦 + LIKE 双路——几千条碎片
毫秒级，零基础设施；rerank 钩子已留（默认恒等），真实模式可换 LLM 精排。
检索范围：本人全部身份的碎片（含私密——树洞只服务本人）+ 本人 L1 原子记忆。
"""
import re
from datetime import datetime

from ... import ai
from ...db.database import cosine, decode_embedding, get_conn

_RRF_K = 60  # RRF 平滑常数（惯例值）
_STOP_CHARS = set("的了是在我你他她它们这那有和就都也不与及或着过啊呢吧吗很还又于为到想去要来会把好一个些什怎么没可可以上下中天今明")


def _keywords(text: str) -> list[str]:
    """确定性关键词抽取：高频字符 bigram（去停用字），最多 3 个。"""
    clean = re.sub(r"[^\w一-鿿]+", "", text)
    seen: list[str] = []
    for i in range(len(clean) - 1):
        gram = clean[i : i + 2]
        if any(ch in _STOP_CHARS for ch in gram) or gram in seen:
            continue
        seen.append(gram)
        if len(seen) >= 3:
            break
    return seen


def _age_decay(created_at: str) -> float:
    """时效衰减：半衰期 30 天（越早权重越低，下限 0.3 防老记忆永不翻身）。"""
    try:
        days = (datetime.now() - datetime.fromisoformat(str(created_at))).days
    except ValueError:
        return 1.0
    return max(0.3, 0.5 ** (max(days, 0) / 30.0))


def _account_user_ids(conn, account_id: str) -> list[str]:
    return [
        r["user_id"]
        for r in conn.execute(
            "SELECT user_id FROM memberships WHERE account_id = ?", (account_id,)
        ).fetchall()
    ]


def rerank_hits(query: str, hits: list[dict]) -> list[dict]:
    """rerank 接口位：当前恒等（RRF 序即最终序）。真实模式要精排时在此接 LLM rerank，
    输入 query + 候选，输出重排后的 hits，契约不变。"""
    return hits


def recall(account_id: str, query: str, limit: int = 5) -> list[dict]:
    """混合召回：本人碎片 + L1 原子，向量与 LIKE 双路 RRF 融合，乘时效衰减，取 top N。

    命中项：{"kind": "fragment"/"atom", "id", "excerpt", "created_at", "score"}。
    """
    conn = get_conn()
    candidates: dict[tuple[str, str], dict] = {}

    def _add(kind: str, row_id: str, text: str, created_at: str, rank: int, path: str) -> None:
        key = (kind, row_id)
        entry = candidates.setdefault(key, {
            "kind": kind, "id": row_id, "excerpt": text[:80], "created_at": created_at,
            "score": 0.0,
        })
        entry["score"] += 1.0 / (_RRF_K + rank)  # RRF：每路各贡献一份倒数名次分

    user_ids = _account_user_ids(conn, account_id)
    vec = ai.embed_text(query)

    # 向量路：碎片 + 原子，暴力余弦
    for kind, table, id_col, text_col, extra_where, params in (
        ("fragment", "fragments", "id", "content",
         f"user_id IN ({','.join('?' * len(user_ids))})" if user_ids else "1=0",
         tuple(user_ids)),
        ("atom", "memory_atoms", "id", "content", "account_id = ?", (account_id,)),
    ):
        scored = []
        for row in conn.execute(
            f"SELECT {id_col} AS rid, {text_col} AS txt, created_at, embedding"
            f" FROM {table} WHERE {extra_where} AND embedding IS NOT NULL",
            params,
        ).fetchall():
            v = decode_embedding(row["embedding"])
            if v is not None:
                scored.append((cosine(vec, v), row))
        for rank, (sim, row) in enumerate(
            sorted(scored, key=lambda x: x[0], reverse=True)[: limit * 2], start=1
        ):
            if sim > 0.05:  # 噪声底线：确定性桩（tests/fakes）哈希向量也有此下限
                _add(kind, row["rid"], row["txt"], row["created_at"], rank, "vector")

    # 关键词路：LIKE（碎片全量、原子全量，各取 top 10）
    for kw in _keywords(query):
        escaped = kw.replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        if user_ids:
            marks = ",".join("?" * len(user_ids))
            for rank, row in enumerate(conn.execute(
                f"""SELECT id AS rid, content AS txt, created_at FROM fragments
                    WHERE user_id IN ({marks}) AND content LIKE ? ESCAPE '\\'
                    ORDER BY created_at DESC LIMIT 10""",
                (*user_ids, like),
            ).fetchall(), start=1):
                _add("fragment", row["rid"], row["txt"], row["created_at"], rank, "keyword")
        for rank, row in enumerate(conn.execute(
            """SELECT id AS rid, content AS txt, created_at FROM memory_atoms
               WHERE account_id = ? AND content LIKE ? ESCAPE '\\'
               ORDER BY created_at DESC LIMIT 10""",
            (account_id, like),
        ).fetchall(), start=1):
            _add("atom", row["rid"], row["txt"], row["created_at"], rank, "keyword")

    # 融合分 × 时效衰减，取 top N 后过 rerank 钩子
    fused = sorted(
        candidates.values(),
        key=lambda h: h["score"] * _age_decay(h["created_at"]),
        reverse=True,
    )[:limit]
    for h in fused:
        h["score"] = round(h["score"] * _age_decay(h["created_at"]), 6)
    return rerank_hits(query, fused)
