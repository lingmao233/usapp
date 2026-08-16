"""鞭策：熟人监督的唯一社会化出口，规则钉死——纯手动、对人限频一天一次、可关闭可屏蔽。"""
import uuid
from datetime import date, datetime

from fastapi import HTTPException

from ..db.database import get_conn
from . import goals as goals_svc
from . import push


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def send_nudge(goal_id: str, from_user_id: str, message: str = "") -> dict:
    """发鞭策。校验链：目标对 from_user 可见（复用 goals.viewer_level）→ nudge_enabled
    → 不在对方屏蔽名单 → 当日无 from→to 记录（对人不对目标）→ 落库 + Web Push。"""
    conn = get_conn()
    goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if goal is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    to_user_id = goal["user_id"]
    if from_user_id == to_user_id:
        raise HTTPException(status_code=400, detail="不能鞭策自己")
    # 私有/不可见目标按不存在处理，不泄露目标存在性
    if goals_svc.viewer_level(dict(goal), from_user_id) != "circle":
        raise HTTPException(status_code=404, detail="目标不存在")
    if not goal["nudge_enabled"]:
        raise HTTPException(status_code=403, detail="对方已关闭鞭策")
    if conn.execute(
        "SELECT 1 FROM nudge_blocks WHERE user_id = ? AND blocked_user_id = ?",
        (to_user_id, from_user_id),
    ).fetchone():
        raise HTTPException(status_code=403, detail="对方已屏蔽你")
    if conn.execute(
        "SELECT 1 FROM nudges WHERE from_user_id = ? AND to_user_id = ? AND substr(created_at, 1, 10) = ?",
        (from_user_id, to_user_id, date.today().isoformat()),
    ).fetchone():
        raise HTTPException(status_code=429, detail="今天已经鞭策过 TA 了")
    message = (message or "").strip()[:200]
    nudge_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO nudges (id, goal_id, from_user_id, to_user_id, message, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (nudge_id, goal_id, from_user_id, to_user_id, message, _now()),
    )
    conn.commit()
    push.notify_nudge(goal_id, from_user_id, message)
    return {"id": nudge_id, "status": "sent"}


def list_nudges(goal_id: str, user_id: str) -> dict:
    """目标鞭策列表：owner 全见留言（含发送者昵称）；圈友只见次数不见留言（简化口径）。"""
    conn = get_conn()
    goal = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if goal is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    level = goals_svc.viewer_level(dict(goal), user_id)
    if level is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM nudges WHERE goal_id = ?", (goal_id,)
    ).fetchone()["c"]
    if level != "owner":
        return {"count": count, "nudges": []}
    rows = conn.execute(
        """SELECT n.id, n.from_user_id, n.message, n.created_at, u.nickname AS from_nickname
           FROM nudges n LEFT JOIN users u ON u.id = n.from_user_id
           WHERE n.goal_id = ? ORDER BY n.created_at DESC""",
        (goal_id,),
    ).fetchall()
    return {"count": count, "nudges": [dict(r) for r in rows]}
