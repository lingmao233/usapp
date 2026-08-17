"""目标系统测试：建目标（framework 规则计算）、列表、viewer 可见性（self_sharing 驱动，
progress/detail 两档）、圈内共享目标列表。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_goals.py -v
"""
import os
import sys
import tempfile
import uuid

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_goals_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _register(client: TestClient) -> str:
    r = client.post("/api/auth/register", json={"username": f"u-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200, r.text
    return r.json()["account_id"]


def _make_circle(client: TestClient, name: str = "目标测试圈"):
    """建圈 + 两名成员，返回 (circle_id, a1, a2)（account_id）。"""
    a1, a2 = _register(client), _register(client)
    circle = client.post("/api/circles", json={
        "name": name, "account_id": a1, "nickname": "阿澈"}).json()
    client.post("/api/circles/join", json={
        "invite_code": circle["invite_code"], "account_id": a2, "nickname": "丫丫"})
    return circle["id"], a1, a2


def _outsider(client: TestClient) -> str:
    """圈外人：另建一个圈的创建者账号。"""
    acc = _register(client)
    client.post("/api/circles", json={"name": "圈外人的圈", "account_id": acc, "nickname": "老周"})
    return acc


def _create_goal(client: TestClient, account_id: str, **over) -> dict:
    body = {
        "account_id": account_id,
        "type": "custom",
        "title": "每天背 20 个单词",
        "params": {},
        "answers": {},
    }
    body.update(over)
    r = client.post("/api/goals", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _share(client: TestClient, account_id: str, circle_id: str, category: str = "goal",
           level: str | None = None) -> None:
    body = {"account_id": account_id, "circle_id": circle_id, "category": category}
    if level:
        body["level"] = level
    r = client.put("/api/self/sharing", json=body)
    assert r.status_code == 200, r.text


# ---------- 建目标：framework 规则计算 ----------

def test_create_weight_loss_framework(client: TestClient) -> None:
    """减肥目标：framework 由 rules 算出（BMR×活动系数−缺口分摊），与纯函数口径一致。"""
    cid, a1, _a2 = _make_circle(client)
    r = client.post("/api/goals", json={
        "account_id": a1, "type": "weight_loss", "title": "瘦 5 公斤",
        "params": {"target_weight_kg": 65, "days_left": 100},
        "answers": {"sex": "male", "weight_kg": 70, "height_cm": 175, "age": 30, "activity": "sedentary"},
    })
    assert r.status_code == 200, r.text
    fw = r.json()["framework"]
    assert fw["bmr_kcal"] == round(10 * 70 + 6.25 * 175 - 5 * 30 + 5)
    assert fw["tdee_kcal"] == round(1648.75 * 1.2)
    assert fw["deficit_kcal"] == round(5 * 7700 / 100)
    assert fw["budget_kcal"] == round(1648.75 * 1.2 - 385)
    assert fw["estimated"] is False
    assert r.json()["status"] == "created" and r.json()["id"]


def test_create_savings_and_study_and_custom_framework(client: TestClient) -> None:
    """存款目标：无消费画像时弹性基线记 0 并标 estimated；学习目标直传每日时长；自定义无框架。"""
    cid, a1, _a2 = _make_circle(client, "三类目标圈")
    r = client.post("/api/goals", json={
        "account_id": a1, "type": "savings", "title": "攒首付",
        "params": {"target_fen": 1200000, "months_left": 6},
        "answers": {"fixed_income_fen": 800000, "fixed_expense_fen": 300000},
    })
    assert r.status_code == 200, r.text
    fw = r.json()["framework"]
    assert fw["monthly_save_fen"] == 500000  # 8000 - 3000 - 0（无画像）
    assert fw["monthly_spendable_fen"] == 0
    assert fw["required_monthly_fen"] == 200000
    assert fw["reachable"] is True
    assert fw["estimated"] is True and "spending_profile" in fw["estimated_fields"]

    r = client.post("/api/goals", json={
        "account_id": a1, "type": "study", "title": "考研英语",
        "answers": {"daily_minutes": 45},
    })
    assert r.json()["framework"] == {"daily_minutes": 45}

    r = _create_goal(client, a1)
    assert r["framework"] == {}


def test_create_goal_defaults_when_answers_missing(client: TestClient) -> None:
    """问卷全跳过：走通用默认值并标 estimated，缺口为 0（无目标体重）。"""
    cid, a1, _a2 = _make_circle(client, "默认问卷圈")
    r = client.post("/api/goals", json={
        "account_id": a1, "type": "weight_loss", "title": "先动起来",
    })
    assert r.status_code == 200, r.text
    fw = r.json()["framework"]
    # 默认男 65kg/165cm/30 岁/久坐：bmr = 650 + 1031.25 − 150 + 5 = 1536.25
    assert fw["bmr_kcal"] == 1536
    assert fw["deficit_kcal"] == 0
    assert fw["budget_kcal"] == fw["tdee_kcal"]
    assert fw["estimated"] is True
    for key in ("sex", "weight_kg", "height_cm", "age", "activity", "target_weight_kg"):
        assert key in fw["estimated_fields"]


def test_create_goal_validation(client: TestClient) -> None:
    """非法类型/空标题/不存在账号 → 对应 4xx。"""
    cid, a1, a2 = _make_circle(client, "校验圈")
    base = {"account_id": a1, "type": "custom", "title": "x"}
    r = client.post("/api/goals", json={**base, "type": " Marathon ".strip()})
    assert r.status_code == 400  # 非四种类型之一
    assert client.post("/api/goals", json={**base, "title": "   "}).status_code == 400
    assert client.post("/api/goals", json={**base, "account_id": "ghost"}).status_code == 404


# ---------- 列表 ----------

def test_list_goals_owner_view(client: TestClient) -> None:
    """我的目标列表：owner 全量（含 params/answers/framework/progress），他人列表不含。"""
    cid, a1, a2 = _make_circle(client, "列表圈")
    g1 = _create_goal(client, a1, title="第一个目标")
    g2 = _create_goal(client, a1, title="第二个目标", type="study", answers={"daily_minutes": 30})

    r = client.get("/api/goals", params={"account_id": a1})
    assert r.status_code == 200, r.text
    goals = r.json()["goals"]
    mine = [g for g in goals if g["id"] in (g1["id"], g2["id"])]
    assert len(mine) == 2
    for g in mine:
        assert g["status"] == "active" and g["nudge_enabled"] is True
        assert g["account_id"] == a1
        assert isinstance(g["params"], dict) and isinstance(g["framework"], dict)
        assert set(g["progress"]) >= {"percent", "streak_days", "today_done", "today_total"}
    # 别人的列表里没有我的目标
    other = client.get("/api/goals", params={"account_id": a2}).json()["goals"]
    assert all(g["id"] not in (g1["id"], g2["id"]) for g in other)
    assert client.get("/api/goals", params={"account_id": "ghost"}).status_code == 404


# ---------- viewer 可见性（self_sharing 驱动） ----------

def test_get_goal_viewer_levels(client: TestClient) -> None:
    """owner 全量 / 圈友 progress 裁明细 / detail 给明细 / 未共享 404（不泄露存在性）。"""
    cid, a1, a2 = _make_circle(client, "可见性圈")
    out = _outsider(client)
    g = _create_goal(
        client, a1, type="savings", title="私密账本",
        params={"target_fen": 500000, "saved_fen": 100000},
        answers={"fixed_income_fen": 900000},
    )
    gid = g["id"]

    # 未共享：圈友与 outsider 都 404，owner 全量可见
    assert client.get(f"/api/goals/{gid}", params={"account_id": a2}).status_code == 404
    assert client.get(f"/api/goals/{gid}", params={"account_id": out}).status_code == 404
    d = client.get(f"/api/goals/{gid}", params={"account_id": a1}).json()
    assert d["params"]["target_fen"] == 500000 and d["answers"]["fixed_income_fen"] == 900000
    assert "framework" in d

    # 共享 goal=progress：圈友可见但裁掉 params/answers/framework 等明细
    _share(client, a1, cid, "goal")
    r = client.get(f"/api/goals/{gid}", params={"account_id": a2})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "私密账本" and d["owner_nickname"] == "阿澈"
    assert d["share_level"] == "progress"
    assert "params" not in d and "answers" not in d and "framework" not in d
    assert "progress" in d and d["viewer_nudged_today"] is False
    # outsider 依旧 404
    assert client.get(f"/api/goals/{gid}", params={"account_id": out}).status_code == 404

    # detail 档：圈友拿到明细（金额/问卷都可见）
    _share(client, a1, cid, "goal", "detail")
    d = client.get(f"/api/goals/{gid}", params={"account_id": a2}).json()
    assert d["share_level"] == "detail"
    assert d["params"]["target_fen"] == 500000
    assert d["framework"]["monthly_save_fen"] > 0
    # 关闭共享：圈友立即回到 404
    client.delete("/api/self/sharing", params={
        "account_id": a1, "circle_id": cid, "category": "goal"})
    assert client.get(f"/api/goals/{gid}", params={"account_id": a2}).status_code == 404


# ---------- 圈内共享目标列表 ----------

def test_circle_goals_list(client: TestClient) -> None:
    """圈内共享目标列表：只列共享到本圈的 active 目标，按档位裁剪；非成员 403。"""
    cid, a1, a2 = _make_circle(client, "伙伴目标圈")
    out = _outsider(client)
    pub = _create_goal(client, a1, title="公开flag")
    # 共享是类别级（self_sharing）：开了 goal 共享，a1 的全部 active 目标对圈友可见
    _share(client, a1, cid, "goal")  # progress 档
    other = _create_goal(client, a1, title="同账号另一目标")
    # 未共享账号的目标不出现
    other_acc = _register(client)
    other_circle = client.post("/api/circles", json={
        "name": "隔壁圈", "account_id": other_acc, "nickname": "隔壁"}).json()
    client.post("/api/goals", json={
        "account_id": other_acc, "type": "custom", "title": "隔壁的目标"})

    r = client.get(f"/api/goals/circle/{cid}", params={"account_id": a2})
    assert r.status_code == 200, r.text
    goals = r.json()["goals"]
    assert {g["title"] for g in goals} == {"公开flag", "同账号另一目标"}
    g = [g for g in goals if g["id"] == pub["id"]][0]
    assert g["owner_nickname"] == "阿澈"
    assert g["share_level"] == "progress"
    assert "params" not in g and "framework" not in g and "answers" not in g
    assert "progress" in g

    # owner 自己看本圈列表也能看到（同为成员）
    r = client.get(f"/api/goals/circle/{cid}", params={"account_id": a1})
    assert any(g["id"] == pub["id"] for g in r.json()["goals"])
    # 非成员 403
    assert client.get(f"/api/goals/circle/{cid}", params={"account_id": out}).status_code == 403
