"""规则引擎测试：纯函数逐组断言，含问卷缺失走默认值的边界。

运行：cd server && .venv-win/Scripts/python -m pytest tests/test_rules.py -v
"""
import os
import sys
import tempfile
from datetime import date, timedelta

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_rules_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402

from app.services import rules  # noqa: E402


# ---------- BMR（Mifflin-St Jeor） ----------

def test_bmr_male():
    # 10×70 + 6.25×175 − 5×30 + 5 = 1648.75
    assert rules.bmr("male", 70, 175, 30) == pytest.approx(1648.75)


def test_bmr_female():
    # 10×60 + 6.25×160 − 5×28 − 161 = 1299.0
    assert rules.bmr("female", 60, 160, 28) == pytest.approx(1299.0)


# ---------- 每日热量预算 ----------

def test_calorie_budget_full_answers():
    answers = {"sex": "male", "weight_kg": 70, "height_cm": 175, "age": 30, "activity": "sedentary"}
    result = rules.daily_calorie_budget(answers, {"target_weight_kg": 65, "days_left": 100})
    assert result["bmr_kcal"] == 1649
    assert result["tdee_kcal"] == round(1648.75 * 1.2)
    # 缺口：5kg × 7700 ÷ 100 = 385（未触上限）
    assert result["deficit_kcal"] == 385
    assert result["budget_kcal"] == round(1648.75 * 1.2 - 385)
    assert result["estimated"] is False
    assert result["estimated_fields"] == []


def test_calorie_budget_deficit_capped():
    answers = {"sex": "male", "weight_kg": 90, "height_cm": 175, "age": 30, "activity": "light"}
    # 缺口 20kg × 7700 ÷ 30 ≈ 5133 → 封顶 500
    result = rules.daily_calorie_budget(answers, {"target_weight_kg": 70, "days_left": 30})
    assert result["deficit_kcal"] == rules.MAX_DAILY_DEFICIT_KCAL


def test_calorie_budget_deadline_days():
    answers = {"sex": "female", "weight_kg": 60, "height_cm": 160, "age": 28, "activity": "moderate"}
    deadline = (date.today() + timedelta(days=10)).isoformat()
    result = rules.daily_calorie_budget(answers, {"target_weight_kg": 59.5, "deadline": deadline})
    assert result["days_left"] == 10
    assert result["deficit_kcal"] == round(0.5 * 7700 / 10)


def test_calorie_budget_empty_answers_defaults():
    result = rules.daily_calorie_budget({}, {})
    # 默认男 65kg/165cm/30 岁/久坐：bmr = 650 + 1031.25 − 150 + 5 = 1536.25
    assert result["bmr_kcal"] == 1536
    assert result["tdee_kcal"] == round(1536.25 * 1.2)
    assert result["deficit_kcal"] == 0
    assert result["budget_kcal"] == result["tdee_kcal"]
    assert result["estimated"] is True
    for key in ("sex", "weight_kg", "height_cm", "age", "activity", "target_weight_kg"):
        assert key in result["estimated_fields"]


def test_calorie_budget_bad_values_fallback():
    answers = {"sex": "未知", "weight_kg": "很多", "height_cm": None, "age": "三十", "activity": "疯狂"}
    result = rules.daily_calorie_budget(answers, {})
    assert result["estimated"] is True
    assert result["bmr_kcal"] == 1536  # 全部落到默认值


# ---------- 运动等效换算 ----------

def test_exercise_equivalents():
    result = rules.exercise_equivalents(415, 70)
    # 跑步 8.3 MET：415 ÷ (8.3×70) × 60 ≈ 42.9 → 43 分钟
    assert result["running"]["minutes"] == round(415 / (8.3 * 70) * 60)
    assert result["running"]["name"] == "跑步（8公里/小时）"
    # 快走 MET 更低 → 分钟数更多
    assert result["walking"]["minutes"] > result["running"]["minutes"]
    assert set(result) == {"running", "walking", "cycling", "swimming"}


def test_exercise_equivalents_zero():
    result = rules.exercise_equivalents(0, 70)
    assert all(v["minutes"] == 0 for v in result.values())


# ---------- 存款月预算 ----------

def test_savings_monthly_plan_with_profile():
    answers = {"fixed_income_fen": 800000, "fixed_expense_fen": 300000}
    profile = {"total": {"daily_avg_fen": 10000}}
    params = {"target_fen": 1200000, "saved_fen": 0, "months_left": 6}
    result = rules.savings_monthly_plan(params, answers, profile)
    assert result["elastic_baseline_fen"] == 300000
    assert result["monthly_save_fen"] == 200000
    assert result["monthly_spendable_fen"] == 300000
    assert result["required_monthly_fen"] == 200000
    assert result["reachable"] is True
    assert result["estimated"] is False


def test_savings_monthly_plan_unreachable():
    answers = {"fixed_income_fen": 500000, "fixed_expense_fen": 300000}
    profile = {"total": {"daily_avg_fen": 5000}}
    params = {"target_fen": 1200000, "months_left": 3}
    result = rules.savings_monthly_plan(params, answers, profile)
    assert result["monthly_save_fen"] == 50000
    assert result["required_monthly_fen"] == 400000
    assert result["reachable"] is False


def test_savings_monthly_plan_no_profile_estimated():
    answers = {"fixed_income_fen": 800000, "fixed_expense_fen": 300000}
    result = rules.savings_monthly_plan({"months_left": 6}, answers, None)
    assert result["elastic_baseline_fen"] == 0
    assert result["monthly_save_fen"] == 500000
    assert result["estimated"] is True
    assert "spending_profile" in result["estimated_fields"]


def test_savings_monthly_plan_empty():
    result = rules.savings_monthly_plan({}, {})
    assert result["monthly_save_fen"] == 0
    assert result["required_monthly_fen"] is None
    assert result["reachable"] is None
    assert result["estimated"] is True


# ---------- 存款滚雪球结算 ----------

def test_savings_settlement():
    goal = {"params": {"target_fen": 1200000, "saved_fen": 200000, "months_left": 10}}
    result = rules.savings_settlement(goal, 100000)
    assert result["saved_fen"] == 300000
    assert result["remaining_fen"] == 900000
    assert result["monthly_target_fen"] == 90000
    assert result["done"] is False


def test_savings_settlement_done():
    goal = {"params": '{"target_fen": 500000, "saved_fen": 400000, "months_left": 6}'}
    result = rules.savings_settlement(goal, 200000)
    assert result["remaining_fen"] == 0
    assert result["monthly_target_fen"] == 0
    assert result["done"] is True


# ---------- 消费习惯画像 ----------

def test_spending_profile():
    rows = [
        {"amount_fen": 3000, "category": "餐饮", "spent_at": "2026-08-01 12:00"},
        {"amount_fen": 5000, "category": "餐饮", "spent_at": "2026-08-02 18:30"},
        {"amount_fen": 600, "category": "交通", "spent_at": "2026-08-02 09:00"},
        {"amount_fen": 0, "category": "餐饮", "spent_at": "2026-08-02 20:00"},  # 零金额忽略
        {"amount_fen": 1000, "category": "", "spent_at": "bad-date"},            # 坏日期忽略
    ]
    result = rules.spending_profile(rows)
    assert result["days"] == 2
    assert result["categories"]["餐饮"]["total_fen"] == 8000
    assert result["categories"]["餐饮"]["daily_avg_fen"] == 4000
    assert result["categories"]["餐饮"]["min_fen"] == 3000
    assert result["categories"]["餐饮"]["max_fen"] == 5000
    assert result["categories"]["交通"]["daily_avg_fen"] == 300
    assert result["total"]["total_fen"] == 8600
    assert result["total"]["daily_avg_fen"] == 4300


def test_spending_profile_empty():
    result = rules.spending_profile([])
    assert result["days"] == 0
    assert result["categories"] == {}
    assert result["total"]["daily_avg_fen"] == 0


# ---------- 热量调整 ----------

def test_calorie_adjustment_under_budget():
    assert rules.calorie_adjustment(1800, 1500, 65) is None
    assert rules.calorie_adjustment(1800, 1800, 65) is None


def test_calorie_adjustment_over_budget():
    result = rules.calorie_adjustment(1800, 2300, 65)
    assert result is not None
    assert result["over_kcal"] == 500
    assert result["remaining_kcal"] == 0
    assert result["exercise"]["running"]["minutes"] == round(500 / (8.3 * 65) * 60)
