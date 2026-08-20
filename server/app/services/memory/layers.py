"""树洞分层记忆（L0-L3）的读写底座。隐私铁律：全部函数以 account_id 隔离，无跨账号路径。

- L0 treehole_messages：对话原文，每轮落库，永不删（清空历史除外）
- L1 memory_atoms：一条一事实（preference/fact/event/commitment），每轮后实时抽取
- L2 memory_scenarios：见 scenarios.py（后台异步聚类）
- L3 复用圈子向 user_profiles 蒸馏（本包 __init__.py 的 refresh_dirty），
  此处只做「账号 → 各圈身份画像」的聚合读取，不另起蒸馏管线
"""
import json
import uuid
from datetime import datetime

from ...db.database import encode_embedding, get_conn

ATOM_KINDS = ("preference", "fact", "event", "commitment")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- L0 对话原文 ----------

def append_message(account_id: str, role: str, content: str, image_url: str = "") -> str:
    """追加一条树洞消息，返回消息 id（供 L1 原子回溯来源）。image_url 为图片消息的原图 URL。"""
    conn = get_conn()
    msg_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO treehole_messages (id, account_id, role, content, image_url, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, account_id, role, content, image_url, _now()),
    )
    conn.commit()
    return msg_id


def list_messages(account_id: str, limit: int | None = None) -> list[dict]:
    """按时间正序读对话原文；limit 截尾（最近 N 条）。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, role, content, image_url, created_at FROM treehole_messages"
        " WHERE account_id = ? ORDER BY created_at, rowid",
        (account_id,),
    ).fetchall()
    out = [dict(r) for r in rows]
    return out[-limit:] if limit else out


def count_messages(account_id: str) -> int:
    conn = get_conn()
    return conn.execute(
        "SELECT COUNT(*) AS c FROM treehole_messages WHERE account_id = ?", (account_id,)
    ).fetchone()["c"]


def clear_messages(account_id: str) -> None:
    """清空 L0（DELETE /history 用）。L1/L2 记忆保留——清空的是对话，不是记忆。"""
    conn = get_conn()
    conn.execute("DELETE FROM treehole_messages WHERE account_id = ?", (account_id,))
    conn.commit()


# ---------- L1 原子记忆 ----------

def insert_atoms(account_id: str, atoms: list[dict], source_msg_ids: list[str]) -> list[dict]:
    """写入抽取出的原子记忆；kind 非法归 fact，内容截断 200 字。返回落库行。"""
    from ... import ai  # 延迟导入：ai 依赖 config，避免模块加载顺序成环

    conn = get_conn()
    now = _now()
    out = []
    for atom in atoms:
        content = str(atom.get("content") or "").strip()[:200]
        if not content:
            continue
        kind = str(atom.get("kind") or "")
        if kind not in ATOM_KINDS:
            kind = "fact"
        atom_id = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO memory_atoms (id, account_id, kind, content, source_msg_ids, embedding, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                atom_id, account_id, kind, content,
                json.dumps(source_msg_ids, ensure_ascii=False),
                encode_embedding(ai.embed_text(content)),
                now,
            ),
        )
        out.append({"id": atom_id, "kind": kind, "content": content,
                    "source_msg_ids": source_msg_ids, "created_at": now})
    conn.commit()
    return out


def list_atoms(account_id: str, kind: str | None = None, limit: int = 50) -> list[dict]:
    """读 L1 原子（不含 embedding blob），按时间倒序。"""
    conn = get_conn()
    sql = ("SELECT id, kind, content, source_msg_ids, created_at FROM memory_atoms"
           " WHERE account_id = ?")
    params: list = [account_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["source_msg_ids"] = json.loads(d["source_msg_ids"] or "[]")
        out.append(d)
    return out


# ---------- L3 长期画像（复用圈子蒸馏，聚合到账号视角） ----------

def account_profile(account_id: str) -> dict:
    """账号级画像：合并该账号在各圈身份（memberships → user_profiles）的已蒸馏画像。

    圈子向蒸馏管线不动；树洞只读取。合并口径：topics 去重保序、summary 逐段并列、
    habit/wish_leaning 取第一个非空。没跑过蒸馏时返回 {}（生成端按「暂无画像」处理）。
    """
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.profile FROM user_profiles p
           JOIN memberships m ON m.circle_id = p.circle_id AND m.user_id = p.user_id
           WHERE m.account_id = ?""",
        (account_id,),
    ).fetchall()
    topics: list[str] = []
    summaries: list[str] = []
    habit = ""
    wish_leaning = ""
    for r in rows:
        try:
            p = json.loads(r["profile"] or "{}")
        except json.JSONDecodeError:
            continue
        for t in p.get("topics") or []:
            if t and t not in topics:
                topics.append(t)
        if p.get("summary"):
            summaries.append(p["summary"])
        habit = habit or p.get("habit") or ""
        wish_leaning = wish_leaning or p.get("wish_leaning") or ""
    if not (topics or summaries):
        return {}
    return {
        "topics": topics[:10],
        "habit": habit,
        "wish_leaning": wish_leaning,
        "summary": "；".join(summaries)[:500],
    }
