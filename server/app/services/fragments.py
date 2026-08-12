"""碎片：发布、时间线、跨用户相关推荐、评论/点赞互动（第 4 期）、删除。"""
import json
import uuid
from datetime import datetime

from fastapi import HTTPException

from ..config import settings
from ..db.database import cosine, decode_embedding, get_conn
from . import memory

RELATED_THRESHOLD = 0.7
RELATED_TOP_K = 3


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row, include_embedding: bool = False) -> dict:
    d = dict(row)
    d["tags"] = json.loads(d.get("tags") or "[]")
    d["is_knowledge"] = bool(d["is_knowledge"])
    d["is_wish"] = bool(d["is_wish"])
    d["processed"] = bool(d["processed"])
    if not include_embedding:
        d.pop("embedding", None)
    return d


def _attach_interaction(fragments: list[dict], user_id: str | None) -> None:
    """给碎片 dict 批量补互动计数（第 4 期）：like_count / comment_count / liked_by_me。"""
    if not fragments:
        return
    conn = get_conn()
    ids = [f["id"] for f in fragments]
    marks = ",".join("?" * len(ids))
    like_counts = {
        r["fragment_id"]: r["c"]
        for r in conn.execute(
            f"SELECT fragment_id, COUNT(*) AS c FROM likes WHERE fragment_id IN ({marks}) GROUP BY fragment_id",
            ids,
        ).fetchall()
    }
    comment_counts = {
        r["fragment_id"]: r["c"]
        for r in conn.execute(
            f"SELECT fragment_id, COUNT(*) AS c FROM comments WHERE fragment_id IN ({marks}) GROUP BY fragment_id",
            ids,
        ).fetchall()
    }
    liked = {
        r["fragment_id"]
        for r in conn.execute(
            f"SELECT fragment_id FROM likes WHERE user_id = ? AND fragment_id IN ({marks})",
            [user_id or "", *ids],
        ).fetchall()
    }
    for f in fragments:
        f["like_count"] = like_counts.get(f["id"], 0)
        f["comment_count"] = comment_counts.get(f["id"], 0)
        f["liked_by_me"] = f["id"] in liked


def create_fragment(
    circle_id: str,
    user_id: str,
    content: str,
    visibility: str = "public",
    image_url: str | None = None,
) -> dict:
    if visibility not in ("public", "private"):
        raise HTTPException(status_code=400, detail="visibility 只能是 public 或 private")
    content = content.strip()
    image_url = (image_url or "").strip()
    # 配图只接受本站上传地址，防任意 URL（外链图片会绕过服务端可见性过滤）
    if image_url and not image_url.startswith("/api/uploads/"):
        raise HTTPException(status_code=400, detail="image_url 只能是本站上传地址")
    if not content and not image_url:
        raise HTTPException(status_code=400, detail="内容和图片至少有一个")
    conn = get_conn()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ? AND circle_id = ?", (user_id, circle_id)
    ).fetchone()
    if user is None:
        raise HTTPException(status_code=403, detail="你不是这个圈子的成员")
    fragment_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO fragments (id, user_id, circle_id, content, type, tags, mood,
           embedding, created_at, is_knowledge, is_wish, wish_category, ai_summary, processed, visibility, image_url)
           VALUES (?, ?, ?, ?, 'text', '[]', '', NULL, ?, 0, 0, '', '', 0, ?, ?)""",
        (fragment_id, user_id, circle_id, content, _now(), visibility, image_url or None),
    )
    conn.commit()
    return {"id": fragment_id, "status": "created"}


def list_fragments(
    circle_id: str,
    user_id: str | None = None,
    author: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    conn = get_conn()
    # 可见性规则：全圈公开 + 请求者本人的隐私碎片；author 筛选叠加同一规则，
    # author=本人时含其隐私碎片，author=他人时自然只剩公开碎片
    where = "f.circle_id = ? AND (f.visibility = 'public' OR f.user_id = ?)"
    params: list = [circle_id, user_id or ""]
    if author:
        where += " AND f.user_id = ?"
        params.append(author)
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM fragments f WHERE {where}", params
    ).fetchone()["c"]
    rows = conn.execute(
        f"""SELECT f.*, u.nickname AS user_nickname FROM fragments f
           JOIN users u ON u.id = f.user_id
           WHERE {where} ORDER BY f.created_at DESC LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()
    fragments = [_row_to_dict(r) for r in rows]
    _attach_interaction(fragments, user_id)
    return {"fragments": fragments, "total": total}


def get_fragment(fragment_id: str, user_id: str | None = None) -> dict:
    row = get_conn().execute(
        """SELECT f.*, u.nickname AS user_nickname FROM fragments f
           JOIN users u ON u.id = f.user_id WHERE f.id = ?""",
        (fragment_id,),
    ).fetchone()
    # 隐私碎片对非作者 404，不暴露存在性
    if row is None or (row["visibility"] == "private" and row["user_id"] != user_id):
        raise HTTPException(status_code=404, detail="碎片不存在")
    fragment = _row_to_dict(row)
    _attach_interaction([fragment], user_id)
    return fragment


def related_fragments(fragment_id: str, user_id: str | None = None) -> dict:
    """跨用户、相似度 ≥ 0.7、top 3。隐私碎片不进别人的推荐。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM fragments WHERE id = ?", (fragment_id,)).fetchone()
    if row is None or (row["visibility"] == "private" and row["user_id"] != user_id):
        raise HTTPException(status_code=404, detail="碎片不存在")
    target_vec = decode_embedding(row["embedding"])
    if target_vec is None:
        return {"related": []}

    rows = conn.execute(
        """SELECT f.*, u.nickname AS user_nickname FROM fragments f
           JOIN users u ON u.id = f.user_id
           WHERE f.circle_id = ? AND f.user_id != ? AND f.id != ? AND f.embedding IS NOT NULL
           AND (f.visibility = 'public' OR f.user_id = ?)""",
        (row["circle_id"], row["user_id"], fragment_id, user_id or ""),
    ).fetchall()

    scored = []
    for other in rows:
        vec = decode_embedding(other["embedding"])
        if vec is None:
            continue
        sim = cosine(target_vec, vec)
        if sim >= RELATED_THRESHOLD:
            d = _row_to_dict(other)
            d["similarity"] = round(sim, 4)
            scored.append(d)
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    top = scored[:RELATED_TOP_K]
    _attach_interaction(top, user_id)
    return {"related": top}


# ---------- 互动（第 4 期）：评论 / 点赞 ----------

def _get_interactable(fragment_id: str):
    """互动目标校验：碎片不存在 → 404；隐私碎片不可互动 → 403（隐私铁律，作者本人也不行）。"""
    row = get_conn().execute(
        "SELECT * FROM fragments WHERE id = ?", (fragment_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="碎片不存在")
    if row["visibility"] != "public":
        raise HTTPException(status_code=403, detail="隐私碎片不可互动")
    return row


def _require_member(conn, circle_id: str, user_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM users WHERE id = ? AND circle_id = ?", (user_id, circle_id)
    ).fetchone() is None:
        raise HTTPException(status_code=403, detail="你不是这个圈子的成员")


def add_comment(fragment_id: str, author_id: str, content: str, parent_id: str | None = None) -> dict:
    """发评论（楼中楼）：parent_id 为空是顶级评论，否则必须指向同一条碎片上的已有评论。"""
    frag = _get_interactable(fragment_id)
    content = content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="评论不能为空")
    conn = get_conn()
    _require_member(conn, frag["circle_id"], author_id)
    if parent_id is not None:
        parent = conn.execute(
            "SELECT 1 FROM comments WHERE id = ? AND fragment_id = ?", (parent_id, fragment_id)
        ).fetchone()
        if parent is None:
            raise HTTPException(status_code=404, detail="父评论不存在")
    comment_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO comments (id, circle_id, fragment_id, author_id, parent_id, content, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (comment_id, frag["circle_id"], fragment_id, author_id, parent_id, content, _now()),
    )
    conn.commit()
    # §4 互动计数写路径实时累加：评论他人碎片 → 当场重算该用户对互动分量（自评不计）
    memory.update_pair_interaction(frag["circle_id"], author_id, frag["user_id"])
    return {"id": comment_id, "status": "created"}


def list_comments(fragment_id: str) -> dict:
    """某碎片的评论平铺列表（带 parent_id 与作者昵称，前端组楼中楼），按时间正序。
    created_at 是秒级精度，同秒并列时用 rowid（插入序）兜底，保证顺序确定。"""
    _get_interactable(fragment_id)
    rows = get_conn().execute(
        """SELECT c.*, u.nickname AS author_nickname FROM comments c
           JOIN users u ON u.id = c.author_id
           WHERE c.fragment_id = ? ORDER BY c.created_at, c.rowid""",
        (fragment_id,),
    ).fetchall()
    return {"comments": [dict(r) for r in rows]}


def toggle_like(fragment_id: str, user_id: str) -> dict:
    """点赞 toggle：已赞则取消，返回最新状态与总数。(fragment_id, user_id) 唯一约束兜底并发。"""
    frag = _get_interactable(fragment_id)
    conn = get_conn()
    _require_member(conn, frag["circle_id"], user_id)
    existing = conn.execute(
        "SELECT id FROM likes WHERE fragment_id = ? AND user_id = ?", (fragment_id, user_id)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO likes (id, circle_id, fragment_id, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], frag["circle_id"], fragment_id, user_id, _now()),
        )
        liked = True
    else:
        conn.execute("DELETE FROM likes WHERE id = ?", (existing["id"],))
        liked = False
    like_count = conn.execute(
        "SELECT COUNT(*) AS c FROM likes WHERE fragment_id = ?", (fragment_id,)
    ).fetchone()["c"]
    conn.commit()
    # §4 写路径实时重算（自赞不计，update_pair_interaction 内部也有守卫）
    memory.update_pair_interaction(frag["circle_id"], user_id, frag["user_id"])
    return {"liked": liked, "like_count": like_count}


# ---------- 删除 ----------

def read_display_image(image_url: str) -> tuple[bytes | None, str]:
    """读碎片的展示图文件（{uuid}_d.jpg 约定推导）；旧图没有展示副本时回退原图。

    embedding / caption 一律用展示图（小、快、向量质量不受影响）。返回 (字节, 图片格式)。
    """
    if not image_url.startswith("/api/uploads/"):
        return None, ""
    base = image_url.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    display = settings.upload_dir / f"{stem}_d.jpg"
    original = settings.upload_dir / base
    path = display if display.is_file() else original
    if not path.is_file():
        return None, ""
    fmt = "jpeg" if path.suffix in (".jpg", ".jpeg") else path.suffix.lstrip(".")
    return path.read_bytes(), fmt


def _delete_unreferenced_image(image_url: str) -> None:
    """图片物理文件仅在没有其他 fragments/wishes 行引用同一 url 时删除（外链不碰）。"""
    if not image_url.startswith("/api/uploads/"):
        return
    conn = get_conn()
    refs = conn.execute(
        """SELECT (SELECT COUNT(*) FROM fragments WHERE image_url = ?)
                + (SELECT COUNT(*) FROM wishes WHERE image_url = ?) AS c""",
        (image_url, image_url),
    ).fetchone()["c"]
    if refs > 0:
        return
    # image_url 只可能来自上传端点，文件名形状已由上传侧白名单保证
    path = settings.upload_dir / image_url.rsplit("/", 1)[-1]
    display = settings.upload_dir / f"{path.stem}_d.jpg"  # 展示图副本一并清，不留孤儿文件
    for p in (path, display):
        if p.is_file():
            p.unlink()


def delete_fragment(fragment_id: str, user_id: str) -> dict:
    """删除碎片（仅作者本人）：级联清评论/点赞/来源愿望/来源知识条目，
    无引用图片文件一并删；受影响用户对互动分量当场重算，作者标 dirty 留给 nightly。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM fragments WHERE id = ?", (fragment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="碎片不存在")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="只能删除自己的碎片")

    # 先收集互动参与者：级联删除后要用他们重算各自的用户对互动分量
    actors = {
        r[0]
        for r in conn.execute(
            """SELECT author_id FROM comments WHERE fragment_id = ?
               UNION SELECT user_id FROM likes WHERE fragment_id = ?""",
            (fragment_id, fragment_id),
        ).fetchall()
    }
    image_url = row["image_url"] or ""
    conn.execute("DELETE FROM comments WHERE fragment_id = ?", (fragment_id,))
    conn.execute("DELETE FROM likes WHERE fragment_id = ?", (fragment_id,))
    conn.execute("DELETE FROM wishes WHERE fragment_id = ?", (fragment_id,))
    conn.execute("DELETE FROM knowledge_items WHERE fragment_id = ?", (fragment_id,))
    conn.execute("DELETE FROM fragments WHERE id = ?", (fragment_id,))
    conn.commit()

    if image_url:
        _delete_unreferenced_image(image_url)
    # 受影响用户对互动分量当场重算（自互动不涉及，update_pair_interaction 有守卫）
    for actor in actors:
        memory.update_pair_interaction(row["circle_id"], actor, user_id)
    # 作者画像与其用户对标 dirty：语义/主题/共同愿望分量留给 nightly 重算
    memory.mark_dirty(row["circle_id"], user_id)
    return {"id": fragment_id, "status": "deleted"}
