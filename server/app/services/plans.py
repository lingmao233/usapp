"""每日计划：单一今日清单，AI 懒生成条目与用户自定义条目混排，打勾/编辑/删除。

懒生成照抄周报四层模式（reports.list_reports）：plan_items 表即缓存（按 user_id+date 查
source='ai'）、cache 旗标防抖 plan_generating:{user}:{date} ex=600、路由层 BackgroundTasks
接力、tasks.run_task 包裹。无目标也可用：自定义条目 goal_id 为 NULL。
"""
import json
import re
import uuid
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from .. import ai
from ..db import cache
from ..db.database import get_conn
from . import tasks

ITEM_KINDS = ("habit", "daily", "task")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _require_user(conn, user_id: str) -> None:
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="用户不存在")


def _item_dict(row) -> dict:
    d = dict(row)
    d["done"] = bool(d["done"])
    return d


def _progress_text(conn, goal) -> str:
    """剩余目标进度一句话（进 prompt 上下文）：存款用金额口径，其余用条目完成率。"""
    if goal["type"] == "savings":
        params = json.loads(goal["params"] or "{}")
        target = int(params.get("target_fen") or 0)
        if target > 0:
            return f"已存 {int(params.get('saved_fen') or 0) / 100:.0f} 元 / 目标 {target / 100:.0f} 元"
    total = conn.execute(
        "SELECT COUNT(*) AS c FROM plan_items WHERE goal_id = ?", (goal["id"],)
    ).fetchone()["c"]
    if not total:
        return ""
    done = conn.execute(
        "SELECT COUNT(*) AS c FROM plan_items WHERE goal_id = ? AND done = 1", (goal["id"],)
    ).fetchone()["c"]
    return f"累计完成 {done}/{total} 条计划条目"


def today(user_id: str) -> dict:
    """今日清单：无 AI 条目且用户有 active 目标时懒触发生成。

    generating='trigger' 由路由层放 BackgroundTasks 接力并改写为 True（周报同款语义）。
    """
    conn = get_conn()
    _require_user(conn, user_id)
    day = date.today().isoformat()
    rows = conn.execute(
        "SELECT * FROM plan_items WHERE user_id = ? AND date = ? ORDER BY created_at, rowid",
        (user_id, day),
    ).fetchall()
    generating: bool | str = False
    if not any(r["source"] == "ai" for r in rows):
        has_goal = conn.execute(
            "SELECT 1 FROM goals WHERE user_id = ? AND status = 'active' LIMIT 1", (user_id,)
        ).fetchone()
        if has_goal:
            flag = f"plan_generating:{user_id}:{day}"
            if not cache.client.get(flag):
                cache.client.set(flag, "1", ex=600)
                generating = "trigger"
            else:
                generating = True
    return {"date": day, "items": [_item_dict(r) for r in rows], "generating": generating}


def generate_today(user_id: str) -> dict:
    """后台生成今日 AI 条目：各 active 目标的规则框架 + 昨日未完成条目（学习补足上下文）。

    已有 AI 条目直接返回（幂等）；失败经任务层重试后仍不行才 500。
    """
    day = date.today().isoformat()
    conn = get_conn()
    existing = conn.execute(
        "SELECT 1 FROM plan_items WHERE user_id = ? AND date = ? AND source = 'ai' LIMIT 1",
        (user_id, day),
    ).fetchone()
    if existing:
        return {"status": "exists"}

    def _job() -> None:
        goals = conn.execute(
            "SELECT * FROM goals WHERE user_id = ? AND status = 'active'", (user_id,)
        ).fetchall()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        unfinished = conn.execute(
            "SELECT goal_id, content FROM plan_items WHERE user_id = ? AND date = ? AND done = 0",
            (user_id, yesterday),
        ).fetchall()
        for goal in goals:
            framework = json.loads(goal["framework"] or "{}")
            missed = [u["content"] for u in unfinished if u["goal_id"] == goal["id"]]
            context = {
                "yesterday": f"昨日未完成：{'；'.join(missed)}" if missed else "",
                "progress": _progress_text(conn, goal),
            }
            for item in ai.generate_daily_plan(goal["type"], framework, context):
                content = str(item.get("content") or "").strip()[:100]
                if not content:
                    continue
                kind = str(item.get("kind") or "daily")
                if kind not in ITEM_KINDS:
                    kind = "daily"
                conn.execute(
                    """INSERT INTO plan_items (id, user_id, goal_id, date, content, kind, source, done, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'ai', 0, ?)""",
                    (uuid.uuid4().hex[:12], user_id, goal["id"], day, content, kind, _now()),
                )
        conn.commit()

    if tasks.run_task("daily_plan", f"{user_id}:{day}", _job) == "failed":
        raise HTTPException(status_code=500, detail="计划生成失败，请稍后重试")
    cache.client.delete(f"plan_generating:{user_id}:{day}")
    return {"status": "generated"}


# ---------- 条目 CRUD ----------

def _valid_day(day: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day or ""):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
    return day


def add_item(
    user_id: str,
    content: str,
    day: str | None = None,
    goal_id: str | None = None,
    kind: str = "task",
) -> dict:
    """自定义条目（source='custom'）：goal_id 可空，无目标也能用。"""
    conn = get_conn()
    _require_user(conn, user_id)
    content = (content or "").strip()[:100]
    if not content:
        raise HTTPException(status_code=400, detail="内容不能为空")
    if kind not in ITEM_KINDS:
        raise HTTPException(status_code=400, detail="kind 只能是 habit/daily/task")
    day = _valid_day(day) if day else date.today().isoformat()
    if goal_id:
        goal = conn.execute("SELECT user_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if goal is None:
            raise HTTPException(status_code=404, detail="目标不存在")
        if goal["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="只能关联自己的目标")
    item_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO plan_items (id, user_id, goal_id, date, content, kind, source, done, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'custom', 0, ?)""",
        (item_id, user_id, goal_id or None, day, content, kind, _now()),
    )
    conn.commit()
    return {"id": item_id, "status": "created"}


def update_item(
    item_id: str, user_id: str, content: str | None = None, done: bool | None = None
) -> dict:
    """改内容 / 打勾（仅 owner）；AI 条目无特殊地位，同样可改可勾。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM plan_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="只能改自己的条目")
    if content is not None:
        content = content.strip()[:100]
        if not content:
            raise HTTPException(status_code=400, detail="内容不能为空")
        conn.execute("UPDATE plan_items SET content = ? WHERE id = ?", (content, item_id))
    if done is not None:
        conn.execute("UPDATE plan_items SET done = ? WHERE id = ?", (1 if done else 0, item_id))
    conn.commit()
    return {"id": item_id, "status": "updated"}


def delete_item(item_id: str, user_id: str) -> dict:
    """删除条目（仅 owner）。"""
    conn = get_conn()
    row = conn.execute("SELECT user_id FROM plan_items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="只能删自己的条目")
    conn.execute("DELETE FROM plan_items WHERE id = ?", (item_id,))
    conn.commit()
    return {"id": item_id, "status": "deleted"}
