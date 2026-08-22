"""记账 + 热量：拍照识别（豆包视觉）→ 待确认 → 确认入账；手动录入兜底。

账号级数据（account_id，跨圈唯一一份）。
- 金额一律 INTEGER 分；识别返回的元在入账时换算；收入记负数账目
  （存款结算口径：实际存入 = 固定收入 + 额外收入 − 支出）
- 热量确认后同步联动：今日累计超今日预算 → 插/更新今日 adjust 计划条目（幂等）；
  没超什么都不发生（已有条目一律不动）
"""
import json
import logging
import re
import statistics
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import HTTPException

from .. import ai
from ..ai.prompts import EXPENSE_CATEGORIES
from ..config import settings
from ..db.database import cosine, decode_embedding, encode_embedding, get_conn

logger = logging.getLogger("us.ledger")

IMAGE_RAG_THRESHOLD = 0.9  # 以图搜图命中阈值（实测：同图 ≈0.999，不同食物 ≈0.42，食物vs人像 ≈0.23）
from . import nutrition, rules, selfshare

MAX_AMOUNT_FEN = 10**12  # 金额 sanity 上限（分，约百亿）
MAX_KCAL = 20000.0  # 单条热量 sanity 上限


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _require_account(conn, account_id: str) -> None:
    selfshare.require_account(conn, account_id)


def _image_path(image_url: str) -> Path:
    """image_url → 本地文件路径：识别优先用 800px 识别副本（{uuid}_s.jpg，更快），
    其次 1600px 展示图（_d.jpg），最后原图。"""
    if not (image_url or "").startswith("/api/uploads/"):
        raise HTTPException(status_code=400, detail="image_url 只能是本站上传地址")
    base = image_url.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    small = settings.upload_dir / f"{stem}_s.jpg"
    display = settings.upload_dir / f"{stem}_d.jpg"
    original = settings.upload_dir / base
    path = small if small.is_file() else (display if display.is_file() else original)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图片不存在")
    return path


def _norm_time(raw) -> str:
    """时间规整为 ISO 前缀（'YYYY-MM-DD' 或 'YYYY-MM-DDTHH:MM'）；非法回空由调用方兜底。"""
    s = str(raw or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::\d{2})?)?$", s)
    if not m:
        return ""
    return m.group(1) + (f"T{m.group(2)}" if m.group(2) else "")


def _yuan_to_fen(raw) -> int:
    """识别返回的元 → 分；非法返回 0（调用方跳过该笔）。"""
    try:
        return int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return 0


def _check_amount(amount_fen) -> int:
    """手动/编辑金额校验：非零整数分，正=支出、负=收入。"""
    if amount_fen is None:
        raise HTTPException(status_code=400, detail="金额必填（单位：分）")
    if not isinstance(amount_fen, int) or isinstance(amount_fen, bool):
        raise HTTPException(status_code=400, detail="金额必须是整数（单位：分）")
    if amount_fen == 0 or abs(amount_fen) > MAX_AMOUNT_FEN:
        raise HTTPException(status_code=400, detail="金额不合法（非零，单位：分）")
    return amount_fen


def _norm_category(raw) -> str:
    """分类限定预设类目（自由分类名会让统计没法做），不认得的归「其他」。"""
    s = str(raw or "").strip()[:10]
    return s if s in EXPENSE_CATEGORIES else "其他"


def _check_kcal(raw) -> float:
    try:
        kcal = round(float(raw), 1)
    except (TypeError, ValueError):
        kcal = 0.0
    if kcal <= 0 or kcal > MAX_KCAL:
        raise HTTPException(status_code=400, detail="热量必填且需在 0-20000 kcal 之间")
    return kcal


# ---------- 记账 ----------

def recognize_expenses(account_id: str, image_url: str) -> dict:
    """小票/支付截图识别 → 一图多笔 pending 行；未配视觉模型抛 400（手动录入兜底）。"""
    conn = get_conn()
    _require_account(conn, account_id)
    result = ai.recognize_receipt(str(_image_path(image_url)))
    if result is None:
        raise HTTPException(status_code=400, detail="未配置视觉模型，请手动录入")
    ids = []
    for raw in result:
        if not isinstance(raw, dict):
            continue
        amount = _yuan_to_fen(raw.get("amount"))
        if amount == 0:
            continue
        if str(raw.get("type") or "").strip().lower() in ("income", "收入"):
            amount = -abs(amount)
        else:
            amount = abs(amount)
        expense_id = uuid.uuid4().hex[:12]
        conn.execute(
            """INSERT INTO expenses (id, account_id, amount_fen, category, merchant, note, spent_at,
               source, image_url, status, created_at)
               VALUES (?, ?, ?, ?, ?, '', ?, 'vision', ?, 'pending', ?)""",
            (
                expense_id,
                account_id,
                amount,
                _norm_category(raw.get("category")),
                str(raw.get("merchant") or "").strip()[:50],
                _norm_time(raw.get("time")) or _now(),
                image_url,
                _now(),
            ),
        )
        ids.append(expense_id)
    conn.commit()
    if not ids:
        raise HTTPException(status_code=400, detail="没识别出有效账目，请手动录入")
    rows = conn.execute(
        f"SELECT * FROM expenses WHERE id IN ({','.join('?' * len(ids))}) ORDER BY created_at, rowid",
        ids,
    ).fetchall()
    return {"items": [dict(r) for r in rows]}


def add_expense(
    account_id: str,
    amount_fen: int | None,
    category: str = "其他",
    merchant: str = "",
    note: str = "",
    spent_at: str | None = None,
) -> dict:
    """手动记账（现金兜底 / 未配视觉模型的降级路径）：直接 confirmed。收入传负数。"""
    conn = get_conn()
    _require_account(conn, account_id)
    amount_fen = _check_amount(amount_fen)
    if spent_at:
        spent_at = _norm_time(spent_at)
        if not spent_at:
            raise HTTPException(status_code=400, detail="时间格式应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM")
    expense_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO expenses (id, account_id, amount_fen, category, merchant, note, spent_at,
           source, image_url, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', '', 'confirmed', ?)""",
        (
            expense_id,
            account_id,
            amount_fen,
            _norm_category(category),
            (merchant or "").strip()[:50],
            (note or "").strip()[:100],
            spent_at or _now(),
            _now(),
        ),
    )
    conn.commit()
    return {"id": expense_id, "status": "confirmed"}


def _get_owned_expense(conn, expense_id: str, account_id: str):
    row = conn.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="账目不存在")
    if row["account_id"] != account_id:
        raise HTTPException(status_code=403, detail="只能改自己的账目")
    return row


def update_expense(
    expense_id: str,
    account_id: str,
    amount_fen: int | None = None,
    category: str | None = None,
    merchant: str | None = None,
    note: str | None = None,
    spent_at: str | None = None,
) -> dict:
    """改账（仅 owner）：金额/分类/商户/备注/时间，传入什么改什么；不动确认状态。"""
    conn = get_conn()
    row = _get_owned_expense(conn, expense_id, account_id)
    fields, values = [], []
    if amount_fen is not None:
        fields.append("amount_fen = ?")
        values.append(_check_amount(amount_fen))
    if category is not None:
        fields.append("category = ?")
        values.append(_norm_category(category))
    if merchant is not None:
        fields.append("merchant = ?")
        values.append(merchant.strip()[:50])
    if note is not None:
        fields.append("note = ?")
        values.append(note.strip()[:100])
    if spent_at is not None:
        norm = _norm_time(spent_at)
        if not norm:
            raise HTTPException(status_code=400, detail="时间格式应为 YYYY-MM-DD 或 YYYY-MM-DD HH:MM")
        fields.append("spent_at = ?")
        values.append(norm)
    if fields:
        values.append(expense_id)
        conn.execute(f"UPDATE expenses SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
    return {"id": expense_id, "status": row["status"]}


def confirm_expense(
    expense_id: str,
    account_id: str,
    amount_fen: int | None = None,
    category: str | None = None,
    merchant: str | None = None,
    note: str | None = None,
    spent_at: str | None = None,
) -> dict:
    """待确认 → 入账（可同时改金额/分类等）；已入账的重复确认幂等返回。无同步联动（进度读取时现算）。"""
    conn = get_conn()
    row = _get_owned_expense(conn, expense_id, account_id)
    if row["status"] != "confirmed":
        update_expense(expense_id, account_id, amount_fen, category, merchant, note, spent_at)
        conn.execute("UPDATE expenses SET status = 'confirmed' WHERE id = ?", (expense_id,))
        conn.commit()
    return {"id": expense_id, "status": "confirmed"}


def delete_expense(expense_id: str, account_id: str) -> dict:
    """删账（仅 owner，pending/confirmed 均可删）。"""
    conn = get_conn()
    _get_owned_expense(conn, expense_id, account_id)
    conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    conn.commit()
    return {"id": expense_id, "status": "deleted"}


def list_expenses(account_id: str, month: str | None = None) -> dict:
    """月账单：只列已入账（pending 未确认不进账本），附月度支出合计与月可花额度（有存款目标时）。"""
    conn = get_conn()
    _require_account(conn, account_id)
    month = month or date.today().isoformat()[:7]
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(status_code=400, detail="月份格式应为 YYYY-MM")
    rows = conn.execute(
        """SELECT * FROM expenses WHERE account_id = ? AND status = 'confirmed'
           AND substr(spent_at, 1, 7) = ? ORDER BY spent_at DESC, created_at DESC""",
        (account_id, month),
    ).fetchall()
    out = {
        "month": month,
        "items": [dict(r) for r in rows],
        "month_total_fen": sum(r["amount_fen"] for r in rows if r["amount_fen"] > 0),
    }
    goal = conn.execute(
        "SELECT framework FROM goals WHERE account_id = ? AND type = 'savings' AND status = 'active' LIMIT 1",
        (account_id,),
    ).fetchone()
    if goal:
        budget = json.loads(goal["framework"] or "{}").get("monthly_spendable_fen")
        if budget:
            out["monthly_spendable_fen"] = budget
    return out


# ---------- 热量 ----------

def _weight_loss_goal(conn, account_id: str):
    return conn.execute(
        "SELECT * FROM goals WHERE account_id = ? AND type = 'weight_loss' AND status = 'active' LIMIT 1",
        (account_id,),
    ).fetchone()


def _weight_kg(goal) -> float:
    """MET 换算体重：取减肥目标问卷体重，缺省 65kg（前端据此标注"按通用值估算"）。"""
    if goal:
        try:
            weight = float(json.loads(goal["answers"] or "{}").get("weight_kg"))
            if weight > 0:
                return weight
        except (TypeError, ValueError):
            pass
    return float(rules.DEFAULTS["weight_kg"])


def _calorie_dict(row) -> dict:
    d = dict(row)
    d["items"] = json.loads(d.get("items") or "[]")
    d["exercise_equiv"] = json.loads(d.get("exercise_equiv") or "{}")
    return d


def recognize_calorie(account_id: str, image_url: str, hint: str = "") -> dict:
    """食物照片识别 → pending 行（菜品明细 + MET 运动等效）；未配视觉模型抛 400。

    识别与计算拆开：模型只认菜名 + 估分量（grams），热量优先查《中国食物成分表》
    按 kcal_per_100g × grams / 100 计算（item 标 source="table"）；正式表不中再查
    共建预数据库（source="staging"，待核实）；仍不中且联网开关开时联网搜
    （source="web_pending" 待认可，同时写入 staging）；全不中回退模型估值（source="model"）。
    hint 为拍照时补的一句描述（"红烧肉一碗约 300g"），显著提升准确度。
    """
    conn = get_conn()
    _require_account(conn, account_id)
    # 用户历史克数纠正 → 注入识别 prompt 做校准（越估越准的来源）：
    # 同类样例 + 用户级系统偏置（AI 历来高/低估多少，校准所有克数）
    try:
        result = ai.recognize_food(str(_image_path(image_url)), hint or "",
                                   calibration=_gram_corrections(conn, account_id),
                                   bias=_gram_bias(conn, account_id))
    except RuntimeError as exc:
        # 视觉调用失败（参数被厂商拒/网络等）：如实 502，别谎报成"未配置"
        raise HTTPException(status_code=502, detail="视觉模型调用失败，请稍后重试或手动录入") from exc
    if result is None:
        raise HTTPException(status_code=400, detail="未配置视觉模型，请手动录入")
    items = []
    missed: list[tuple[str, str, float | None]] = []  # 本地全不中的菜名：后台联网入库用（含模型隐含单价，供交叉校验）
    # 图片型 RAG：单菜品照片先以图搜图——命中历史确认过的图就直接复用上次的菜名/品牌/单价
    raw_items = [r for r in (result.get("items") or []) if isinstance(r, dict)]
    rag_hit = _image_rag_recall(conn, account_id, image_url) if len(raw_items) == 1 else None
    for raw in raw_items:
        name = str(raw.get("name") or "").strip()[:50]
        if not name:
            continue
        raw_name = name  # 模型原始输出（纠正应用前）：改回原名时精准撤销纠正（防震荡）
        name = _apply_name_correction(conn, account_id, name)  # 用户纠正过的名字直接用纠正名
        brand = str(raw.get("brand") or "").strip()[:30]
        try:
            grams = float(raw.get("grams"))
        except (TypeError, ValueError):
            grams = 0.0
        try:
            model_kcal = float(raw.get("kcal"))
        except (TypeError, ValueError):
            model_kcal = 0.0
        try:  # 模型自报把握度（0-1）：前端把人工确认引导到最没把握的项
            conf = float(raw.get("confidence"))
            confidence = round(max(0.0, min(1.0, conf)), 2)
        except (TypeError, ValueError):
            confidence = None
        if rag_hit and rag_hit.get("kcal_per_100g") and grams > 0:
            # 以图搜图命中：菜名/品牌/单价全部复用历史确认值（同物同价，识别抖动归零）
            name = rag_hit["name"]
            brand = rag_hit["brand"] or brand
            hit = None
            kcal = round(float(rag_hit["kcal_per_100g"]) * grams / 100)
            source = "image_rag"
            staging_id = None
            kcal_per_100g = float(rag_hit["kcal_per_100g"])
        else:
            hit = nutrition.match(name, brand) if grams > 0 else None
            kcal = round(hit["kcal_per_100g"] * grams / 100) if hit else 0
            source = hit["source"] if hit else ""  # table / staging
            staging_id = hit.get("staging_id") if hit else None
            kcal_per_100g = hit["kcal_per_100g"] if hit else None
        if kcal <= 0 and grams > 0:
            # 查表（含 staging）未命中 → 联网兜底移出热路径（新品首次按模型估值标 model），
            # 菜名收集起来由后台任务联网入库——与用户是否确认入账解耦，下次识别直接命中；
            # 模型隐含单价一并带走，入库前与联网值交叉校验（BUG-022：错值不得落全局库）
            missed.append((name, brand,
                           round(model_kcal * 100 / grams, 1)
                           if (model_kcal > 0 and grams > 0) else None))
        if kcal <= 0 and model_kcal > 0:  # 全不中回退模型估值
            # 物理上限钳制：隐含单价超 1000 kcal/100g（纯油 ~900）必是估飞了，钳回上限
            if grams > 0 and model_kcal * 100 / grams > nutrition.MAX_KCAL_PER_100G:
                model_kcal = nutrition.MAX_KCAL_PER_100G * grams / 100
            kcal, source, staging_id, kcal_per_100g = round(model_kcal), "model", None, None
        if kcal <= 0:
            continue
        item = {"name": name, "kcal": kcal, "source": source}
        if confidence is not None:
            item["confidence"] = confidence
        if raw_name and raw_name != name:
            item["raw_name"] = raw_name  # 纠正生效时留底原识别名：改回原名=撤销纠正
        if brand:
            item["brand"] = brand
        if staging_id is not None:
            item["staging_id"] = staging_id
        if grams > 0:
            item["grams"] = round(grams)
        if kcal_per_100g:  # 落每 100g 单价：改克数时按它重算，不用重新匹配
            item["kcal_per_100g"] = kcal_per_100g
        items.append(item)
    total = round(sum(i["kcal"] for i in items), 1)
    if not items or total <= 0:
        raise HTTPException(status_code=400, detail="没识别出有效食物，请手动录入")
    equiv = rules.exercise_equivalents(total, _weight_kg(_weight_loss_goal(conn, account_id)))
    entry_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO calorie_entries (id, account_id, total_kcal, items, exercise_equiv, note,
           source, image_url, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'vision', ?, 'pending', ?)""",
        (
            entry_id,
            account_id,
            total,
            json.dumps(items, ensure_ascii=False),
            json.dumps(equiv, ensure_ascii=False),
            str(result.get("note") or "").strip()[:100],
            image_url,
            _now(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM calorie_entries WHERE id = ?", (entry_id,)).fetchone()
    return {"entry": _calorie_dict(row), "missed": missed}


def backfill_food_web(account_id: str, missed: list[tuple[str, str, float | None]]) -> None:
    """识别热路径未命中的菜名：后台联网查询并写入共建库（与用户是否确认入账解耦；
    即使估值被丢弃也会入库）。下次识别同食物直接命中 staging，不再等待。

    并发 3 路（每个菜名一次联网调用实测约 20-25s，串行太慢）；每个菜名经任务层
    run_task 执行（失败重试 2 次、指数退坡、落库状态可查）。入库前过 sanitize_web_value
    清洗（BUG-022：联网错值差模型 10 倍以上/饮品超先验 → 不落库）；入库成功经 SSE
    通知该账号在线页面刷新（升级提示自动出现，不用等用户手动刷新）。
    """
    from concurrent.futures import ThreadPoolExecutor

    from . import events, tasks

    def _one(name: str, brand: str, model_per_100g: float | None) -> None:
        def _run() -> None:
            web = ai.web_search_food(name, brand, model_per_100g)  # 估值随行：搜索端交叉自检用
            if web is None:
                return
            staged = nutrition.upsert_staging_web(name, web, brand, model_per_100g)
            if staged is None:
                logger.warning("联网值未通过合理性校验，不落共建库：%s → %s kcal/100g"
                               "（模型参考 %s）", name, web.get("kcal_per_100g"), model_per_100g)
                return
            if staged.get("note") or web.get("basis"):
                logger.info("联网值入库：%s %s kcal/100g（%s）%s", name, staged["kcal_per_100g"],
                            web.get("basis") or "口径未说明", staged.get("note", ""))
            events.publish(account_id, {
                "type": "staging_ready",
                "name": name,
                "kcal_per_100g": staged["kcal_per_100g"],
            })

        tasks.run_task("food_web_backfill", name, _run, retries=2)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda nbm: _one(*nbm), missed))


def _calorie_linkage(conn, account_id: str) -> dict | None:
    """热量联动（减肥 ↔ 热量）：今日累计超今日预算 → 插/更新今日 adjust 条目（幂等）。

    无减肥目标 / 无预算 / 没超 → 返回 None，什么都不发生（已有条目一律不动）。
    """
    goal = _weight_loss_goal(conn, account_id)
    if goal is None:
        return None
    budget = json.loads(goal["framework"] or "{}").get("budget_kcal")
    if not budget:
        return None
    day = date.today().isoformat()
    consumed = conn.execute(
        """SELECT COALESCE(SUM(total_kcal), 0) AS s FROM calorie_entries
           WHERE account_id = ? AND status = 'confirmed' AND substr(created_at, 1, 10) = ?""",
        (account_id, day),
    ).fetchone()["s"]
    adj = rules.calorie_adjustment(float(budget), float(consumed), _weight_kg(goal))
    if adj is None:
        return None
    options = " / ".join(f"{v['name']}{v['minutes']}分钟" for v in adj["exercise"].values())
    content = f"今日热量已超预算 {adj['over_kcal']} kcal，运动补偿：{options}"
    existing = conn.execute(
        "SELECT id FROM plan_items WHERE account_id = ? AND date = ? AND source = 'adjust'",
        (account_id, day),
    ).fetchone()
    if existing:
        conn.execute("UPDATE plan_items SET content = ? WHERE id = ?", (content, existing["id"]))
        item_id = existing["id"]
    else:
        item_id = uuid.uuid4().hex[:12]
        conn.execute(
            """INSERT INTO plan_items (id, account_id, goal_id, date, content, kind, source, done, created_at)
               VALUES (?, ?, ?, ?, ?, 'task', 'adjust', 0, ?)""",
            (item_id, account_id, goal["id"], day, content, _now()),
        )
    conn.commit()
    return {
        "plan_item_id": item_id,
        "content": content,
        "over_kcal": adj["over_kcal"],
        "exercise": adj["exercise"],
    }


def _image_rag_recall(conn, account_id: str, image_url: str) -> dict | None:
    """以图搜图（图片型 RAG）：当前识别图与这个账号历史确认图的图片向量做余弦，
    top1 ≥ IMAGE_RAG_THRESHOLD 才命中，返回历史确认的菜名/品牌/单价。
    账号级隔离（隐私铁律）；无历史/失败一律 None 走常管线。"""
    has_rows = conn.execute(
        "SELECT 1 FROM calorie_food_images WHERE account_id = ? AND embedding IS NOT NULL LIMIT 1",
        (account_id,),
    ).fetchone()
    if not has_rows:
        return None
    try:
        vec = ai.embed_food_image(_image_path(image_url).read_bytes(), "jpeg")
    except Exception as exc:  # noqa: BLE001
        logger.warning("图片向量化失败，跳过以图搜图：%s", exc)
        return None
    rows = conn.execute(
        "SELECT name, brand, kcal_per_100g, typical_grams, embedding FROM calorie_food_images"
        " WHERE account_id = ? AND embedding IS NOT NULL",
        (account_id,),
    ).fetchall()
    best, best_sim = None, 0.0
    for r in rows:
        emb = decode_embedding(r["embedding"])
        if emb is None:
            continue
        sim = cosine(vec, emb)
        if sim > best_sim:
            best, best_sim = r, sim
    if best is not None and best_sim >= IMAGE_RAG_THRESHOLD:
        return {"name": best["name"], "brand": best["brand"],
                "kcal_per_100g": best["kcal_per_100g"],
                "typical_grams": best["typical_grams"], "similarity": round(best_sim, 3)}
    return None


def _food_image_store(conn, row) -> None:
    """确认入账后把识别图向量化入库（图片型 RAG）：仅单菜品 + 有图的记录。
    失败只记日志，不影响入账。"""
    try:
        items = json.loads(row["items"] or "[]")
        if not row["image_url"] or len(items) != 1:
            return
        it = items[0]
        name = str(it.get("name") or "").strip()
        if not name:
            return
        g = float(it.get("grams") or 0)
        per100 = it.get("kcal_per_100g")
        if per100 is None and g > 0 and float(it.get("kcal") or 0) > 0:
            per100 = round(float(it["kcal"]) * 100 / g, 1)  # 模型估值项按确认值折算单价
        vec = ai.embed_food_image(_image_path(row["image_url"]).read_bytes(), "jpeg")
        conn.execute(
            """INSERT INTO calorie_food_images
               (id, account_id, name, brand, kcal_per_100g, typical_grams, image_url,
                embedding, entry_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex[:12], row["account_id"], name,
             str(it.get("brand") or "")[:30], per100, g or None, row["image_url"],
             encode_embedding(vec), row["id"], _now()),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("食物图片向量入库失败（不影响入账）：%s", exc)


def _gram_corrections(conn, account_id: str, limit: int = 8) -> list[dict]:
    """该账号最近的克数纠正（同名取最新），注入识别 prompt 做校准样例。"""
    rows = conn.execute(
        """SELECT name, brand, ai_grams, user_grams FROM calorie_gram_corrections
           WHERE account_id = ? ORDER BY rowid DESC LIMIT ?""",
        (account_id, limit * 3),
    ).fetchall()
    seen: set[str] = set()
    out = []
    for r in rows:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        out.append({"name": r["name"], "brand": r["brand"],
                    "ai_grams": r["ai_grams"], "user_grams": r["user_grams"]})
        if len(out) >= limit:
            break
    return out


def _gram_bias(conn, account_id: str, min_samples: int = 3,
               window: int = 30, cap: float = 1.5) -> float | None:
    """用户级克数偏置：历史纠正里 用户实际/模型估计 的中位比值（钳到 [1/cap, cap]）。

    「这个用户的 AI 历来低估 20%」这种系统偏置比逐条样例更稳——样例只帮同类食物，
    偏置帮所有食物。样本 <min_samples 或钳到边界（离谱单点）不给，宁缺毋滥。
    """
    rows = conn.execute(
        """SELECT ai_grams, user_grams FROM calorie_gram_corrections
           WHERE account_id = ? ORDER BY rowid DESC LIMIT ?""",
        (account_id, window),
    ).fetchall()
    ratios = [float(r["user_grams"]) / float(r["ai_grams"])
              for r in rows if r["ai_grams"] and float(r["ai_grams"]) > 0
              and r["user_grams"] and float(r["user_grams"]) > 0]
    if len(ratios) < min_samples:
        return None
    med = statistics.median(ratios)
    if med > cap or med < 1 / cap:
        return None  # 中位都离谱说明纠正数据本身不可信（用户乱改/单位错）
    return round(med, 2)


def _apply_name_correction(conn, account_id: str, name: str) -> str:
    """识别出的菜名先过一遍该账号的名字纠正表：有纠正记录就用纠正名（最新一条生效）。"""
    row = conn.execute(
        """SELECT corrected_name FROM calorie_name_corrections
           WHERE account_id = ? AND recognized_name = ? ORDER BY rowid DESC LIMIT 1""",
        (account_id, name),
    ).fetchone()
    return row["corrected_name"] if row else name


def _record_name_correction(conn, account_id: str, entry_id: str, item: dict,
                            new_name: str) -> None:
    """改名时写名字纠正（BUG-021：防「红茶↔火腿」双向震荡）。

    - 纠正记在「模型会怎么叫它」上：条目带 raw_name（被纠正过的识别结果）时记
      raw_name→new（模型下次还输出 raw_name），否则记当前名→new
    - 改回 raw_name = 撤销纠正：删除正向行 (raw_name→当前名)，不新增反向行——
      反向行 (火腿→红茶) 会让真火腿照片也变成红茶，正是震荡根源
    - 其他改名先清反向行 (new→key) 再插入，保证任何时刻一个名字对只有一个纠正方向
    """
    cur = str(item.get("name") or "")[:50]
    raw_name = str(item.get("raw_name") or "").strip()[:50]
    key = raw_name if (raw_name and raw_name != new_name) else cur
    if new_name == raw_name:
        conn.execute(
            """DELETE FROM calorie_name_corrections
               WHERE account_id = ? AND recognized_name = ? AND corrected_name = ?""",
            (account_id, raw_name, cur),
        )
        return
    conn.execute(
        """DELETE FROM calorie_name_corrections
           WHERE account_id = ? AND recognized_name = ? AND corrected_name = ?""",
        (account_id, new_name, key),
    )
    conn.execute(
        """INSERT INTO calorie_name_corrections
           (id, account_id, recognized_name, corrected_name, entry_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (uuid.uuid4().hex[:12], account_id, key, new_name, entry_id, _now()),
    )


def lookup_food(account_id: str, name: str, grams=None) -> dict:
    """手动录入的即时匹配（只读不写库）：正式表/共建库命中返回单价；给了克数顺带算好热量。"""
    conn = get_conn()
    _require_account(conn, account_id)
    name = str(name or "").strip()[:50]
    if not name:
        raise HTTPException(status_code=400, detail="食物名不能为空")
    hit = nutrition.match(name)
    if hit is None:
        return {"found": False}
    out = {"found": True, "name": hit["name"], "kcal_per_100g": hit["kcal_per_100g"],
           "source": hit["source"]}
    try:
        g = float(grams)
    except (TypeError, ValueError):
        g = 0.0
    if g > 0:
        out["grams"] = g
        out["kcal"] = round(float(hit["kcal_per_100g"]) * g / 100)
    return out


def update_calorie_item(entry_id: str, account_id: str, index: int,
                        grams=None, name: str | None = None) -> dict:
    """改某菜品：克数和/或名字（至少改一样）。

    克数：按 kcal_per_100g 重算（无则按旧值线性缩放）；与原估值不同记一条克数纠正。
    名字：记名字纠正（识别名→纠正名，下次识别直接生效），再重走匹配链——查表/共建库命中
    按新单价×克数重算；不中且联网开时联网搜、搜到写入 staging（标 web_pending）；全不中
    保留当前热量标 model 估值。total/运动等效同步；已入账重触发超预算联动。
    """
    conn = get_conn()
    row = conn.execute("SELECT * FROM calorie_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if row["account_id"] != account_id:
        raise HTTPException(status_code=403, detail="只能改自己的记录")
    items = json.loads(row["items"] or "[]")
    if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < len(items)):
        raise HTTPException(status_code=400, detail="菜品序号不对")
    item = items[index]
    changed = False

    # ---- 改名字/重匹配：名字变了记纠正；重走匹配链（同名也走——「联网数据已入库，
    # 点可更新」的升级动作就是同名重匹配） ----
    if name is not None:
        new_name = str(name or "").strip()[:50]
        if not new_name:
            raise HTTPException(status_code=400, detail="菜名不能为空")
        renamed = new_name != item.get("name")
        if renamed:
            # 留底本次改名前的名字（模型原始输出或最早的录入名）：之后再改回它 = 撤销纠正
            item.setdefault("raw_name", str(item.get("name") or "")[:50])
            _record_name_correction(conn, account_id, entry_id, item, new_name)
        brand = str(item.get("brand") or "")
        g0 = float(item.get("grams") or 0)
        hit = nutrition.match(new_name, brand)
        web = ai.web_search_food(new_name, brand) if hit is None and g0 > 0 else None
        # 联网值同样过合理性清洗（此处无模型参照可用——旧条目的热量是旧食物的，只做品类先验）
        staged = nutrition.upsert_staging_web(new_name, web, brand) if web is not None else None
        if hit is not None or staged is not None:
            # 查表/共建库命中，或联网搜到（清洗通过写入 staging 标待认可）：按新单价×克数重算
            per100 = float(hit["kcal_per_100g"]) if hit is not None else staged["kcal_per_100g"]
            if hit is not None:
                item["source"] = hit["source"]
                if hit.get("staging_id") is not None:
                    item["staging_id"] = hit["staging_id"]
                else:
                    item.pop("staging_id", None)
            else:
                item["source"] = "web_pending"
                item["staging_id"] = staged["staging_id"]
            item["kcal_per_100g"] = per100
            if g0 > 0:
                item["kcal"] = round(per100 * g0 / 100)
            item["name"] = new_name
            changed = True
        elif renamed:
            # 改名且全不中：保留当前热量，标估值
            item["source"] = "model"
            item.pop("staging_id", None)
            item.pop("kcal_per_100g", None)
            item["name"] = new_name
            changed = True

    # ---- 改克数：按（可能刚更新的）单价重算 + 克数纠正 ----
    if grams is not None:
        try:
            grams = float(grams)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="克数必须是数字")
        if not (0 < grams <= 5000):
            raise HTTPException(status_code=400, detail="克数要在 1-5000 之间")
        old_grams = float(item.get("grams") or 0)
        old_kcal = float(item.get("kcal") or 0)
        if old_grams <= 0:
            raise HTTPException(status_code=400, detail="这个菜品没有克数可改")
        if item.get("kcal_per_100g"):
            new_kcal = round(float(item["kcal_per_100g"]) * grams / 100)
        else:  # 模型估值没有单价：按旧值线性缩放
            new_kcal = round(old_kcal * grams / old_grams)
        if new_kcal <= 0:
            raise HTTPException(status_code=400, detail="改完热量不对，检查一下克数")
        # 与原估值不同才记纠正（幂等：同值反复保存不灌水）
        if round(grams) != round(old_grams):
            conn.execute(
                """INSERT INTO calorie_gram_corrections
                   (id, account_id, name, brand, ai_grams, user_grams, entry_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex[:12], account_id, str(item.get("name") or "")[:50],
                 str(item.get("brand") or "")[:30], old_grams, grams, entry_id, _now()),
            )
        item["grams"] = round(grams)
        item["kcal"] = new_kcal
        changed = True

    if not changed:
        raise HTTPException(status_code=400, detail="没有改动")

    total = round(sum(float(i.get("kcal") or 0) for i in items), 1)
    equiv = rules.exercise_equivalents(total, _weight_kg(_weight_loss_goal(conn, account_id)))
    conn.execute(
        "UPDATE calorie_entries SET items = ?, total_kcal = ?, exercise_equiv = ? WHERE id = ?",
        (json.dumps(items, ensure_ascii=False), total,
         json.dumps(equiv, ensure_ascii=False), entry_id),
    )
    conn.commit()
    adjustment = _calorie_linkage(conn, account_id) if row["status"] == "confirmed" else None
    updated = conn.execute("SELECT * FROM calorie_entries WHERE id = ?", (entry_id,)).fetchone()
    return {"entry": _calorie_dict(updated), "adjustment": adjustment}


def add_calorie(account_id: str, total_kcal: float | None, note: str = "",
                items: list[dict] | None = None) -> dict:
    """手动录入热量（数字 + 备注）：直接 confirmed，触发超预算联动。

    items 为可选结构化明细（手动录入时按食物名查表命中后带上）：轻校验，
    非法条目丢弃；有明细的记录之后也能逐项改克数/改名。
    """
    conn = get_conn()
    _require_account(conn, account_id)
    kcal = _check_kcal(total_kcal)
    clean: list[dict] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        n = str(raw.get("name") or "").strip()[:50]
        if not n:
            continue
        try:
            ik = round(float(raw.get("kcal") or 0))
        except (TypeError, ValueError):
            continue
        if ik <= 0:
            continue
        it: dict = {"name": n, "kcal": ik, "source": str(raw.get("source") or "manual")[:20]}
        try:
            g = float(raw.get("grams") or 0)
            if g > 0:
                it["grams"] = round(g)
        except (TypeError, ValueError):
            pass
        try:
            p = float(raw.get("kcal_per_100g") or 0)
            if p > 0:
                it["kcal_per_100g"] = p
        except (TypeError, ValueError):
            pass
        clean.append(it)
    equiv = rules.exercise_equivalents(kcal, _weight_kg(_weight_loss_goal(conn, account_id)))
    entry_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO calorie_entries (id, account_id, total_kcal, items, exercise_equiv, note,
           source, image_url, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'manual', '', 'confirmed', ?)""",
        (entry_id, account_id, kcal, json.dumps(clean, ensure_ascii=False),
         json.dumps(equiv, ensure_ascii=False), (note or "").strip()[:100], _now()),
    )
    conn.commit()
    adjustment = _calorie_linkage(conn, account_id)
    return {"id": entry_id, "status": "confirmed", "adjustment": adjustment}


def confirm_calorie(
    entry_id: str, account_id: str, total_kcal: float | None = None, note: str | None = None
) -> dict:
    """待确认 → 入账（数字可改，改了重算运动等效）；确认后触发超预算联动。重复确认幂等。

    共建认可：条目里 source ∈ staging/web_pending 的菜品（带 staging_id）在首次确认时
    计一次认可（同账号同食物去重），满 3 个不同账号认可晋升正式成分表。
    """
    conn = get_conn()
    row = conn.execute("SELECT * FROM calorie_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if row["account_id"] != account_id:
        raise HTTPException(status_code=403, detail="只能改自己的记录")
    if row["status"] != "confirmed":
        fields = ["status = 'confirmed'"]
        values = []
        if total_kcal is not None:
            kcal = _check_kcal(total_kcal)
            fields.append("total_kcal = ?")
            values.append(kcal)
            fields.append("exercise_equiv = ?")
            values.append(
                json.dumps(
                    rules.exercise_equivalents(kcal, _weight_kg(_weight_loss_goal(conn, account_id))),
                    ensure_ascii=False,
                )
            )
        if note is not None:
            fields.append("note = ?")
            values.append(note.strip()[:100])
        values.append(entry_id)
        conn.execute(f"UPDATE calorie_entries SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()
        for item in json.loads(row["items"] or "[]"):
            if isinstance(item, dict) and item.get("source") in ("staging", "web_pending", "model"):
                # 联网兜底已移出热路径（后台异步入库）：item 里没有 staging_id 时按名字反查补上
                staging_id = item.get("staging_id") or nutrition.staging_id_by_name(
                    conn, str(item.get("name") or ""), str(item.get("brand") or ""))
                if staging_id is not None:
                    nutrition.approve_staging(staging_id, account_id)
        # 图片型 RAG：单菜品 + 有图的确认记录向量化入库（下次以图搜图直接复用菜名/品牌/单价）
        _food_image_store(conn, conn.execute(
            "SELECT * FROM calorie_entries WHERE id = ?", (entry_id,)).fetchone())
    adjustment = _calorie_linkage(conn, account_id)
    return {"id": entry_id, "status": "confirmed", "adjustment": adjustment}


def delete_calorie(entry_id: str, account_id: str) -> dict:
    """删除一条热量记录（仅本人；物理删除，预算联动随下一次入账自然重算）。"""
    conn = get_conn()
    _require_account(conn, account_id)
    row = conn.execute("SELECT id, account_id FROM calorie_entries WHERE id = ?",
                       (entry_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if row["account_id"] != account_id:
        raise HTTPException(status_code=403, detail="只能删自己的记录")
    conn.execute("DELETE FROM calorie_entries WHERE id = ?", (entry_id,))
    conn.commit()
    return {"status": "deleted"}


def list_corrections(account_id: str) -> dict:
    """我的识别纠正记录（名字/克数两张表，纠正错了可删——删了下次识别不再生效）。"""
    conn = get_conn()
    _require_account(conn, account_id)
    names = conn.execute(
        """SELECT id, recognized_name, corrected_name, created_at
           FROM calorie_name_corrections WHERE account_id = ? ORDER BY rowid DESC""",
        (account_id,),
    ).fetchall()
    grams = conn.execute(
        """SELECT id, name, ai_grams, user_grams, created_at
           FROM calorie_gram_corrections WHERE account_id = ? ORDER BY rowid DESC""",
        (account_id,),
    ).fetchall()
    return {
        "names": [dict(r) for r in names],
        "grams": [dict(r) for r in grams],
    }


def delete_correction(account_id: str, kind: str, correction_id: str) -> dict:
    """删除一条识别纠正（kind=name/gram），仅本人。"""
    if kind not in ("name", "gram"):
        raise HTTPException(status_code=400, detail="kind 只能是 name/gram")
    conn = get_conn()
    _require_account(conn, account_id)
    table = "calorie_name_corrections" if kind == "name" else "calorie_gram_corrections"
    row = conn.execute(f"SELECT account_id FROM {table} WHERE id = ?",
                       (correction_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="纠正记录不存在")
    if row["account_id"] != account_id:
        raise HTTPException(status_code=403, detail="只能删自己的纠正")
    conn.execute(f"DELETE FROM {table} WHERE id = ?", (correction_id,))
    conn.commit()
    return {"status": "deleted"}


def list_calories(account_id: str, day: str | None = None) -> dict:
    """某日热量：已入账记录 + 当日累计 + 今日预算（有减肥目标时）。"""
    conn = get_conn()
    _require_account(conn, account_id)
    day = day or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
    rows = conn.execute(
        """SELECT * FROM calorie_entries WHERE account_id = ? AND status = 'confirmed'
           AND substr(created_at, 1, 10) = ? ORDER BY created_at, rowid""",
        (account_id, day),
    ).fetchall()
    out = {
        "date": day,
        "items": [_calorie_dict(r) for r in rows],
        "consumed_kcal": round(sum(r["total_kcal"] for r in rows), 1),
    }
    # 估值条目的升级提示：联网后台入库后，staging 有了同名行 → 挂 upgrade（用户自己决定是否更新）
    for d in out["items"]:
        for it in d["items"]:
            if not isinstance(it, dict) or it.get("source") != "model" or not it.get("grams"):
                continue
            sid = nutrition.staging_id_by_name(conn, str(it.get("name") or ""),
                                               str(it.get("brand") or ""))
            if sid is None:
                continue
            srow = conn.execute(
                "SELECT kcal_per_100g FROM food_nutrition_staging WHERE id = ?", (sid,)
            ).fetchone()
            if srow:
                it["upgrade"] = {
                    "kcal_per_100g": srow["kcal_per_100g"],
                    "kcal": round(float(srow["kcal_per_100g"]) * float(it["grams"]) / 100),
                }
    goal = _weight_loss_goal(conn, account_id)
    if goal:
        budget = json.loads(goal["framework"] or "{}").get("budget_kcal")
        if budget:
            out["budget_kcal"] = budget
    return out
