"""愿望：自动识别 + 手动添加 + 共同愿望匹配 + 行动方案。"""
import hashlib
import json
import logging
import uuid
from datetime import datetime
from itertools import combinations

from fastapi import HTTPException

from .. import ai
from ..config import settings
from ..db import cache
from ..db.database import cosine, decode_embedding, encode_embedding, get_conn
from . import amap, tasks

logger = logging.getLogger("us.wishes")

COMMON_THRESHOLD = 0.7


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["matched_users"] = json.loads(d.get("matched_users") or "[]")
    d["plan"] = json.loads(d["plan"]) if d.get("plan") else None
    d.pop("embedding", None)
    return d


def add_wish(
    circle_id: str,
    user_id: str,
    content: str,
    visibility: str = "public",
    image_url: str | None = None,
) -> dict:
    """手动加愿望：校验后立刻入库返回（分类默认"想做"、向量留空）；
    AI 分类与向量化由 process_wish 后台完成（与碎片管线同模式，提交秒回）。"""
    if visibility not in ("public", "private"):
        raise HTTPException(status_code=400, detail="visibility 只能是 public 或 private")
    content = content.strip()
    image_url = (image_url or "").strip()
    # 配图只接受本站上传地址，防任意 URL
    if image_url and not image_url.startswith("/api/uploads/"):
        raise HTTPException(status_code=400, detail="image_url 只能是本站上传地址")
    if not content and not image_url:
        raise HTTPException(status_code=400, detail="内容和图片至少有一个")
    conn = get_conn()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ? AND circle_id = ?", (user_id, circle_id)
    ).fetchone()
    if user is None:
        raise HTTPException(status_code=403, detail="你不是这个圈子的成员")
    wish_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO wishes
           (id, user_id, circle_id, content, category, fragment_id, status,
            matched_users, embedding, plan, created_at, visibility, image_url)
           VALUES (?, ?, ?, ?, 'do', '', 'active', '[]', NULL, NULL, ?, ?, ?)""",
        (wish_id, user_id, circle_id, content, _now(), visibility, image_url or None),
    )
    conn.commit()
    return {"id": wish_id, "status": "created"}


def process_wish(wish_id: str) -> None:
    """手动愿望的异步处理：AI 分类（想吃/想去…）+ 文本向量。

    向量就绪前该愿望不参与共同愿望匹配与"一起去"参与人计算（NULL 天然跳过）；
    经任务层：失败重试 + degraded 落库，重试对已处理行幂等跳过。
    """

    def _job() -> None:
        conn = get_conn()
        row = conn.execute("SELECT * FROM wishes WHERE id = ?", (wish_id,)).fetchone()
        if row is None or row["embedding"] is not None:
            return
        # 纯图片愿望：分类/embedding 的文本输入用占位词，库里存原始 content（可空）
        ai_text = row["content"] or "[图片]"
        classification = ai.classify_fragment(ai_text)
        vec = ai.embed_text(ai_text)
        conn.execute(
            "UPDATE wishes SET category=?, embedding=? WHERE id=?",
            (classification["wish_category"] or "do", encode_embedding(vec), wish_id),
        )
        conn.commit()

    tasks.run_task("wish_pipeline", wish_id, _job)


def list_wishes(circle_id: str, user_id: str | None = None, status: str | None = None) -> dict:
    conn = get_conn()
    # 可见性直接读 w.visibility 列（发布时已从来源碎片同步）：公开 OR 本人的
    sql = """SELECT w.*, u.nickname AS user_nickname FROM wishes w
             JOIN users u ON u.id = w.user_id
             WHERE w.circle_id = ?
             AND (w.visibility = 'public' OR w.user_id = ?)"""
    params: list = [circle_id, user_id or ""]
    if status:
        sql += " AND w.status = ?"
        params.append(status)
    sql += " ORDER BY w.created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return {"wishes": [_row_to_dict(r) for r in rows]}


def delete_wish(wish_id: str, user_id: str) -> dict:
    """删除愿望（仅作者本人）：单行删除。共同愿望分量变化由调用方打 dirty 留给 nightly。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM wishes WHERE id = ?", (wish_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="愿望不存在")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="只能删除自己的愿望")
    conn.execute("DELETE FROM wishes WHERE id = ?", (wish_id,))
    conn.commit()
    return {"id": wish_id, "status": "deleted", "circle_id": row["circle_id"]}


def set_wish_done(wish_id: str, user_id: str, done: bool) -> dict:
    """勾选完成/取消完成（仅作者本人）：完成的愿望移出共同愿望匹配池（status='done'），
    取消勾选回到 active 重新参与匹配。与方案是否生成无关。"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM wishes WHERE id = ?", (wish_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="愿望不存在")
    if row["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="只能操作自己的愿望")
    status = "done" if done else "active"
    conn.execute("UPDATE wishes SET status = ? WHERE id = ?", (status, wish_id))
    conn.commit()
    return {"id": wish_id, "status": status, "circle_id": row["circle_id"]}


def common_wishes(circle_id: str) -> dict:
    """共同愿望匹配（圈级缓存 + stale-while-revalidate）。

    指纹 = 圈内全部愿望的 id/状态/向量就绪标记的哈希——增删愿望、勾选完成、
    向量就绪都会改变指纹，缓存因此自失效，无需在写路径手动清。

    指纹一致直接返回缓存；不一致时**旧结果照旧返回**（refreshing=True），重算
    交给后台（路由层按 "trigger" 放 BackgroundTasks），请求不再阻塞在 LLM 上。
    """
    conn = get_conn()
    fp = _pool_fingerprint(conn, circle_id)
    row = conn.execute(
        "SELECT fingerprint, result FROM common_wishes_cache WHERE circle_id = ?",
        (circle_id,),
    ).fetchone()
    if row and row["fingerprint"] == fp:
        return {"common_wishes": json.loads(row["result"]), "refreshing": False}
    # 旗标防抖：一次重算进行中的重复请求只返回陈旧结果，不重复触发
    flag = f"common_wishes_refreshing:{circle_id}"
    refreshing: bool | str = True
    if not cache.client.get(flag):
        cache.client.set(flag, "1", ex=120)
        refreshing = "trigger"
    stale = json.loads(row["result"]) if row else []
    return {"common_wishes": stale, "refreshing": refreshing}


def refresh_common_wishes(circle_id: str) -> None:
    """后台重算共同愿望并写缓存（stale-while-revalidate 的 revalidate 半）。

    失败保留旧缓存（task_runs 已记失败），下次请求重新触发；
    完成后清旗标，后续指纹变化可立即再触发。
    """
    flag = f"common_wishes_refreshing:{circle_id}"
    results: list[dict] = []

    def _job() -> None:
        nonlocal results
        results = compute_common_wishes(circle_id)

    if tasks.run_task("common_wishes", circle_id, _job) == "failed":
        cache.client.delete(flag)
        return
    conn = get_conn()
    conn.execute(
        """INSERT INTO common_wishes_cache (circle_id, fingerprint, result, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(circle_id) DO UPDATE SET fingerprint=excluded.fingerprint,
               result=excluded.result, updated_at=excluded.updated_at""",
        (circle_id, _pool_fingerprint(conn, circle_id), json.dumps(results, ensure_ascii=False), _now()),
    )
    conn.commit()
    cache.client.delete(flag)


def _pool_fingerprint(conn, circle_id: str) -> str:
    """匹配池指纹：愿望集合（id + status + 向量是否就绪）的哈希。"""
    rows = conn.execute(
        "SELECT id, status, embedding IS NOT NULL AS has_vec FROM wishes"
        " WHERE circle_id = ? ORDER BY id",
        (circle_id,),
    ).fetchall()
    h = hashlib.md5()
    for r in rows:
        h.update(f"{r['id']}:{r['status']}:{r['has_vec']};".encode())
    return h.hexdigest()


def compute_common_wishes(circle_id: str, include_private: bool = False) -> list[dict]:
    """embedding 粗筛（跨用户、≥0.7）+ LLM 按 PRD 6.3 确认；未配置/失败只用相似度（记 degraded）。

    返回结果列表；记忆层按 wish_ids 归属统计用户对共同愿望数。
    默认只匹配公开愿望（隐私愿望不触发对他人可见的匹配提示）；
    include_private=True 仅供记忆层算分（算分对称、展示不对称），对外端点不得使用。
    """
    conn = get_conn()
    sql = """SELECT w.*, u.nickname AS user_nickname FROM wishes w
             JOIN users u ON u.id = w.user_id
             WHERE w.circle_id = ? AND w.status = 'active' AND w.embedding IS NOT NULL"""
    if not include_private:
        sql += " AND w.visibility = 'public'"
    rows = conn.execute(sql, (circle_id,)).fetchall()

    # 1) embedding 粗筛：跨用户相似对聚成簇
    clusters: list[list[dict]] = []
    for row in rows:
        vec = decode_embedding(row["embedding"])
        if vec is None:
            continue
        placed = False
        for cluster in clusters:
            for member in cluster:
                if member["user_id"] == row["user_id"]:
                    continue
                m_vec = decode_embedding(member["_vec_blob"])
                if m_vec is not None and cosine(vec, m_vec) >= COMMON_THRESHOLD:
                    cluster.append({**dict(row), "_vec_blob": row["embedding"]})
                    placed = True
                    break
            if placed:
                break
        if not placed:
            clusters.append([{**dict(row), "_vec_blob": row["embedding"]}])

    candidates = []
    for cluster in clusters:
        user_ids = {m["user_id"] for m in cluster}
        if len(user_ids) < 2:
            continue
        # 每个用户取最早一条
        by_user: dict[str, dict] = {}
        for m in sorted(cluster, key=lambda x: x["created_at"]):
            by_user.setdefault(m["user_id"], m)
        members = list(by_user.values())
        sims = []
        for a, b in combinations(members, 2):
            va = decode_embedding(a["_vec_blob"])
            vb = decode_embedding(b["_vec_blob"])
            if va is not None and vb is not None:
                sims.append(cosine(va, vb))
        candidates.append(
            {
                "content": members[0]["content"],
                "variants": [m["content"] for m in members],
                "matched_users": [m["user_nickname"] for m in members],
                "wish_ids": [m["id"] for m in members],
                "similarity": round(sum(sims) / len(sims), 4) if sims else 0.0,
            }
        )

    # 2) LLM 确认（未配置/失败时 confirm_common_wishes 返回空，直接用相似度结果）
    results = []
    if candidates:
        wishes_repr = "\n".join(
            f"- {c['matched_users']}：{' / '.join(c['variants'])}" for c in candidates
        )
        confirmed = ai.confirm_common_wishes(wishes_repr)
        if confirmed:
            for item in confirmed:
                if float(item.get("confidence", 0)) >= COMMON_THRESHOLD:
                    results.append(
                        {
                            "content": item.get("content", ""),
                            "matched_users": item.get("matched_users", []),
                            "suggestion": item.get("suggestion", ""),
                            "confidence": item.get("confidence", 0),
                            "wish_ids": next(
                                (c["wish_ids"] for c in candidates
                                 if set(c["matched_users"]) & set(item.get("matched_users", []))),
                                [],
                            ),
                        }
                    )
    if not results:
        for c in candidates:
            results.append(
                {
                    "content": c["content"],
                    "matched_users": c["matched_users"],
                    "suggestion": ai.wish_suggestion(c["content"], c["matched_users"]),
                    "confidence": c["similarity"],
                    "wish_ids": c["wish_ids"],
                }
            )

    results.sort(key=lambda x: x["confidence"], reverse=True)
    return results


def get_cached_plan(wish_id: str) -> dict | None:
    """已缓存的方案直接返回；未生成返回 None（调用方转异步生成）。"""
    row = get_conn().execute(
        "SELECT plan FROM wishes WHERE id = ?", (wish_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="愿望不存在")
    if not row["plan"]:
        return None
    return {"plan": json.loads(row["plan"]), "cached": True}


def generate_plan_and_notify(wish_id: str, user_id: str | None) -> None:
    """后台生成方案（经任务层），完成后 Web Push 通知点击者。"""
    from . import push  # 延迟导入：push 与 wishes 无相互依赖，仅此处用到

    def _job() -> None:
        generate_plan(wish_id)

    if tasks.run_task("wish_plan", wish_id, _job) != "failed" and user_id:
        push.notify_plan_ready(wish_id, user_id)


def generate_plan(wish_id: str) -> dict:
    """生成"一起去"行动方案，缓存在 wishes.plan。"""
    conn = get_conn()
    row = conn.execute(
        """SELECT w.*, u.nickname AS user_nickname FROM wishes w
           JOIN users u ON u.id = w.user_id WHERE w.id = ?""",
        (wish_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="愿望不存在")
    if row["plan"]:
        return {"plan": json.loads(row["plan"]), "cached": True}

    # 找到共同愿望里的其他参与人（与共同愿望匹配同规则：只考虑公开愿望，
    # 隐私愿望的作者绝不出现在他人的方案里）
    participants = [row["user_nickname"]]
    vec = decode_embedding(row["embedding"])
    if vec is not None:
        others = conn.execute(
            """SELECT w.*, u.nickname AS user_nickname FROM wishes w
               JOIN users u ON u.id = w.user_id
               WHERE w.circle_id = ? AND w.id != ? AND w.user_id != ? AND w.embedding IS NOT NULL
               AND w.visibility = 'public'""",
            (row["circle_id"], wish_id, row["user_id"]),
        ).fetchall()
        for other in others:
            o_vec = decode_embedding(other["embedding"])
            if o_vec is not None and cosine(vec, o_vec) >= COMMON_THRESHOLD:
                participants.append(other["user_nickname"])
    participants = sorted(set(participants))

    analysis, real_data = _plan_context(row["content"])
    plan = ai.generate_plan(
        row["content"], participants, real_data=real_data, analysis=analysis
    )
    # 只缓存方案，不改状态：愿望是否完成只由用户勾选决定（matched 语义已下线）。
    # participants 一并落库：缓存命中/方案追问时仍拿得到参与人
    conn.execute(
        "UPDATE wishes SET plan = ? WHERE id = ?",
        (json.dumps({**plan, "participants": participants}, ensure_ascii=False), wish_id),
    )
    conn.commit()
    return {"plan": plan, "participants": participants, "cached": False}


def _plan_context(content: str) -> tuple[dict, dict | None]:
    """方案上下文（LLM 愿望分析 + 高德真实数据）。

    先由 LLM 判断愿望类型：围绕真实地点/品类的愿望才查高德（need_real_data）；
    人名/"约人"类愿望不查——把名字当 POI 搜只会得到噪声。real_data 为 None 时
    prompt 走纯经验模式（未配 AMAP_KEY / 分析判定不需要 / 高德查询失败）。
    """
    q = ai.extract_plan_query(content)
    if not settings.AMAP_KEY or not q.get("need_real_data", True) or not q.get("keywords"):
        return q, None
    try:
        return q, amap.gather(q["city"], q["keywords"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("高德真实数据查询失败，回退纯经验方案：%s", exc)
        return q, None
