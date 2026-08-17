"""目标系统：账号级个人目标（减肥/存款/学习/自定义），跨圈唯一一份，挂在 account_id 上。

- 周期框架（热量预算/月预算）建目标时由 rules 纯函数算出落库，不经 AI
- 可见性由 self_sharing（类别 × 圈子开关，level=progress/detail）驱动，过滤全在服务端：
  owner 全量 / 圈友按档位裁剪 / 未共享 404（不泄露存在性）
- 存款目标读取时做月底结算懒检查：last_settled_month 游标 + 滚雪球重算（rules）+ AI 文案
"""
import json
import uuid
from datetime import date, datetime

from fastapi import HTTPException

from .. import ai
from ..db.database import get_conn
from . import rules, selfshare

GOAL_TYPES = ("weight_loss", "savings", "study", "custom")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _month_add(month: str, n: int) -> str:
    """'YYYY-MM' 加 n 个月。"""
    total = int(month[:4]) * 12 + (int(month[5:7]) - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _compute_framework(conn, goal_type: str, params: dict, answers: dict, account_id: str) -> dict:
    """周期框架：确定性规则计算（不经 AI）。study 直传每日时长给 AI 用，custom 无框架。"""
    if goal_type == "weight_loss":
        return rules.daily_calorie_budget(answers, params)
    if goal_type == "savings":
        # 消费习惯画像：读取时 SQL 聚合近 90 天已入账支出（决策 8：不动 nightly 蒸馏）
        rows = conn.execute(
            """SELECT amount_fen, category, spent_at FROM expenses
               WHERE account_id = ? AND status = 'confirmed' AND amount_fen > 0
               AND spent_at >= date('now', '-90 days')""",
            (account_id,),
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
    account_id: str,
    goal_type: str,
    title: str,
    params: dict | None = None,
    answers: dict | None = None,
) -> dict:
    """建目标：按类型算周期框架落库；问卷可全跳过（rules 走默认值并标 estimated）。
    共享走 /api/self/sharing（类别 × 圈子开关），建目标不再带可见性参数。"""
    conn = get_conn()
    selfshare.require_account(conn, account_id)
    if goal_type not in GOAL_TYPES:
        raise HTTPException(status_code=400, detail="目标类型只支持 weight_loss/savings/study/custom")
    title = (title or "").strip()[:50]
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    params = params or {}
    answers = answers or {}
    framework = _compute_framework(conn, goal_type, params, answers, account_id)
    goal_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO goals (id, account_id, type, title, params, answers, framework, status,
           nudge_enabled, last_settled_month, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 1, '', ?)""",
        (
            goal_id,
            account_id,
            goal_type,
            title,
            json.dumps(params, ensure_ascii=False),
            json.dumps(answers, ensure_ascii=False),
            json.dumps(framework, ensure_ascii=False),
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
    d["nudge_enabled"] = bool(d["nudge_enabled"])
    d["progress"] = _progress(conn, row)
    return d


_SUMMARY_KEYS = (
    "id", "type", "title", "status", "created_at",
    "nudge_enabled", "owner_nickname", "progress",
)


def _trim_summary(d: dict) -> dict:
    """圈友 progress 粒度：只留进度摘要，裁掉体重/金额/问卷等明细字段。"""
    return {k: d[k] for k in _SUMMARY_KEYS if k in d}


def _circle_nickname(conn, account_id: str, circle_id: str) -> str | None:
    """某账号在指定圈子里的圈内昵称（无 membership 返回 None）。"""
    row = conn.execute(
        """SELECT u.nickname FROM memberships m JOIN users u ON u.id = m.user_id
           WHERE m.account_id = ? AND m.circle_id = ?""",
        (account_id, circle_id),
    ).fetchone()
    return row["nickname"] if row else None


def _shared_circle_with(conn, owner_account_id: str, viewer_account_id: str, category: str) -> str | None:
    """owner 共享该类别的圈子里，任选一个 viewer 也是成员的圈子 id。"""
    row = conn.execute(
        """SELECT s.circle_id FROM self_sharing s
           JOIN memberships m ON m.circle_id = s.circle_id AND m.account_id = ?
           WHERE s.account_id = ? AND s.category = ? LIMIT 1""",
        (viewer_account_id, owner_account_id, category),
    ).fetchone()
    return row["circle_id"] if row else None


def viewer_level(goal: dict, viewer_account_id: str | None) -> str | None:
    """viewer 对目标的可见级别：owner / circle / None（不可见）。

    圈友可见 = owner 把 goal 类别共享到的圈子里，存在一个圈子 viewer 也是成员
    （selfshare.share_level）。nudges 模块复用本判定做鞭策入口校验。
    """
    if not viewer_account_id:
        return None
    if viewer_account_id == goal["account_id"]:
        return "owner"
    level = selfshare.share_level(get_conn(), goal["account_id"], viewer_account_id, "goal")
    return "circle" if level else None


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
               WHERE account_id = ? AND status = 'confirmed' AND substr(spent_at, 1, 7) = ?""",
            (goal["account_id"], month),
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


def get_goal(goal_id: str, viewer_account_id: str | None) -> dict:
    """目标详情：owner 全量；圈友按 self_sharing 档位裁剪（progress 裁明细）；其余 404。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    _maybe_settle(conn, row)  # 存款目标读取时懒结算，结算后重读
    row = conn.execute(
        """SELECT g.*, a.nickname AS owner_nickname FROM goals g
           LEFT JOIN accounts a ON a.id = g.account_id WHERE g.id = ?""",
        (goal_id,),
    ).fetchone()
    level = viewer_level(dict(row), viewer_account_id)
    if level is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    d = _to_dict(conn, row)
    if level == "owner":
        return d
    share = selfshare.share_level(conn, row["account_id"], viewer_account_id, "goal")
    # 圈友视角的 owner_nickname 用圈内昵称（圈语境），退账号昵称
    shared_circle = _shared_circle_with(conn, row["account_id"], viewer_account_id, "goal")
    if shared_circle:
        nick = _circle_nickname(conn, row["account_id"], shared_circle)
        if nick:
            d["owner_nickname"] = nick
    d["share_level"] = share
    if share != "detail":
        d = _trim_summary(d)
        d["share_level"] = share
    # 圈友视角附带鞭策入口状态（前端当日已用置灰）；不暴露是否被屏蔽
    d["viewer_nudged_today"] = conn.execute(
        """SELECT 1 FROM nudges WHERE from_account_id = ? AND to_account_id = ?
           AND substr(created_at, 1, 10) = ?""",
        (viewer_account_id, row["account_id"], date.today().isoformat()),
    ).fetchone() is not None
    return d


def list_goals(account_id: str) -> dict:
    """我的目标列表（owner 视角全量）；存款目标顺带做月底结算懒检查。"""
    conn = get_conn()
    selfshare.require_account(conn, account_id)
    rows = conn.execute(
        "SELECT * FROM goals WHERE account_id = ? ORDER BY created_at DESC", (account_id,)
    ).fetchall()
    for row in rows:
        _maybe_settle(conn, row)
    rows = conn.execute(
        "SELECT * FROM goals WHERE account_id = ? ORDER BY created_at DESC", (account_id,)
    ).fetchall()
    return {"goals": [_to_dict(conn, r) for r in rows]}


def list_circle_goals(circle_id: str, viewer_account_id: str) -> dict:
    """本圈内共享出来的目标列表：viewer 须为本圈成员；按 self_sharing 档位裁剪。

    朋友任务聚合接口（/api/circles/{id}/friend-tasks）之外的轻量列表形态，同一份过滤逻辑。
    """
    conn = get_conn()
    if conn.execute(
        "SELECT 1 FROM memberships WHERE account_id = ? AND circle_id = ?",
        (viewer_account_id, circle_id),
    ).fetchone() is None:
        raise HTTPException(status_code=403, detail="你不是这个圈子的成员")
    rows = conn.execute(
        """SELECT g.*, COALESCE(u.nickname, a.nickname) AS owner_nickname, s.level AS share_level
           FROM goals g
           JOIN self_sharing s
             ON s.account_id = g.account_id AND s.category = 'goal' AND s.circle_id = ?
           LEFT JOIN memberships m ON m.account_id = g.account_id AND m.circle_id = s.circle_id
           LEFT JOIN users u ON u.id = m.user_id
           LEFT JOIN accounts a ON a.id = g.account_id
           WHERE g.status = 'active' ORDER BY g.created_at DESC""",
        (circle_id,),
    ).fetchall()
    out = []
    for row in rows:
        d = _to_dict(conn, row)
        if row["share_level"] != "detail":
            d = _trim_summary(d)
        d["share_level"] = row["share_level"]
        out.append(d)
    return {"goals": out}


# ---------- 鞭策开关与屏蔽（归属校验全部在 account 级） ----------

def set_nudge_enabled(goal_id: str, account_id: str, enabled: bool) -> dict:
    """按目标关闭/打开鞭策：仅 owner。"""
    conn = get_conn()
    row = conn.execute("SELECT account_id FROM goals WHERE id = ?", (goal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="目标不存在")
    if row["account_id"] != account_id:
        raise HTTPException(status_code=403, detail="只能改自己的目标")
    conn.execute(
        "UPDATE goals SET nudge_enabled = ? WHERE id = ?", (1 if enabled else 0, goal_id)
    )
    conn.commit()
    return {"id": goal_id, "nudge_enabled": bool(enabled)}


def block_user(account_id: str, blocked_account_id: str) -> dict:
    """按人屏蔽鞭策（nudge_blocks，account 级）：幂等，重复屏蔽不插重复行。"""
    conn = get_conn()
    selfshare.require_account(conn, account_id)
    blocked_account_id = (blocked_account_id or "").strip()
    if not blocked_account_id or blocked_account_id == account_id:
        raise HTTPException(status_code=400, detail="屏蔽对象不合法")
    if conn.execute(
        "SELECT 1 FROM nudge_blocks WHERE account_id = ? AND blocked_account_id = ?",
        (account_id, blocked_account_id),
    ).fetchone() is None:
        conn.execute(
            "INSERT INTO nudge_blocks (id, account_id, blocked_account_id, created_at)"
            " VALUES (?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], account_id, blocked_account_id, _now()),
        )
        conn.commit()
    return {"status": "blocked", "blocked_account_id": blocked_account_id}
