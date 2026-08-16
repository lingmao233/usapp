"""目标系统：账号级个人目标（减肥/存款/学习/自定义），可选公开到指定圈子接受鞭策。

- 周期框架（热量预算/月预算）建目标时由 rules 纯函数算出落库，不经 AI
- 可见性三粒度：私有 / 圈友进度摘要 / 圈友含明细，过滤全在服务端（viewer_level）
- 存款目标读取时做月底结算懒检查：last_settled_month 游标 + 滚雪球重算（rules）+ AI 文案
"""
import json
import uuid
from datetime import date, datetime

from fastapi import HTTPException

from .. import ai
from ..db.database import get_conn
from . import rules

GOAL_TYPES = ("weight_loss", "savings", "study", "custom")
DETAIL_LEVELS = ("summary", "detail")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _require_user(conn, user_id: str) -> None:
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="用户不存在")


def _month_add(month: str, n: int) -> str:
    """'YYYY-MM' 加 n 个月。"""
    total = int(month[:4]) * 12 + (int(month[5:7]) - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _check_circles(conn, user_id: str, circle_ids: list[str]) -> list[str]:
    """可见圈子白名单校验：只能公开到自己所在的圈子；去重保序。"""
    seen: list[str] = []
    for cid in circle_ids:
        if not isinstance(cid, str) or not cid:
            raise HTTPException(status_code=400, detail="圈子 id 不合法")
        if cid in seen:
            continue
        if conn.execute(
            "SELECT 1 FROM users WHERE id = ? AND circle_id = ?", (user_id, cid)
        ).fetchone() is None:
            raise HTTPException(status_code=400, detail="只能公开到自己所在的圈子")
        seen.append(cid)
    return seen


def _compute_framework(conn, goal_type: str, params: dict, answers: dict, user_id: str) -> dict:
    """周期框架：确定性规则计算（不经 AI）。study 直传每日时长给 AI 用，custom 无框架。"""
    if goal_type == "weight_loss":
        return rules.daily_calorie_budget(answers, params)
    if goal_type == "savings":
        # 消费习惯画像：读取时 SQL 聚合近 90 天已入账支出（决策 8：不动 nightly 蒸馏）
        rows = conn.execute(
            """SELECT amount_fen, category, spent_at FROM expenses
               WHERE user_id = ? AND status = 'confirmed' AND amount_fen > 0
               AND spent_at >= date('now', '-90 days')""",
            (user_id,),
        ).fetchall()
        profile = rules.spending_profile([dict(r) for r in rows]) if rows else None
        return rules.savings_monthly_plan(params, answers, profile)
    if goal_type == "study":
        try:
            minutes = int(float(answers.get("daily_minutes")))
            if minutes > 0:
                return {"daily_minutes": minutes}
        except (TypeError, ValueError):
            pass
    return {}


def create_goal(
    user_id: str,
    goal_type: str,
    title: str,
    params: dict | None = None,
    answers: dict | None = None,
    visible_circle_ids: list[str] | None = None,
    detail_level: str = "summary",
) -> dict:
    """建目标：按类型算周期框架落库；问卷可全跳过（rules 走默认值并标 estimated）。"""
    conn = get_conn()
    _require_user(conn, user_id)
    if goal_type not in GOAL_TYPES:
        raise HTTPException(status_code=400, detail="目标类型只支持 weight_loss/savings/study/custom")
    title = (title or "").strip()[:50]
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    if detail_level not in DETAIL_LEVELS:
        raise HTTPException(status_code=400, detail="detail_level 只能是 summary 或 detail")
    params = params or {}
    answers = answers or {}
    circles = _check_circles(conn, user_id, visible_circle_ids or [])
    framework = _compute_framework(conn, goal_type, params, answers, user_id)
    goal_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO goals (id, user_id, type, title, params, answers, framework, status,
           visible_circle_ids, detail_level, nudge_enabled, last_settled_month, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1, '', ?)""",
        (
            goal_id,
            user_id,
            goal_type,
            title,
            json.dumps(params, ensure_ascii=False),
            json.dumps(answers, ensure_ascii=False),
            json.dumps(framework, ensure_ascii=False),
            json.dumps(circles, ensure_ascii=False),
            detail_level,
            _now(),
        ),
    )
    conn.commit()
    return {"id": goal_id, "status": "created", "framework": framework}


# ---------- 读取与可见性 ----------

def _progress(conn, goal) -> dict:
    """进度摘要：完成百分比、连续全勤天数、今日完成 x/y；存款目标用金额口径覆盖百分比。"""
    rows = conn.execute(
        "SELECT date, done FROM plan_items WHERE goal_id = ?", (goal["id"],)
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
    if goal["type"] == "savings":
        params = json.loads(goal["params"] or "{}")
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


def _to_dict(conn, row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d.get("params") or "{}")
    d["answers"] = json.loads(d.get("answers") or "{}")
    d["framework"] = json.loads(d.get("framework") or "{}")
    d["visible_circle_ids"] = json.loads(d.get("visible_circle_ids") or "[]")
    d["nudge_enabled"] = bool(d["nudge_enabled"])
    d["progress"] = _progress(conn, row)
    return d


_SUMMARY_KEYS = (
    "id", "type", "title", "status", "created_at",
    "detail_level", "nudge_enabled", "owner_nickname", "progress",
)


def _trim_summary(d: dict) -> dict:
    """圈友 summary 粒度：只留进度摘要，裁掉体重/金额/问卷等明细字段。"""
    return {k: d[k] for k in _SUMMARY_KEYS if k in d}


def viewer_level(goal: dict, viewer_id: str | None) -> str | None:
    """viewer 对目标的可见级别：owner / circle / None（不可见）。

    圈友可见 = 目标公开的圈子里，存在一个圈子 viewer 与 owner 同为 membership。
    nudges 模块复用本判定做鞭策入口校验。
    """
    if not viewer_id:
        return None
    if viewer_id == goal["user_id"]:
        return "owner"
    circle_ids = goal.get("visible_circle_ids") or []
    if isinstance(circle_ids, str):
        circle_ids = json.loads(circle_ids or "[]")
    if not circle_ids:
        return None
    marks = ",".join("?" * len(circle_ids))
    row = get_conn().execute(
        f"""SELECT 1 FROM users o JOIN users v ON v.circle_id = o.circle_id
            WHERE o.id = ? AND v.id = ? AND o.circle_id IN ({marks}) LIMIT 1""",
        [goal["user_id"], viewer_id, *circle_ids],
    ).fetchone()
    return "circle" if row else None


def _maybe_settle(conn, goal) -> None:
    """存款目标月底结算懒检查：游标之后每完整过一个自然月结算一次（滚雪球重算）。

    实际存入 = 固定收入(问卷) + 账本额外收入(负数账目) − 账本支出；数字全走 rules，
    文案走 ai.generate_savings_advice，二者落库 framework 后推进游标。
    """
    if goal["type"] != "savings" or goal["status"] != "active":
        return
    cursor = goal["last_settled_month"] or (goal["created_at"] or "")[:7]
    current = date.today().isoformat()[:7]
    if not cursor or cursor >= current:
        return
    answers = json.loads(goal["answers"] or "{}")
    params = json.loads(goal["params"] or "{}")
    framework = json.loads(goal["framework"] or "{}")
    try:
        fixed_income = int(float(answers.get("fixed_income_fen") or 0))
    except (TypeError, ValueError):
        fixed_income = 0
    month = cursor
    while month < current:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE WHEN amount_fen > 0 THEN amount_fen ELSE 0 END), 0) AS spent,
                      COALESCE(SUM(CASE WHEN amount_fen < 0 THEN -amount_fen ELSE 0 END), 0) AS extra
               FROM expenses
               WHERE user_id = ? AND status = 'confirmed' AND substr(spent_at, 1, 7) = ?""",
            (goal["user_id"], month),
        ).fetchone()
        actual = fixed_income + int(row["extra"]) - int(row["spent"])
        settlement = rules.savings_settlement({"params": params}, actual)
        params["saved_fen"] = settlement["saved_fen"]
        advice = ai.generate_savings_advice({
            **settlement,
            "month": month,
            "target_saved_fen": int(framework.get("monthly_save_fen") or 0),
            "actual_saved_fen": actual,
        })
        framework["settlement"] = {
            **settlement, "month": month, "actual_saved_fen": actual, "advice": advice,
        }
        month = _month_add(month, 1)
    status = "done" if (framework.get("settlement") or {}).get("done") else goal["status"]
    conn.execute(
        "UPDATE goals SET params = ?, framework = ?, last_settled_month = ?, status = ? WHERE id = ?",
        (
            json.dumps(params, ensure_ascii=False),
            json.dumps(framework, ensure_ascii=False),
            current,
            status,
            goal["id"],
        ),
    )
    conn.commit()


def get_goal(goal_id: str, viewer_id: str | None) -> dict:
    """目标详情：owner 全量；圈友校验共同圈子 + 按 detail_level 裁剪；其余 404（不泄露存在性）。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    _maybe_settle(conn, row)  # 存款目标读取时懒结算，结算后重读
    row = conn.execute(
        """SELECT g.*, u.nickname AS owner_nickname FROM goals g
           LEFT JOIN users u ON u.id = g.user_id WHERE g.id = ?""",
        (goal_id,),
    ).fetchone()
    level = viewer_level(dict(row), viewer_id)
    if level is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    d = _to_dict(conn, row)
    if level == "owner":
        return d
    if row["detail_level"] == "summary":
        d = _trim_summary(d)
    # 圈友视角附带鞭策入口状态（前端当日已用置灰）；不暴露是否被屏蔽
    d["viewer_nudged_today"] = conn.execute(
        "SELECT 1 FROM nudges WHERE from_user_id = ? AND to_user_id = ? AND substr(created_at, 1, 10) = ?",
        (viewer_id, row["user_id"], date.today().isoformat()),
    ).fetchone() is not None
    return d


def list_goals(user_id: str) -> dict:
    """我的目标列表（owner 视角全量）；存款目标顺带做月底结算懒检查。"""
    conn = get_conn()
    _require_user(conn, user_id)
    rows = conn.execute(
        "SELECT * FROM goals WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    for row in rows:
        _maybe_settle(conn, row)
    rows = conn.execute(
        "SELECT * FROM goals WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    return {"goals": [_to_dict(conn, r) for r in rows]}


def list_circle_goals(circle_id: str, viewer_id: str) -> dict:
    """Wall「伙伴目标」：本圈内公开的目标（owner 与 viewer 均须为本圈成员），恒按 summary 粒度。"""
    conn = get_conn()
    if conn.execute(
        "SELECT 1 FROM users WHERE id = ? AND circle_id = ?", (viewer_id, circle_id)
    ).fetchone() is None:
        raise HTTPException(status_code=403, detail="你不是这个圈子的成员")
    rows = conn.execute(
        """SELECT g.*, u.nickname AS owner_nickname FROM goals g
           JOIN users u ON u.id = g.user_id AND u.circle_id = ?
           WHERE g.status = 'active' ORDER BY g.created_at DESC""",
        (circle_id,),
    ).fetchall()
    out = []
    for row in rows:
        if circle_id not in json.loads(row["visible_circle_ids"] or "[]"):
            continue
        out.append(_trim_summary(_to_dict(conn, row)))
    return {"goals": out}


# ---------- 可见性设置与屏蔽 ----------

def update_sharing(
    goal_id: str, user_id: str, visible_circle_ids: list[str], detail_level: str
) -> dict:
    """更新可见性（受众圈子 + 粒度）：仅 owner；转回私有（空列表）后鞭策入口自然消失。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="只能改自己的目标")
    if detail_level not in DETAIL_LEVELS:
        raise HTTPException(status_code=400, detail="detail_level 只能是 summary 或 detail")
    circles = _check_circles(conn, user_id, visible_circle_ids)
    conn.execute(
        "UPDATE goals SET visible_circle_ids = ?, detail_level = ? WHERE id = ?",
        (json.dumps(circles, ensure_ascii=False), detail_level, goal_id),
    )
    conn.commit()
    return {"id": goal_id, "visible_circle_ids": circles, "detail_level": detail_level}


def set_nudge_enabled(goal_id: str, user_id: str, enabled: bool) -> dict:
    """按目标关闭/打开鞭策：仅 owner。"""
    conn = get_conn()
    row = conn.execute("SELECT user_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="只能改自己的目标")
    conn.execute(
        "UPDATE goals SET nudge_enabled = ? WHERE id = ?", (1 if enabled else 0, goal_id)
    )
    conn.commit()
    return {"id": goal_id, "nudge_enabled": bool(enabled)}


def block_user(user_id: str, blocked_user_id: str) -> dict:
    """按人屏蔽鞭策（nudge_blocks）：幂等，重复屏蔽不插重复行。"""
    conn = get_conn()
    _require_user(conn, user_id)
    blocked_user_id = (blocked_user_id or "").strip()
    if not blocked_user_id or blocked_user_id == user_id:
        raise HTTPException(status_code=400, detail="屏蔽对象不合法")
    if conn.execute(
        "SELECT 1 FROM nudge_blocks WHERE user_id = ? AND blocked_user_id = ?",
        (user_id, blocked_user_id),
    ).fetchone() is None:
        conn.execute(
            "INSERT INTO nudge_blocks (id, user_id, blocked_user_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], user_id, blocked_user_id, _now()),
        )
        conn.commit()
    return {"status": "blocked", "blocked_user_id": blocked_user_id}
