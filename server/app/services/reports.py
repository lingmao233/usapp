"""每周交集报告：懒触发 + 周内滚动刷新（有新公开内容时重生成）+ 手动强制刷新。"""
import json
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException

from .. import ai
from ..ai.prompts import PERSONAS, resolve_persona
from ..db import cache
from ..db.database import get_conn
from . import memory, tasks

logger = logging.getLogger("us.reports")

# 语录检索上限：进周报 prompt 的真实语录条数与单条长度
QUOTE_LIMIT = 8
QUOTE_MAX_LEN = 50


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _circle_persona(conn, circle_id: str) -> str:
    """圈子人格文本：自定义优先，预设兜底，查不到回退观察员。"""
    row = conn.execute(
        "SELECT persona_preset, persona_custom FROM circles WHERE id = ?", (circle_id,)
    ).fetchone()
    if row is None:
        return PERSONAS["observer"]
    return resolve_persona(row["persona_preset"], row["persona_custom"])


def _collect_quotes(conn, circle_id: str, week_start: str, week_end: str) -> list[str]:
    """语录检索（B1 内容对齐）：圈内公开碎片 + 公开碎片下的评论原句。

    优先互动多的（点赞 + 评论计数），不足的用最近发言补齐，共 ≤ QUOTE_LIMIT 条，
    每条截 QUOTE_MAX_LEN 字。只用公开内容——隐私碎片及其评论永远不进 prompt。
    """
    candidates: list[dict] = []
    for r in conn.execute(
        """SELECT f.content, f.created_at, u.nickname,
                  (SELECT COUNT(*) FROM likes l WHERE l.fragment_id = f.id) AS like_count,
                  (SELECT COUNT(*) FROM comments c WHERE c.fragment_id = f.id) AS comment_count
           FROM fragments f JOIN users u ON u.id = f.user_id
           WHERE f.circle_id = ? AND f.visibility = 'public' AND f.content != ''
           AND date(f.created_at) BETWEEN ? AND ?""",
        (circle_id, week_start, week_end),
    ).fetchall():
        candidates.append(
            {
                "text": f"{r['nickname']}：{r['content'].strip()[:QUOTE_MAX_LEN]}",
                "score": r["like_count"] + r["comment_count"],
                "created_at": r["created_at"],
            }
        )
    for r in conn.execute(
        """SELECT c.content, c.created_at, u.nickname
           FROM comments c
           JOIN fragments f ON f.id = c.fragment_id
           JOIN users u ON u.id = c.author_id
           WHERE c.circle_id = ? AND f.visibility = 'public'
           AND date(c.created_at) BETWEEN ? AND ?""",
        (circle_id, week_start, week_end),
    ).fetchall():
        candidates.append(
            {
                "text": f"{r['nickname']}：{r['content'].strip()[:QUOTE_MAX_LEN]}",
                "score": 0,
                "created_at": r["created_at"],
            }
        )

    picked: list[dict] = []
    seen: set[str] = set()

    def _take(c: dict) -> bool:
        if c["text"] in seen or len(picked) >= QUOTE_LIMIT:
            return False
        seen.add(c["text"])
        picked.append(c)
        return True

    # 互动多的优先入选，再按时间倒序补足
    for c in sorted(candidates, key=lambda x: (-x["score"], x["created_at"])):
        if c["score"] <= 0:
            break
        _take(c)
    for c in sorted(candidates, key=lambda x: x["created_at"], reverse=True):
        if len(picked) >= QUOTE_LIMIT:
            break
        _take(c)
    return [c["text"] for c in picked]


def current_week_range() -> tuple[str, str]:
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def _collect_week_data(circle_id: str, week_start: str, week_end: str) -> dict:
    conn = get_conn()
    # 周报内容来源只用公开碎片（关键连接、标签统计同源）
    rows = conn.execute(
        """SELECT f.*, u.nickname AS user_nickname FROM fragments f
           JOIN users u ON u.id = f.user_id
           WHERE f.circle_id = ? AND f.visibility = 'public'
           AND date(f.created_at) BETWEEN ? AND ?
           ORDER BY f.created_at""",
        (circle_id, week_start, week_end),
    ).fetchall()
    fragments = [dict(r) for r in rows]

    # 关键连接：读 pair_relationships 持久数据（generate_report 已先补跑 dirty），
    # 不再每次 O(n²) 现算；对外只保留公开来源（public-public）的共同主题
    pair_rows = conn.execute(
        """SELECT p.topics, p.semantic, p.interaction, p.common_wishes, p.common_topics,
                  ua.nickname AS a_nick, ub.nickname AS b_nick
           FROM pair_relationships p
           JOIN users ua ON ua.id = p.user_a
           JOIN users ub ON ub.id = p.user_b
           WHERE p.circle_id = ?""",
        (circle_id,),
    ).fetchall()
    scored = sorted(
        pair_rows,
        key=lambda r: memory.compute_pair_score(
            {
                "semantic": r["semantic"],
                "interaction": r["interaction"],
                "common_wishes": r["common_wishes"],
                "common_topics": r["common_topics"],
            }
        ),
        reverse=True,
    )
    connections = []
    for r in scored:
        tags = [t["tag"] for t in json.loads(r["topics"] or "[]") if t.get("source") == "public-public"]
        if not tags:
            continue
        connections.append(
            f"{r['a_nick']} 和 {r['b_nick']} 都关注着「{'」「'.join(tags[:3])}」，要不要聊聊？"
        )
        if len(connections) >= 5:
            break

    tag_count: dict[str, int] = {}
    for f in fragments:
        for t in json.loads(f.get("tags") or "[]"):
            tag_count[t] = tag_count.get(t, 0) + 1
    top_tags = sorted(tag_count, key=tag_count.get, reverse=True)[:5]

    # 愿望动态同样只用公开愿望（可见性直接读 w.visibility 列）
    wish_rows = conn.execute(
        """SELECT w.content, u.nickname FROM wishes w
           JOIN users u ON u.id = w.user_id
           WHERE w.circle_id = ? AND date(w.created_at) BETWEEN ? AND ?
           AND w.visibility = 'public'""",
        (circle_id, week_start, week_end),
    ).fetchall()
    wishes = [f"{w['nickname']} 想：{w['content']}" for w in wish_rows]

    knowledge_count = conn.execute(
        """SELECT COUNT(*) AS c FROM knowledge_items
           WHERE circle_id = ? AND date(created_at) BETWEEN ? AND ?""",
        (circle_id, week_start, week_end),
    ).fetchone()["c"]

    users = sorted({f["user_nickname"] for f in fragments})

    def _fragment_line(f: dict) -> str:
        """素材行：愿望/图片都带明确标记——让 LLM 分清"提到/想/晒"，不给脑补留借口。"""
        text = f["content"]
        if f.get("image_url"):
            cap = (f.get("caption") or "").strip()
            marker = f"[图片] {cap}".strip()
            text = f"{text} {marker}".strip() if text else marker
        if f.get("is_wish"):
            text = f"（愿望）{text}"
        return f"- [{f['created_at'][:10]}] {f['user_nickname']}：{text}"

    fragments_repr = "\n".join(_fragment_line(f) for f in fragments) or "（本周还没有碎片）"

    return {
        "fragments_repr": fragments_repr,
        "quotes": _collect_quotes(conn, circle_id, week_start, week_end),
        "stats": {
            "users": users,
            "top_tags": top_tags,
            "wishes": wishes,
            "knowledge_count": knowledge_count,
            "connections": connections,
        },
        "key_connections": connections,
    }


def generate_report(
    circle_id: str,
    week_start: str | None = None,
    week_end: str | None = None,
    force: bool = False,
) -> dict:
    if not week_start or not week_end:
        week_start, week_end = current_week_range()
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM reports WHERE circle_id = ? AND week_start = ?",
        (circle_id, week_start),
    ).fetchone()
    if existing and not force:
        return {"report_id": existing["id"], "status": "exists"}

    outcome: dict = {}

    def _job() -> None:
        # 周报生成前强制对脏数据补跑一轮蒸馏，key_connections 才有得读
        memory.refresh_dirty(circle_id)
        data = _collect_week_data(circle_id, week_start, week_end)
        content = ai.generate_weekly_report(
            data["fragments_repr"], week_start, week_end, data["stats"],
            persona=_circle_persona(conn, circle_id), quotes=data["quotes"],
        )
        report_id = uuid.uuid4().hex[:12]
        if existing:
            # 周内滚动更新：删掉旧版再写入新版
            conn.execute("DELETE FROM reports WHERE id = ?", (existing["id"],))
        conn.execute(
            """INSERT INTO reports (id, circle_id, week_start, week_end, content, key_connections, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                report_id,
                circle_id,
                week_start,
                week_end,
                content,
                json.dumps(data["key_connections"], ensure_ascii=False),
                _now(),
            ),
        )
        conn.commit()
        outcome.update({"report_id": report_id, "status": "generated"})

    if tasks.run_task("weekly_report", f"{circle_id}:{week_start}", _job) == "failed":
        raise HTTPException(status_code=500, detail="周报生成失败，请稍后重试")
    cache.client.delete(f"report_generating:{circle_id}:{week_start}")
    return outcome


def list_reports(circle_id: str) -> dict:
    """历史报告列表；本周报告缺失时懒触发生成，已有报告但有新公开内容时滚动刷新。"""
    conn = get_conn()
    week_start, week_end = current_week_range()
    current = conn.execute(
        "SELECT id, created_at FROM reports WHERE circle_id = ? AND week_start = ?",
        (circle_id, week_start),
    ).fetchone()
    stale = False
    if current:
        # 报告生成后又有新的公开碎片/愿望 → 视为过期（flag 的 10 分钟窗口天然限频）
        newer = conn.execute(
            """SELECT (SELECT COUNT(*) FROM fragments
                        WHERE circle_id = ? AND visibility = 'public' AND created_at > ?) +
                      (SELECT COUNT(*) FROM wishes
                        WHERE circle_id = ? AND visibility = 'public' AND created_at > ?) AS c""",
            (circle_id, current["created_at"], circle_id, current["created_at"]),
        ).fetchone()["c"]
        stale = newer > 0
    generating = False
    if not current or stale:
        fragment_count = conn.execute(
            "SELECT COUNT(*) AS c FROM fragments WHERE circle_id = ?", (circle_id,)
        ).fetchone()["c"]
        if fragment_count > 0:
            flag = f"report_generating:{circle_id}:{week_start}"
            if not cache.client.get(flag):
                cache.client.set(flag, "1", ex=600)
                generating = "trigger"  # 路由层据此放 BackgroundTasks
            else:
                generating = True

    rows = conn.execute(
        "SELECT id, week_start, week_end, created_at FROM reports WHERE circle_id = ? ORDER BY week_start DESC",
        (circle_id,),
    ).fetchall()
    return {
        "reports": [dict(r) for r in rows],
        "current_week": {"week_start": week_start, "week_end": week_end},
        "generating": generating,
    }


def get_report(report_id: str) -> dict:
    row = get_conn().execute(
        "SELECT * FROM reports WHERE id = ?", (report_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    d = dict(row)
    d["key_connections"] = json.loads(d.get("key_connections") or "[]")
    return d
