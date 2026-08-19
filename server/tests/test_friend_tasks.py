"""朋友任务聚合接口测试：按人分组、只返回已共享类别、progress/detail 两档裁剪、
未共享不可见、鞭策状态（viewer 当日是否已鞭策，对人不对目标）、非成员 403。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_friend_tasks.py -v
"""
import os
import sys
import tempfile
import uuid

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_friendtasks_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
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


def _make_circle(client: TestClient, name: str, members: int = 2):
    """建圈 + members 个成员账号，返回 (circle_id, [account_id...])。"""
    accs = [_register(client) for _ in range(members + 1)]
    r = client.post("/api/circles", json={"name": name, "account_id": accs[0], "nickname": "成员0"})
    assert r.status_code == 200, r.text
    circle = r.json()
    for i, acc in enumerate(accs[1:], 1):
        r = client.post("/api/circles/join", json={
            "invite_code": circle["invite_code"], "account_id": acc, "nickname": f"成员{i}"})
        assert r.status_code == 200, r.text
    return circle["id"], accs


def _share(client: TestClient, account_id: str, circle_id: str, category: str, level: str | None = None) -> None:
    body = {"account_id": account_id, "circle_id": circle_id, "category": category}
    if level:
        body["level"] = level
    r = client.put("/api/self/sharing", json=body)
    assert r.status_code == 200, r.text


def _goal(client: TestClient, account_id: str, title: str, **over) -> str:
    body = {"account_id": account_id, "type": "custom", "title": title}
    body.update(over)
    r = client.post("/api/goals", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_friend_tasks_grouping_and_levels(client: TestClient) -> None:
    """A 共享 goal=detail + plan=progress；B 只共享 goal=progress；C 不共享（不出现）。"""
    cid, (a, b, c) = _make_circle(client, "朋友任务圈", members=2)

    g_a = _goal(client, a, "A 的存款目标", type="savings",
                 params={"target_fen": 500000}, answers={"fixed_income_fen": 900000})
    _share(client, a, cid, "goal", "detail")
    _share(client, a, cid, "plan")  # 默认 progress
    client.post("/api/plans/items", json={"account_id": a, "content": "记账一笔"})
    r = client.post("/api/plans/items", json={"account_id": a, "content": "存 50 元"})
    client.put(f"/api/plans/items/{r.json()['id']}", json={"account_id": a, "done": True})

    g_b = _goal(client, b, "B 的私密体重", type="weight_loss",
                answers={"weight_kg": 70, "height_cm": 170})
    _share(client, b, cid, "goal", "progress")

    # viewer=A：只看到 B；B 的目标按 progress 裁剪；无 plan 键；未鞭策过
    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": a}).json()
    assert body["circle_id"] == cid and body["date"]
    assert [m["account_id"] for m in body["members"]] == [b]
    m = body["members"][0]
    assert m["nickname"] == "成员1" and m["viewer_nudged_today"] is False
    assert "plan" not in m
    g = m["goals"][0]
    assert g["id"] == g_b and g["share_level"] == "progress"
    assert set(g["progress"]) >= {"percent", "streak_days", "today_done", "today_total"}
    assert "params" not in g and "answers" not in g and "framework" not in g

    # viewer=B：看到 A 的卡；goal=detail 有明细；plan=progress 只有计数没条目
    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": b}).json()
    assert [m["account_id"] for m in body["members"]] == [a]
    m = body["members"][0]
    g = m["goals"][0]
    assert g["id"] == g_a and g["share_level"] == "detail"
    assert g["params"]["target_fen"] == 500000 and g["answers"]["fixed_income_fen"] == 900000
    assert g["framework"]["monthly_save_fen"] > 0
    p = m["plan"]
    assert p["share_level"] == "progress" and "items" not in p
    assert p["today_done"] == 1 and p["today_total"] == 2

    # viewer=C（不共享的人也能看别人的共享）：看到 A、B 两张卡，自己的卡不在任何响应里
    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": c}).json()
    assert {m["account_id"] for m in body["members"]} == {a, b}
    assert all(m["account_id"] != c for m in body["members"])


def test_friend_tasks_plan_detail_level(client: TestClient) -> None:
    """plan=detail：圈友拿到今日条目明细（内容/打勾状态）。"""
    cid, (a, b) = _make_circle(client, "计划明细圈", members=1)
    _share(client, a, cid, "plan", "detail")
    client.post("/api/plans/items", json={"account_id": a, "content": "背 30 个单词", "kind": "daily"})

    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": b}).json()
    p = body["members"][0]["plan"]
    assert p["share_level"] == "detail"
    assert [i["content"] for i in p["items"]] == ["背 30 个单词"]
    assert p["items"][0]["kind"] == "daily" and p["items"][0]["done"] is False
    assert p["today_total"] == 1 and p["today_done"] == 0


def test_friend_tasks_nudge_status(client: TestClient) -> None:
    """鞭策后 viewer_nudged_today 置 True（对人不对目标）；别人视角不受影响。"""
    cid, (a, b, c) = _make_circle(client, "鞭策状态圈", members=2)
    g_b = _goal(client, b, "B 的目标")
    _share(client, b, cid, "goal", "progress")

    r = client.post(f"/api/goals/{g_b}/nudges", json={"account_id": a, "message": "冲"})
    assert r.status_code == 200, r.text

    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": a}).json()
    m = [m for m in body["members"] if m["account_id"] == b][0]
    assert m["viewer_nudged_today"] is True
    # 旁观者 C 当日没鞭策过 B
    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": c}).json()
    m = [m for m in body["members"] if m["account_id"] == b][0]
    assert m["viewer_nudged_today"] is False


def test_friend_tasks_unshared_invisible_and_authz(client: TestClient) -> None:
    """未共享任何类别的人不出现；目标转回未共享立刻消失；非本圈成员 403；圈子不存在 404。"""
    cid, (a, b) = _make_circle(client, "隐私圈", members=1)
    _goal(client, a, "A 的私房目标")  # 建了目标但不共享
    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": b}).json()
    assert body["members"] == []

    # 开了又关：立刻不可见
    _share(client, a, cid, "goal")
    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": b}).json()
    assert len(body["members"]) == 1
    client.delete("/api/self/sharing", params={
        "account_id": a, "circle_id": cid, "category": "goal"})
    body = client.get(f"/api/circles/{cid}/friend-tasks", params={"account_id": b}).json()
    assert body["members"] == []

    # 非成员 403；圈子不存在 404
    outsider = _register(client)
    assert client.get(
        f"/api/circles/{cid}/friend-tasks", params={"account_id": outsider}).status_code == 403
    assert client.get(
        "/api/circles/ghost/friend-tasks", params={"account_id": b}).status_code == 404
