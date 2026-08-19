"""记账 + 热量：拍照识别（豆包视觉）→ 待确认 → 确认入账；手动录入兜底。

账号级数据（account_id，跨圈唯一一份）。
- 金额一律 INTEGER 分；识别返回的元在入账时换算；收入记负数账目
  （存款结算口径：实际存入 = 固定收入 + 额外收入 − 支出）
- 热量确认后同步联动：今日累计超今日预算 → 插/更新今日 adjust 计划条目（幂等）；
  没超什么都不发生（已有条目一律不动）
"""
import json
import re
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import HTTPException

from .. import ai
from ..ai.prompts import EXPENSE_CATEGORIES
from ..config import settings
from ..db.database import get_conn
from . import nutrition, rules, selfshare

MAX_AMOUNT_FEN = 10**12  # 金额 sanity 上限（分，约百亿）
MAX_KCAL = 20000.0  # 单条热量 sanity 上限


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _require_account(conn, account_id: str) -> None:
    selfshare.require_account(conn, account_id)


def _image_path(image_url: str) -> Path:
    """image_url → 本地文件路径（照抄 fragments.read_display_image：优先 {uuid}_d.jpg 展示图副本）。"""
    if not (image_url or "").startswith("/api/uploads/"):
        raise HTTPException(status_code=400, detail="image_url 只能是本站上传地址")
    base = image_url.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    display = settings.upload_dir / f"{stem}_d.jpg"
    original = settings.upload_dir / base
    path = display if display.is_file() else original
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
    result = ai.recognize_food(str(_image_path(image_url)), hint or "")
    if result is None:
        raise HTTPException(status_code=400, detail="未配置视觉模型，请手动录入")
    items = []
    for raw in result.get("items") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()[:50]
        if not name:
            continue
        brand = str(raw.get("brand") or "").strip()[:30]
        try:
            grams = float(raw.get("grams"))
        except (TypeError, ValueError):
            grams = 0.0
        try:
            model_kcal = float(raw.get("kcal"))
        except (TypeError, ValueError):
            model_kcal = 0.0
        hit = nutrition.match(name, brand) if grams > 0 else None
        kcal = round(hit["kcal_per_100g"] * grams / 100) if hit else 0
        source = hit["source"] if hit else ""  # table / staging
        staging_id = hit.get("staging_id") if hit else None
        if kcal <= 0 and grams > 0:
            # 查表（含 staging）未命中 → 联网搜（品牌参与查询，搜到按品牌款入 staging）
            web = ai.web_search_food(name, brand)
            if web is not None:
                staging_id = nutrition.upsert_staging_web(name, web, brand)
                kcal = round(float(web["kcal_per_100g"]) * grams / 100)
                source = "web_pending"
        if kcal <= 0 and model_kcal > 0:  # 全不中回退模型估值
            kcal, source, staging_id = round(model_kcal), "model", None
        if kcal <= 0:
            continue
        item = {"name": name, "kcal": kcal, "source": source}
        if brand:
            item["brand"] = brand
        if staging_id is not None:
            item["staging_id"] = staging_id
        if grams > 0:
            item["grams"] = round(grams)
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
    return {"entry": _calorie_dict(row)}


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


def add_calorie(account_id: str, total_kcal: float | None, note: str = "") -> dict:
    """手动录入热量（数字 + 备注）：直接 confirmed，触发超预算联动。"""
    conn = get_conn()
    _require_account(conn, account_id)
    kcal = _check_kcal(total_kcal)
    equiv = rules.exercise_equivalents(kcal, _weight_kg(_weight_loss_goal(conn, account_id)))
    entry_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO calorie_entries (id, account_id, total_kcal, items, exercise_equiv, note,
           source, image_url, status, created_at)
           VALUES (?, ?, ?, '[]', ?, ?, 'manual', '', 'confirmed', ?)""",
        (entry_id, account_id, kcal, json.dumps(equiv, ensure_ascii=False), (note or "").strip()[:100], _now()),
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
            if isinstance(item, dict) and item.get("source") in ("staging", "web_pending"):
                nutrition.approve_staging(item.get("staging_id"), account_id)
    adjustment = _calorie_linkage(conn, account_id)
    return {"id": entry_id, "status": "confirmed", "adjustment": adjustment}


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
    goal = _weight_loss_goal(conn, account_id)
    if goal:
        budget = json.loads(goal["framework"] or "{}").get("budget_kcal")
        if budget:
            out["budget_kcal"] = budget
    return out
