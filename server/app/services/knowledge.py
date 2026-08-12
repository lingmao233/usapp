"""知识库：列表、标签过滤、语义搜索。"""
import json

from .. import ai
from ..db.database import cosine, decode_embedding, encode_embedding, get_conn


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d.pop("embedding", None)
    return d


def list_items(circle_id: str, tag: str | None = None, limit: int = 50) -> dict:
    conn = get_conn()
    rows = conn.execute(
        """SELECT k.*, u.nickname AS user_nickname FROM knowledge_items k
           JOIN fragments f ON f.id = k.fragment_id
           JOIN users u ON u.id = f.user_id
           WHERE k.circle_id = ? ORDER BY k.created_at DESC LIMIT ?""",
        (circle_id, limit * 4),
    ).fetchall()
    items = [_row_to_dict(r) for r in rows]
    if tag:
        items = [i for i in items if tag in i["tags"]]
    all_tags: dict[str, int] = {}
    for i in items:
        for t in i["tags"]:
            all_tags[t] = all_tags.get(t, 0) + 1
    return {
        "items": items[:limit],
        "tags": sorted(all_tags, key=all_tags.get, reverse=True)[:20],
        "total": len(items),
    }


def search(query: str, circle_id: str, top_k: int = 5) -> dict:
    """自然语言语义搜索：query embedding 与条目 embedding 算余弦。"""
    conn = get_conn()
    q_vec = ai.embed_text(query)
    rows = conn.execute(
        """SELECT k.*, u.nickname AS user_nickname FROM knowledge_items k
           JOIN fragments f ON f.id = k.fragment_id
           JOIN users u ON u.id = f.user_id
           WHERE k.circle_id = ? AND k.embedding IS NOT NULL""",
        (circle_id,),
    ).fetchall()
    scored = []
    for row in rows:
        vec = decode_embedding(row["embedding"])
        if vec is None:
            continue
        sim = cosine(q_vec, vec)
        d = _row_to_dict(row)
        d["similarity"] = round(sim, 4)
        scored.append(d)
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return {"results": scored[:top_k]}


def reindex_item(item_id: str, vector) -> None:
    get_conn().execute(
        "UPDATE knowledge_items SET embedding=? WHERE id=?",
        (encode_embedding(vector), item_id),
    )
    get_conn().commit()
