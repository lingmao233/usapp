"""树洞分层记忆（L0-L3）的读写底座。隐私铁律：全部函数以 account_id 隔离，无跨账号路径。

- L0 treehole_messages：对话原文，每轮落库，永不删（清空历史除外）；assistant 轮随行
  持久化 citations/tools（历史接口带出，刷新页面不丢「依据/刚刚查了」）
- L1 memory_atoms：一条一事实（preference/fact/event/commitment），每轮后实时抽取；
  入库前去重（同文本精确 + 向量余弦 ≥0.9 相似即跳过——反复倾诉同一偏好不堆重复条目）
- L2 memory_scenarios：见 scenarios.py（后台异步聚类）
- L3 复用圈子向 user_profiles 蒸馏（本包 __init__.py 的 refresh_dirty），
  此处只做「账号 → 各圈身份画像」的聚合读取，不另起蒸馏管线
"""
import json
import uuid
from datetime import datetime

from ...db.database import cosine, decode_embedding, encode_embedding, get_conn

ATOM_KINDS = ("preference", "fact", "event", "commitment")
ATOM_DUP_SIMILARITY = 0.9  # 同账号已有原子的向量余弦 ≥ 此值视为重复，跳过入库


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------- L0 对话原文 ----------

def append_message(account_id: str, role: str, content: str, image_url: str = "",
                   citations: list | None = None, tools: list | None = None) -> str:
    """追加一条树洞消息，返回消息 id（供 L1 原子回溯来源）。image_url 为图片消息的原图 URL；
    citations/tools 仅 assistant 轮有意义，随行持久化（history 带出）。"""
    conn = get_conn()
    msg_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO treehole_messages (id, account_id, role, content, image_url, citations, tools, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (msg_id, account_id, role, content, image_url,
         json.dumps(citations or [], ensure_ascii=False),
         json.dumps(tools or [], ensure_ascii=False), _now()),
    )
    conn.commit()
    return msg_id


def list_messages(account_id: str, limit: int | None = None,
                  before_created: str | None = None) -> list[dict]:
    """按时间正序读对话原文；limit 截尾（最近 N 条）；before_created（该条的 created_at）
    只取更早的——分页「加载更早」用。citations/tools 解析成数组带出。"""
    conn = get_conn()
    sql = ("SELECT id, role, content, image_url, citations, tools, created_at FROM treehole_messages"
           " WHERE account_id = ?")
    params: list = [account_id]
    if before_created:
        sql += " AND created_at < ?"
        params.append(before_created)
    sql += " ORDER BY created_at, rowid"
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("citations", "tools"):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except (ValueError, TypeError):
                d[k] = []
        out.append(d)
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
    """写入抽取出的原子记忆；kind 非法归 fact，内容截断 200 字。返回落库行。

    去重（优化清单第 3 项）：同账号已有原子里，内容完全一致或向量余弦 ≥
    ATOM_DUP_SIMILARITY 视为重复跳过——反复倾诉「我喜欢吃辣」不堆 N 条复读，
    检索池与 prompt 注入位（top5）不被重复条目挤占。本批内也互相去重。
    """
    from ... import ai  # 延迟导入：ai 依赖 config，避免模块加载顺序成环

    conn = get_conn()
    now = _now()

    existing = conn.execute(
        "SELECT content, embedding FROM memory_atoms WHERE account_id = ?",
        (account_id,),
    ).fetchall()
    existing_texts = [r["content"] for r in existing]
    existing_vecs = [decode_embedding(r["embedding"]) for r in existing]

    def _is_dup(text: str, vec) -> bool:
        if text in existing_texts:
            return True
        if vec is None:
            return False
        for ev in existing_vecs:
            if ev is not None and cosine(vec, ev) >= ATOM_DUP_SIMILARITY:
                return True
        return False

    out = []
    for atom in atoms:
        content = str(atom.get("content") or "").strip()[:200]
        if not content:
            continue
        kind = str(atom.get("kind") or "")
        if kind not in ATOM_KINDS:
            kind = "fact"
        try:
            vec = ai.embed_text(content)
        except Exception:  # noqa: BLE001 —— 向量不可用时只做精确文本去重，不阻塞写回
            vec = None
        if _is_dup(content, vec):
            continue
        atom_id = uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO memory_atoms (id, account_id, kind, content, source_msg_ids, embedding, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                atom_id, account_id, kind, content,
                json.dumps(source_msg_ids, ensure_ascii=False),
                encode_embedding(vec) if vec is not None else None,
                now,
            ),
        )
        existing_texts.append(content)
        if vec is not None:
            existing_vecs.append(vec)
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
