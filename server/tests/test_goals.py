"""目标系统测试：建目标（framework 规则计算）、列表、viewer 三粒度过滤、sharing 更新、圈内公开目标。

运行：cd server && .venv-win/Scripts/python -m pytest tests/test_goals.py -v
"""
import os
import sys
import tempfile

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_goals_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _make_circle(client: TestClient, name: str = "目标测试圈"):
    """建圈 + 两名成员，返回 (circle_id, u1, u2)。"""
    circle = client.post("/api/circles", json={"name": name}).json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    return circle["id"], u1["user_id"], u2["user_id"]


def _outsider(client: TestClient) -> str:
    """圈外人：另建一个圈的创建者。"""
    return client.post("/api/circles", json={"name": "圈外人的圈", "nickname": "老周"}).json()["user_id"]


def _create_goal(client: TestClient, user_id: str, **over) -> dict:
    body = {
        "user_id": user_id,
        "type": "custom",
        "title": "每天背 20 个单词",
        "params": {},
        "answers": {},
        "visible_circle_ids": [],
        "detail_level": "summary",
    }
    body.update(over)
    r = client.post("/api/goals", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 建目标：framework 规则计算 ----------

def test_create_weight_loss_framework(client: TestClient) -> None:
    """减肥目标：framework 由 rules 算出（BMR×活动系数−缺口分摊），与纯函数口径一致。"""
    cid, u1, _u2 = _make_circle(client)
    r = client.post("/api/goals", json={
        "user_id": u1, "type": "weight_loss", "title": "瘦 5 公斤",
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
    cid, u1, _u2 = _make_circle(client, "三类目标圈")
    r = client.post("/api/goals", json={
        "user_id": u1, "type": "savings", "title": "攒首付",
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
        "user_id": u1, "type": "study", "title": "考研英语",
        "answers": {"daily_minutes": 45},
    })
    assert r.json()["framework"] == {"daily_minutes": 45}

    r = _create_goal(client, u1)
    assert r["framework"] == {}


def test_create_goal_defaults_when_answers_missing(client: TestClient) -> None:
    """问卷全跳过：走通用默认值并标 estimated，缺口为 0（无目标体重）。"""
    cid, u1, _u2 = _make_circle(client, "默认问卷圈")
    r = client.post("/api/goals", json={
        "user_id": u1, "type": "weight_loss", "title": "先动起来",
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
    """非法类型/空标题/非法粒度/公开到未加入的圈子/不存在用户 → 对应 4xx。"""
    cid, u1, u2 = _make_circle(client, "校验圈")
    base = {"user_id": u1, "type": "custom", "title": "x"}
    r = client.post("/api/goals", json={**base, "type": " Marathon ".strip()})
    assert r.status_code == 400  # 非四种类型之一
    assert client.post("/api/goals", json={**base, "title": "   "}).status_code == 400
    assert client.post("/api/goals", json={**base, "detail_level": "everything"}).status_code == 400
    # 只能公开到自己所在的圈子：别人建的圈不行
    other_circle = client.post("/api/circles", json={"name": "别人的圈"}).json()
    r = client.post("/api/goals", json={**base, "visible_circle_ids": [other_circle["id"]]})
    assert r.status_code == 400
    assert client.post("/api/goals", json={**base, "user_id": "ghost"}).status_code == 404


# ---------- 列表 ----------

def test_list_goals_owner_view(client: TestClient) -> None:
    """我的目标列表：owner 全量（含 params/answers/framework/progress），他人列表不含。"""
    cid, u1, u2 = _make_circle(client, "列表圈")
    g1 = _create_goal(client, u1, title="第一个目标")
    g2 = _create_goal(client, u1, title="第二个目标", type="study", answers={"daily_minutes": 30})

    r = client.get("/api/goals", params={"user_id": u1})
    assert r.status_code == 200, r.text
    goals = r.json()["goals"]
    mine = [g for g in goals if g["id"] in (g1["id"], g2["id"])]
    assert len(mine) == 2
    for g in mine:
        assert g["status"] == "active" and g["nudge_enabled"] is True
        assert isinstance(g["params"], dict) and isinstance(g["framework"], dict)
        assert set(g["progress"]) >= {"percent", "streak_days", "today_done", "today_total"}
    # 别人的列表里没有我的目标
    other = client.get("/api/goals", params={"user_id": u2}).json()["goals"]
    assert all(g["id"] not in (g1["id"], g2["id"]) for g in other)


# ---------- viewer 三粒度过滤 ----------

def test_get_goal_viewer_levels(client: TestClient) -> None:
    """owner 全量 / 圈友 summary 裁明细 / detail_level='detail' 给明细 / 非授权 404。"""
    cid, u1, u2 = _make_circle(client, "可见性圈")
    out = _outsider(client)
    g = _create_goal(
        client, u1, type="savings", title="私密账本",
        params={"target_fen": 500000, "saved_fen": 100000},
        answers={"fixed_income_fen": 900000},
    )
    gid = g["id"]

    # 私有目标：圈友与 outsider 都 404（不泄露存在性），owner 全量可见
    assert client.get(f"/api/goals/{gid}", params={"viewer_id": u2}).status_code == 404
    assert client.get(f"/api/goals/{gid}", params={"viewer_id": out}).status_code == 404
    d = client.get(f"/api/goals/{gid}", params={"viewer_id": u1}).json()
    assert d["params"]["target_fen"] == 500000 and d["answers"]["fixed_income_fen"] == 900000
    assert "framework" in d and "visible_circle_ids" in d

    # 公开到圈子（summary 粒度）：圈友可见但裁掉 params/answers/framework 等明细
    r = client.put(f"/api/goals/{gid}/sharing", json={
        "user_id": u1, "visible_circle_ids": [cid], "detail_level": "summary"})
    assert r.status_code == 200 and r.json()["visible_circle_ids"] == [cid]
    r = client.get(f"/api/goals/{gid}", params={"viewer_id": u2})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "私密账本" and d["owner_nickname"] == "阿澈"
    assert "params" not in d and "answers" not in d and "framework" not in d
    assert "progress" in d and d["viewer_nudged_today"] is False
    # outsider 依旧 404
    assert client.get(f"/api/goals/{gid}", params={"viewer_id": out}).status_code == 404

    # detail 粒度：圈友拿到明细（金额/问卷都可见）
    client.put(f"/api/goals/{gid}/sharing", json={
        "user_id": u1, "visible_circle_ids": [cid], "detail_level": "detail"})
    d = client.get(f"/api/goals/{gid}", params={"viewer_id": u2}).json()
    assert d["params"]["target_fen"] == 500000
    assert d["framework"]["monthly_save_fen"] > 0
    # 转回私有（空列表）：圈友立即回到 404
    client.put(f"/api/goals/{gid}/sharing", json={
        "user_id": u1, "visible_circle_ids": [], "detail_level": "summary"})
    assert client.get(f"/api/goals/{gid}", params={"viewer_id": u2}).status_code == 404


def test_update_sharing_authz(client: TestClient) -> None:
    """sharing 更新：仅 owner；粒度枚举校验；圈子白名单去重。"""
    cid, u1, u2 = _make_circle(client, "sharing 圈")
    gid = _create_goal(client, u1)["id"]
    r = client.put(f"/api/goals/{gid}/sharing", json={
        "user_id": u2, "visible_circle_ids": [cid], "detail_level": "summary"})
    assert r.status_code == 403  # 非 owner
    r = client.put(f"/api/goals/{gid}/sharing", json={
        "user_id": u1, "visible_circle_ids": [cid], "detail_level": "bogus"})
    assert r.status_code == 400
    r = client.put(f"/api/goals/{gid}/sharing", json={
        "user_id": u1, "visible_circle_ids": [cid, cid], "detail_level": "detail"})
    assert r.status_code == 200
    assert r.json()["visible_circle_ids"] == [cid]  # 去重
    assert r.json()["detail_level"] == "detail"
    assert client.put(f"/api/goals/missing/sharing", json={
        "user_id": u1, "visible_circle_ids": [], "detail_level": "summary"}).status_code == 404


# ---------- 圈内公开目标列表 ----------

def test_circle_goals_list(client: TestClient) -> None:
    """Wall「伙伴目标」：只列本圈内公开的目标，恒 summary 粒度；非成员 403。"""
    cid, u1, u2 = _make_circle(client, "伙伴目标圈")
    out = _outsider(client)
    pub = _create_goal(client, u1, title="公开flag", visible_circle_ids=[cid])
    _create_goal(client, u1, title="私有flag")  # 不出现
    # 别的圈子公开的目标不出现
    other_circle = client.post("/api/circles", json={"name": "隔壁圈", "nickname": "隔壁"}).json()
    client.post("/api/goals", json={
        "user_id": other_circle["user_id"], "type": "custom", "title": "隔壁的目标",
        "visible_circle_ids": [other_circle["id"]]})

    r = client.get(f"/api/goals/circle/{cid}", params={"viewer_id": u2})
    assert r.status_code == 200, r.text
    goals = r.json()["goals"]
    assert [g["title"] for g in goals] == ["公开flag"]
    g = goals[0]
    assert g["id"] == pub["id"] and g["owner_nickname"] == "阿澈"
    assert "params" not in g and "framework" not in g and "answers" not in g
    assert "progress" in g and g["detail_level"] == "summary"

    # owner 自己看本圈列表也能看到（同为成员）
    r = client.get(f"/api/goals/circle/{cid}", params={"viewer_id": u1})
    assert any(g["id"] == pub["id"] for g in r.json()["goals"])
    # 非成员 403
    assert client.get(f"/api/goals/circle/{cid}", params={"viewer_id": out}).status_code == 403
