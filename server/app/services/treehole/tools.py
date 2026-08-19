"""树洞工具集（tool calling 节点）：自研注册表，全部只读、账号级隔离。

每个工具返回 {"name", "summary", "data"}：summary 是一句人话（直接进回复），
data 是结构化结果（给真实模式 LLM 组织语言用）。新工具加进 TOOLS 注册表即可。
"""
import json
import re
from collections import Counter
from datetime import date

from ...db.database import get_conn
from ..memory import layers


def _account_user_ids(conn, account_id: str) -> list[str]:
    return [
        r["user_id"]
        for r in conn.execute(
            "SELECT user_id FROM memberships WHERE account_id = ?", (account_id,)
        ).fetchall()
    ]


def query_ledger(account_id: str, args: dict) -> dict:
    """本月支出：合计 + 分类 top3 + 笔数（只算已入账的正数支出）。"""
    conn = get_conn()
    month = str((args or {}).get("month") or "").strip()
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        month = ""
    month = month or date.today().isoformat()[:7]
    rows = conn.execute(
        """SELECT amount_fen, category FROM expenses
           WHERE account_id = ? AND status = 'confirmed' AND substr(spent_at, 1, 7) = ?""",
        (account_id, month),
    ).fetchall()
    spend = [r for r in rows if r["amount_fen"] > 0]
    total = sum(r["amount_fen"] for r in spend)
    by_cat: Counter[str] = Counter()
    for r in spend:
        by_cat[r["category"]] += r["amount_fen"]
    top = "、".join(f"{cat} {fen / 100:.2f} 元" for cat, fen in by_cat.most_common(3))
    summary = f"{month} 已入账支出合计 {total / 100:.2f} 元，共 {len(spend)} 笔"
    if top:
        summary += f"；大头是{top}"
    return {"name": "query_ledger", "summary": summary,
            "data": {"month": month, "total_fen": total, "count": len(spend),
                     "by_category": dict(by_cat)}}


def query_today_plan(account_id: str, args: dict) -> dict:
    """今日计划完成度：条目列表 + done/total。"""
    conn = get_conn()
    day = date.today().isoformat()
    rows = conn.execute(
        "SELECT content, kind, done FROM plan_items WHERE account_id = ? AND date = ?"
        " ORDER BY created_at, rowid",
        (account_id, day),
    ).fetchall()
    done = sum(1 for r in rows if r["done"])
    items = [f"{'✓' if r['done'] else '·'} {r['content']}" for r in rows]
    summary = (
        f"今日计划 {done}/{len(rows)} 条完成：" + "；".join(items)
        if rows else "今天还没有计划条目"
    )
    return {"name": "query_today_plan", "summary": summary,
            "data": {"date": day, "done": done, "total": len(rows),
                     "items": [dict(r) for r in rows]}}


def query_calories(account_id: str, args: dict) -> dict:
    """今日热量：已入账条目合计 kcal + 餐数。"""
    conn = get_conn()
    day = date.today().isoformat()
    rows = conn.execute(
        "SELECT total_kcal, note FROM calorie_entries"
        " WHERE account_id = ? AND status = 'confirmed' AND substr(created_at, 1, 10) = ?",
        (account_id, day),
    ).fetchall()
    total = sum(r["total_kcal"] for r in rows)
    summary = (
        f"今天已记录 {len(rows)} 餐，合计约 {total:.0f} kcal"
        if rows else "今天还没有热量记录"
    )
    return {"name": "query_calories", "summary": summary,
            "data": {"date": day, "total_kcal": round(total, 1), "meals": len(rows)}}


def search_fragments(account_id: str, args: dict) -> dict:
    """关键词搜本人碎片（跨圈全部身份，含私密碎片——只服务本人，不涉及展示边界）。"""
    conn = get_conn()
    keyword = str((args or {}).get("keyword") or "").strip()[:30]
    user_ids = _account_user_ids(conn, account_id)
    if not keyword or not user_ids:
        return {"name": "search_fragments", "summary": "没有可搜的关键词或碎片", "data": {"items": []}}
    marks = ",".join("?" * len(user_ids))
    escaped = keyword.replace("%", "\\%").replace("_", "\\_")
    rows = conn.execute(
        f"""SELECT id, content, created_at FROM fragments
            WHERE user_id IN ({marks}) AND content LIKE ? ESCAPE '\\'
            ORDER BY created_at DESC LIMIT 5""",
        (*user_ids, f"%{escaped}%"),
    ).fetchall()
    if not rows:
        summary = f"没找到和「{keyword}」相关的碎片"
    else:
        preview = "；".join(f"「{r['content'][:30]}」" for r in rows)
        summary = f"找到 {len(rows)} 条相关碎片：{preview}"
    return {"name": "search_fragments", "summary": summary,
            "data": {"items": [dict(r) for r in rows]}}


def get_memory_profile(account_id: str, args: dict) -> dict:
    """读记忆画像：L3 聚合画像 + 最近 L1 原子条数。"""
    profile = layers.account_profile(account_id)
    atoms = layers.list_atoms(account_id, limit=5)
    if not profile and not atoms:
        summary = "还没有积累出画像——多聊几次我就更懂你了"
    else:
        topics = "、".join(profile.get("topics") or []) or "暂无"
        summary = f"我记得的你：常聊 {topics}；最近记住 {len(atoms)} 条小事"
    return {"name": "get_memory_profile", "summary": summary,
            "data": {"profile": profile, "recent_atoms": [a["content"] for a in atoms]}}


TOOLS = {
    "query_ledger": {"fn": query_ledger, "desc": "query_ledger(month?)：查本月支出合计与分类"},
    "query_today_plan": {"fn": query_today_plan, "desc": "query_today_plan()：查今日计划完成度"},
    "query_calories": {"fn": query_calories, "desc": "query_calories()：查今日热量摄入"},
    "search_fragments": {"fn": search_fragments, "desc": "search_fragments(keyword)：关键词搜本人历史碎片"},
    "get_memory_profile": {"fn": get_memory_profile, "desc": "get_memory_profile()：读记忆画像（长期偏好/近期记忆）"},
}


def specs_text() -> str:
    """给工具决策 prompt 的工具说明书。"""
    return "\n".join(f"- {t['desc']}" for t in TOOLS.values())


def execute(account_id: str, name: str, args: dict) -> dict:
    """执行一个工具调用；未注册的工具返回空结果（不让 LLM 幻觉工具名搞崩图）。"""
    tool = TOOLS.get(name)
    if tool is None:
        return {"name": name, "summary": "", "data": {}}
    return tool["fn"](account_id, args or {})


def describe_account_data(account_id: str) -> str:  # pragma: no cover - 调试辅助
    return json.dumps({name: execute(account_id, name, {})["data"] for name in TOOLS},
                      ensure_ascii=False)
