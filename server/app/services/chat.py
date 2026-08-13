"""会话（方案追问）：通用 chat_threads/chat_messages 两表，kind+ref_id 关联业务对象。

当前只有 kind='plan'（ref_id=wish_id，每人每个愿望一条线程）；
未来独立 AI 聊天页直接复用本模块与表结构。
"""
import json
import uuid
from datetime import datetime

from fastapi import HTTPException

from .. import ai
from ..db.database import get_conn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_wish(wish_id: str):
    row = get_conn().execute("SELECT * FROM wishes WHERE id = ?", (wish_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="愿望不存在")
    return row


def _require_member(circle_id: str, user_id: str) -> None:
    if get_conn().execute(
        "SELECT 1 FROM users WHERE id = ? AND circle_id = ?", (user_id, circle_id)
    ).fetchone() is None:
        raise HTTPException(status_code=403, detail="你不是这个圈子的成员")


def _get_or_create_thread(conn, circle_id: str, user_id: str, kind: str, ref_id: str) -> str:
    row = conn.execute(
        "SELECT id FROM chat_threads WHERE user_id = ? AND kind = ? AND ref_id = ?",
        (user_id, kind, ref_id),
    ).fetchone()
    if row:
        return row["id"]
    thread_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO chat_threads (id, circle_id, user_id, kind, ref_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, circle_id, user_id, kind, ref_id, _now()),
    )
    return thread_id


def _thread_messages(conn, thread_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, role, content, created_at FROM chat_messages WHERE thread_id = ? ORDER BY created_at",
        (thread_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _find_thread_id(conn, user_id: str, kind: str, ref_id: str) -> str | None:
    row = conn.execute(
        "SELECT id FROM chat_threads WHERE user_id = ? AND kind = ? AND ref_id = ?",
        (user_id, kind, ref_id),
    ).fetchone()
    return row["id"] if row else None


def list_plan_chat(wish_id: str, user_id: str) -> dict:
    """读取方案追问记录；没聊过返回空列表。"""
    wish = _get_wish(wish_id)
    _require_member(wish["circle_id"], user_id)
    thread_id = _find_thread_id(get_conn(), user_id, "plan", wish_id)
    messages = _thread_messages(get_conn(), thread_id) if thread_id else []
    return {"messages": messages}


def send_plan_chat(wish_id: str, user_id: str, message: str) -> dict:
    """追加一条用户追问并生成助手回复，返回全量对话。

    上下文：愿望原文 + 已定方案 + 圈内本周公开语录 + 近 10 条对话。
    方案还没生成时拒绝追问（没有方案聊什么）。
    """
    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="问题不能为空")
    wish = _get_wish(wish_id)
    _require_member(wish["circle_id"], user_id)
    if not wish["plan"]:
        raise HTTPException(status_code=400, detail="方案还没生成，先点「生成方案」")

    conn = get_conn()
    thread_id = _get_or_create_thread(conn, wish["circle_id"], user_id, "plan", wish_id)
    conn.execute(
        "INSERT INTO chat_messages (id, thread_id, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
        (uuid.uuid4().hex[:12], thread_id, message, _now()),
    )
    conn.commit()

    history = _thread_messages(conn, thread_id)
    # 语录复用周报的公开检索（隐私铁律同源）：只取本周公开碎片/评论原句
    from . import memory, reports  # 延迟导入：reports/memory 与 chat 无相互依赖，仅此处用到

    week_start, week_end = reports.current_week_range()
    quotes = reports._collect_quotes(conn, wish["circle_id"], week_start, week_end)
    plan = json.loads(wish["plan"])
    participants = plan.get("participants") or [_nickname(wish["user_id"])]
    # 画像注入（viewer-relative）：自己全量（含隐私来源统计，仅本人可见）+ 其他参与者仅 style
    profiles = memory.get_profiles(wish["circle_id"])
    uid_by_nick = {
        r["nickname"]: r["id"]
        for r in conn.execute(
            "SELECT id, nickname FROM users WHERE circle_id = ?", (wish["circle_id"],)
        )
    }
    member_styles = []
    for name in participants:
        uid = uid_by_nick.get(name)
        if uid and uid != user_id:
            line = ai.format_style_digest(name, profiles.get(uid, {}))
            if line:
                member_styles.append(line)
    reply = ai.plan_chat(
        wish["content"], plan, participants, quotes, history, message,
        viewer_profile=profiles.get(user_id), member_styles=member_styles,
    )

    conn.execute(
        "INSERT INTO chat_messages (id, thread_id, role, content, created_at) VALUES (?, ?, 'assistant', ?, ?)",
        (uuid.uuid4().hex[:12], thread_id, reply, _now()),
    )
    conn.commit()
    return {"messages": _thread_messages(conn, thread_id)}


def _nickname(user_id: str) -> str:
    row = get_conn().execute("SELECT nickname FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["nickname"] if row else "朋友"
