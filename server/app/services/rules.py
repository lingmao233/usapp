"""个人功能规则引擎：纯函数模块。

不碰 db、不调 AI、无外部状态，输入输出全是 dict/数字——测试直接断言。
公式口径见 docs/个人功能-目标计划记账热量设计.md §3.2。
金额一律 INTEGER 分；问卷缺字段走通用默认值并标注 estimated。
"""
import json
import math
from datetime import date

from ..data.met import MET

# 每减 1kg 体重约需 7700kcal 缺口；每日缺口上限（安全口径）
KCAL_PER_KG = 7700
MAX_DAILY_DEFICIT_KCAL = 500

# 运动基础（问卷 answers.activity）→ 活动系数
ACTIVITY_FACTORS: dict[str, float] = {
    "sedentary": 1.2,    # 久坐
    "light": 1.375,      # 每周轻运动 1-3 次
    "moderate": 1.55,    # 每周中等运动 3-5 次
    "active": 1.725,     # 每周高强度运动 6-7 次
}

# 问卷缺失时的通用默认值
DEFAULTS = {"sex": "male", "weight_kg": 65.0, "height_cm": 165.0, "age": 30, "activity": "sedentary"}

# 运动等效换算用的四种标准运动（键 = 输出 key，值 = met.py 里的运动名）
EQUIV_EXERCISES: dict[str, str] = {
    "running": "跑步（8公里/小时）",
    "walking": "快走（6.4公里/小时）",
    "cycling": "骑车（16公里/小时）",
    "swimming": "游泳（慢速）",
}


def _num(raw: object, default: float) -> float:
    """问卷数字容错：缺失/非法走默认值。"""
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _days_left(params: dict) -> int | None:
    """剩余天数：params.days_left 优先，其次按 params.deadline('YYYY-MM-DD') 对今天折算。"""
    raw = params.get("days_left")
    if raw:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    try:
        deadline = date.fromisoformat(str(params.get("deadline") or ""))
    except ValueError:
        return None
    return max(1, (deadline - date.today()).days)


def _months_left(params: dict) -> int | None:
    """剩余月数：params.months_left 优先，其次按 deadline 折算（含当月，至少 1）。"""
    raw = params.get("months_left")
    if raw:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    try:
        deadline = date.fromisoformat(str(params.get("deadline") or ""))
    except ValueError:
        return None
    today = date.today()
    return max(1, (deadline.year - today.year) * 12 + deadline.month - today.month + 1)


def bmr(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    """Mifflin-St Jeor 基础代谢（kcal/天）：男 = 10w + 6.25h − 5a + 5，女末项 −161。"""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + 5 if sex == "male" else base - 161


def daily_calorie_budget(answers: dict, params: dict) -> dict:
    """每日热量预算：BMR × 活动系数 − 减重缺口分摊（缺口上限 500kcal/天）。

    answers: sex/weight_kg/height_cm/age/activity（缺字段走 DEFAULTS 并记 estimated_fields）
    params: target_weight_kg + days_left/deadline（缺了则不削减，deficit=0）
    """
    answers = answers or {}
    params = params or {}
    estimated_fields: list[str] = []

    def field(key: str) -> float:
        if key not in answers:
            estimated_fields.append(key)
        return _num(answers.get(key), DEFAULTS[key])  # type: ignore[index]

    sex = str(answers.get("sex") or "").strip().lower()
    if sex not in ("male", "female"):
        sex = str(DEFAULTS["sex"])
        estimated_fields.append("sex")
    activity = str(answers.get("activity") or "").strip().lower()
    if activity not in ACTIVITY_FACTORS:
        activity = str(DEFAULTS["activity"])
        estimated_fields.append("activity")

    weight = field("weight_kg")
    base = bmr(sex, weight, field("height_cm"), int(field("age")))
    factor = ACTIVITY_FACTORS[activity]
    tdee = base * factor

    deficit = 0.0
    days = _days_left(params)
    target_weight = _num(params.get("target_weight_kg"), 0.0)
    if target_weight > 0 and days:
        gap_kcal = max(0.0, weight - target_weight) * KCAL_PER_KG
        deficit = min(float(MAX_DAILY_DEFICIT_KCAL), gap_kcal / days)
    elif "target_weight_kg" not in params:
        estimated_fields.append("target_weight_kg")

    return {
        "bmr_kcal": round(base),
        "activity_factor": factor,
        "tdee_kcal": round(tdee),
        "deficit_kcal": round(deficit),
        "budget_kcal": round(tdee - deficit),
        "days_left": days,
        "estimated": bool(estimated_fields),
        "estimated_fields": estimated_fields,
    }


def exercise_equivalents(kcal: float, weight_kg: float) -> dict:
    """把 kcal 换算成四种标准运动的等效分钟数（kcal = MET × 体重kg × 小时）。"""
    weight = _num(weight_kg, DEFAULTS["weight_kg"])  # type: ignore[index]
    result: dict[str, dict] = {}
    for key, name in EQUIV_EXERCISES.items():
        met = MET[name]
        minutes = round(kcal / (met * weight) * 60) if kcal > 0 else 0
        result[key] = {"name": name, "met": met, "minutes": minutes}
    return result


def savings_monthly_plan(params: dict, answers: dict, spending_profile: dict | None = None) -> dict:
    """存款月预算：固定收入 − 固定支出 − 弹性花销基线 → 月建议存款额 + 月可花额度。

    弹性基线：有 spending_profile 用真实区间（日均 × 30），没有按 answers 固定支出口径推算
    （即把固定支出当作全部花销基线，弹性记 0 并标 estimated）。
    params: target_fen/saved_fen/months_left/deadline → 校验目标可达性。
    """
    params = params or {}
    answers = answers or {}
    estimated_fields: list[str] = []

    def fen(key: str) -> int:
        if key not in answers:
            estimated_fields.append(key)
        return int(_num(answers.get(key), 0))

    income = fen("fixed_income_fen")
    fixed = fen("fixed_expense_fen")
    if spending_profile and spending_profile.get("total"):
        elastic = int(spending_profile["total"].get("daily_avg_fen", 0)) * 30
    else:
        elastic = 0
        estimated_fields.append("spending_profile")

    save = max(0, income - fixed - elastic)
    spendable = max(0, income - fixed - save)

    target = int(_num(params.get("target_fen"), 0))
    saved = int(_num(params.get("saved_fen"), 0))
    months = _months_left(params)
    required = math.ceil(max(0, target - saved) / months) if target > 0 and months else None

    return {
        "monthly_save_fen": save,
        "monthly_spendable_fen": spendable,
        "elastic_baseline_fen": elastic,
        "months_left": months,
        "required_monthly_fen": required,
        "reachable": (save >= required) if required is not None else None,
        "estimated": bool(estimated_fields),
        "estimated_fields": estimated_fields,
    }


def savings_settlement(goal: dict, actual_saved_fen: int) -> dict:
    """滚雪球重算：目标总额 − 已累计存入，差额分摊进剩余月份 → 新的每月目标。

    goal.params 接受 dict 或 JSON 字符串；actual_saved_fen 为本月实际存入（用户可修正）。
    """
    params = goal.get("params") or {}
    if isinstance(params, str):
        params = json.loads(params or "{}")
    target = int(_num(params.get("target_fen"), 0))
    saved = int(_num(params.get("saved_fen"), 0)) + int(actual_saved_fen)
    remaining = max(0, target - saved)
    months = _months_left(params) or 1
    return {
        "saved_fen": saved,
        "remaining_fen": remaining,
        "months_left": months,
        "monthly_target_fen": math.ceil(remaining / months) if remaining else 0,
        "done": target > 0 and remaining == 0,
    }


def _parse_day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _agg(day_totals: dict[date, int], days: int) -> dict:
    total = sum(day_totals.values())
    values = list(day_totals.values())
    return {
        "total_fen": total,
        "daily_avg_fen": round(total / days),
        "min_fen": min(values),
        "max_fen": max(values),
    }


def spending_profile(expense_rows: list[dict]) -> dict:
    """消费习惯画像：按类目聚合日均与区间（min/max 取该类目单日总额的最值）。

    rows: [{amount_fen, category, spent_at}]，供读取层用 SQL 聚合近 90 天后传入。
    """
    rows = [
        (int(_num(r.get("amount_fen"), 0)), str(r.get("category") or "其他"), _parse_day(r.get("spent_at")))
        for r in expense_rows or []
    ]
    rows = [(amount, cat, day) for amount, cat, day in rows if amount > 0 and day]
    if not rows:
        return {
            "days": 0,
            "categories": {},
            "total": {"total_fen": 0, "daily_avg_fen": 0, "min_fen": 0, "max_fen": 0},
        }

    days = max(1, (max(d for _, _, d in rows) - min(d for _, _, d in rows)).days + 1)
    by_cat: dict[str, dict[date, int]] = {}
    all_days: dict[date, int] = {}
    for amount, cat, day in rows:
        by_cat.setdefault(cat, {})[day] = by_cat.setdefault(cat, {}).get(day, 0) + amount
        all_days[day] = all_days.get(day, 0) + amount
    return {
        "days": days,
        "categories": {cat: _agg(totals, days) for cat, totals in sorted(by_cat.items())},
        "total": _agg(all_days, days),
    }


def calorie_adjustment(budget: float, consumed: float, weight_kg: float) -> dict | None:
    """热量调整条目数据：今日累计未超预算返回 None；超了返回超出量 + 运动补偿换算。"""
    over = consumed - budget
    if over <= 0:
        return None
    return {
        "over_kcal": round(over),
        "remaining_kcal": 0,
        "exercise": exercise_equivalents(over, weight_kg),
    }
