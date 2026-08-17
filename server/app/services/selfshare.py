"""Self 共享与朋友任务：类别 × 圈子开关（self_sharing），过滤永远在服务端。

- category ∈ goal/plan/ledger/calorie；level ∈ progress/detail 仅 goal/plan 用，
  ledger/calorie 只有开关无档位（level 恒 ''）；有行=共享，删行=关闭
- 朋友任务聚合：圈内其他成员共享出来的目标 + 今日计划，按人分组，附鞭策状态；
  level=progress 只给进度不给明细；未共享的类别绝不出现在响应里
"""
import json
import uuid
from datetime import date, datetime

from fastapi import HTTPException

from ..db.database import get_conn

CATEGORIES = ("goal", "plan", "ledger", "calorie")
LEVELS = ("progress", "detail")
# 仅这两类有档位；其余类别只有开关
_LEVELED = ("goal", "plan")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def require_account(conn, account_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM accounts WHERE id = ?", (account_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="账号不存在")


def _require_membership(conn, account_id: str, circle_id: str) -> None:
    if conn.execute(
        "SELECT 1 FROM circles WHERE id = ?", (circle_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="圈子不存在")
    if conn.execute(
        "SELECT 1 FROM memberships WHERE account_id = ? AND circle_id = ?",
        (account_id, circle_id),
    ).fetchone() is None:
        raise HTTPException(status_code=403, detail="只能设置自己所在圈子的共享")


# ---------- 共享开关读写（owner 操作） ----------

def list_sharing(account_id: str) -> dict:
    """我的共享开关列表（全部圈子 × 类别）。"""
    conn = get_conn()
    require_account(conn, account_id)
    rows = conn.execute(
        """SELECT s.circle_id, s.category, s.level, c.name AS circle_name
           FROM self_sharing s JOIN circles c ON c.id = s.circle_id
           WHERE s.account_id = ? ORDER BY s.created_at""",
        (account_id,),
    ).fetchall()
    return {"account_id": account_id, "items": [dict(r) for r in rows]}


def upsert_sharing(
    account_id: str, circle_id: str, category: str, level: str | None = None
) -> dict:
    """开/调共享（UPSERT）：仅 owner 本人操作自己的账号；圈子须为自己所在圈子。

    goal/plan 的 level 缺省 progress（隐私优先给最小粒度）；ledger/calorie 无档位，level 恒 ''。
    """
    conn = get_conn()
    require_account(conn, account_id)
    _require_membership(conn, account_id, circle_id)
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="category 只能是 goal/plan/ledger/calorie")
    if category in _LEVELED:
        level = level or "progress"
        if level not in LEVELS:
            raise HTTPException(status_code=400, detail="level 只能是 progress 或 detail")
    else:
        level = ""
    conn.execute(
        """INSERT INTO self_sharing (account_id, circle_id, category, level, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(account_id, circle_id, category) DO UPDATE SET level = excluded.level""",
        (account_id, circle_id, category, level, _now()),
    )
    conn.commit()
    return {"account_id": account_id, "circle_id": circle_id, "category": category, "level": level}


def delete_sharing(account_id: str, circle_id: str, category: str) -> dict:
    """关闭共享（删行）：幂等，本就没开过也返回成功。"""
    conn = get_conn()
    require_account(conn, account_id)
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="category 只能是 goal/plan/ledger/calorie")
    conn.execute(
        "DELETE FROM self_sharing WHERE account_id = ? AND circle_id = ? AND category = ?",
        (account_id, circle_id, category),
    )
    conn.commit()
    return {"account_id": account_id, "circle_id": circle_id, "category": category, "shared": False}


# ---------- 可见级别判定（服务端过滤的唯一入口） ----------

def share_level(conn, owner_account_id: str, viewer_account_id: str, category: str) -> str | None:
    """viewer 对 owner 某类别的可见级别：'detail' / 'progress'（无档位类别恒 'detail'）/ None。

    可见 = owner 共享到的圈子里，存在一个圈子 viewer 也是成员（memberships）。
    多圈子不同档位时取更高档（detail > progress）。
    """
    rows = conn.execute(
        """SELECT s.level FROM self_sharing s
           JOIN memberships m ON m.circle_id = s.circle_id AND m.account_id = ?
           WHERE s.account_id = ? AND s.category = ?""",
        (viewer_account_id, owner_account_id, category),
    ).fetchall()
    if not rows:
        return None
    levels = {r["level"] for r in rows}
    if category not in _LEVELED:
        return "detail"
    return "detail" if "detail" in levels else "progress"


# ---------- 朋友任务聚合 ----------

def _goal_progress(conn, goal_id: str, goal_type: str, params: dict) -> dict:
    """进度摘要：完成百分比、连续全勤天数、今日完成 x/y；存款目标用金额口径覆盖百分比。

    与 goals._progress 同口径（独立实现避免 services 间循环依赖）。
    """
    rows = conn.execute(
        "SELECT date, done FROM plan_items WHERE goal_id = ?", (goal_id,)
    ).fetchall()
    today = date.today().isoformat()
    total = len(rows)
    done = sum(1 for r in rows if r["done"])
    today_rows = [r for r in rows if r["date"] == today]
    by_date: dict[str, list[int]] = {}
    for r in rows:
        by_date.setdefault(r["date"], []).append(r["done"])
    streak = 0
    for d in sorted(by_date, reverse=True):
        if not all(by_date[d]):
            if d == today:
                continue  # 今天还没打完勾，不破连续
            break
        streak += 1
    percent = round(done / total * 100) if total else 0
    if goal_type == "savings":
        target = int(params.get("target_fen") or 0)
        if target > 0:
            percent = min(100, round(int(params.get("saved_fen") or 0) / target * 100))
    return {
        "percent": percent,
        "streak_days": streak,
        "today_done": sum(1 for r in today_rows if r["done"]),
        "today_total": len(today_rows),
        "total_done": done,
        "total_items": total,
    }


def _friend_goal(conn, row, level: str) -> dict:
    """共享给圈友的目标：progress 档只给进度摘要；detail 档附 params/answers/framework。"""
    params = json.loads(row["params"] or "{}")
    d = {
        "id": row["id"],
        "type": row["type"],
        "title": row["title"],
        "status": row["status"],
        "nudge_enabled": bool(row["nudge_enabled"]),
        "created_at": row["created_at"],
        "share_level": level,
        "progress": _goal_progress(conn, row["id"], row["type"], params),
    }
    if level == "detail":
        d["params"] = params
        d["answers"] = json.loads(row["answers"] or "{}")
        d["framework"] = json.loads(row["framework"] or "{}")
    return d


def _friend_plan(conn, account_id: str, day: str, level: str) -> dict:
    """共享给圈友的今日计划：progress 档只给完成计数；detail 档附条目明细。"""
    rows = conn.execute(
        """SELECT id, content, kind, done FROM plan_items
           WHERE account_id = ? AND date = ? ORDER BY created_at, rowid""",
        (account_id, day),
    ).fetchall()
    d = {
        "date": day,
        "today_done": sum(1 for r in rows if r["done"]),
        "today_total": len(rows),
        "share_level": level,
    }
    if level == "detail":
        d["items"] = [
            {"id": r["id"], "content": r["content"], "kind": r["kind"], "done": bool(r["done"])}
            for r in rows
        ]
    return d


def friend_tasks(circle_id: str, viewer_account_id: str) -> dict:
    """朋友任务：圈内其他成员共享出来的目标 + 今日计划，按人分组，附鞭策状态。

    只返回已共享类别；未共享/未入圈的数据绝不出现在响应里（隐私铁律）。
    viewer_nudged_today 对人不对目标（与鞭策限频同口径）。
    """
    conn = get_conn()
    if conn.execute(
        "SELECT 1 FROM circles WHERE id = ?", (circle_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="圈子不存在")
    if conn.execute(
        "SELECT 1 FROM memberships WHERE account_id = ? AND circle_id = ?",
        (viewer_account_id, circle_id),
    ).fetchone() is None:
        raise HTTPException(status_code=403, detail="你不是这个圈子的成员")
    others = conn.execute(
        """SELECT m.account_id, u.nickname FROM memberships m
           JOIN users u ON u.id = m.user_id
           WHERE m.circle_id = ? AND m.account_id != ? ORDER BY m.created_at""",
        (circle_id, viewer_account_id),
    ).fetchall()
    day = date.today().isoformat()
    members = []
    for other in others:
        owner_id = other["account_id"]
        out: dict = {"account_id": owner_id, "nickname": other["nickname"]}
        goal_level = share_level(conn, owner_id, viewer_account_id, "goal")
        if goal_level:
            rows = conn.execute(
                "SELECT * FROM goals WHERE account_id = ? AND status = 'active'"
                " ORDER BY created_at DESC",
                (owner_id,),
            ).fetchall()
            out["goals"] = [_friend_goal(conn, r, goal_level) for r in rows]
        plan_level = share_level(conn, owner_id, viewer_account_id, "plan")
        if plan_level:
            out["plan"] = _friend_plan(conn, owner_id, day, plan_level)
        if "goals" not in out and "plan" not in out:
            continue  # 未共享任何类别的人不出现
        out["viewer_nudged_today"] = conn.execute(
            """SELECT 1 FROM nudges WHERE from_account_id = ? AND to_account_id = ?
               AND substr(created_at, 1, 10) = ?""",
            (viewer_account_id, owner_id, day),
        ).fetchone() is not None
        members.append(out)
    return {"circle_id": circle_id, "date": day, "members": members}
