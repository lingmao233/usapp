"""每日计划测试：懒生成收敛、条目 CRUD 与越权、无目标自定义条目、昨日未完成进生成上下文。

运行：cd server && .venv-win/Scripts/python -m pytest tests/test_daily_plans.py -v
"""
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_daily_plans_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import plans as svc  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    """测试线程直读数据库（WAL 允许多连接一读一写）。"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _new_user(client: TestClient, name: str = "计划测试圈") -> str:
    """注册账号并建圈，返回 account_id（Self 数据全部挂在账号级）。"""
    acc = client.post(
        "/api/auth/register", json={"username": f"u-{uuid.uuid4().hex[:8]}"}
    ).json()["account_id"]
    client.post("/api/circles", json={"name": name, "account_id": acc, "nickname": "阿澈"})
    return acc


def _create_goal(client: TestClient, account_id: str, **over) -> str:
    body = {"account_id": account_id, "type": "custom", "title": "目标", "params": {}, "answers": {}}
    body.update(over)
    r = client.post("/api/goals", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------- 懒生成 ----------

def test_today_lazy_generation_converges(client: TestClient) -> None:
    """首次拉取 generating=trigger（路由改写 True）→ 背景任务收敛出 mock 条目 → 再拉不重复生成。"""
    uid = _new_user(client)
    # 无目标：不触发，空清单
    r = client.get("/api/plans/today", params={"account_id": uid})
    assert r.status_code == 200 and r.json()["generating"] is False and r.json()["items"] == []

    _create_goal(client, uid, type="study", title="考研英语", answers={"daily_minutes": 45})

    # 有 active 目标且无 AI 条目 → 触发懒生成；TestClient 内联跑完 BackgroundTasks
    r = client.get("/api/plans/today", params={"account_id": uid})
    assert r.json()["generating"] is True and r.json()["items"] == []

    # 收敛：mock 桩按 study 模板出 2 条（daily_minutes=45 进文案），source='ai'
    r = client.get("/api/plans/today", params={"account_id": uid})
    body = r.json()
    assert body["generating"] is False
    contents = [i["content"] for i in body["items"]]
    assert "专注学习 45 分钟" in contents
    assert "回顾昨天学的内容，花 10 分钟过一遍" in contents
    assert all(i["source"] == "ai" and i["done"] is False for i in body["items"])
    kinds = {i["content"]: i["kind"] for i in body["items"]}
    assert kinds["专注学习 45 分钟"] == "daily"

    # 幂等：已有 AI 条目不再触发，条目数不膨胀
    r = client.get("/api/plans/today", params={"account_id": uid})
    assert r.json()["generating"] is False and len(r.json()["items"]) == 2


def test_generate_today_direct_idempotent(client: TestClient) -> None:
    """service 直调：generated → exists（不重插）。"""
    uid = _new_user(client, "直调圈")
    _create_goal(client, uid)
    assert svc.generate_today(uid)["status"] == "generated"
    assert svc.generate_today(uid)["status"] == "exists"
    assert svc.today(uid)["generating"] is False  # cache 旗标已清


# ---------- 条目 CRUD 与越权 ----------

def test_item_crud_and_authz(client: TestClient) -> None:
    """打勾/编辑/删除正常流转；他人改/删 403；校验失败 400/404。"""
    uid = _new_user(client, "CRUD 圈")
    other = _new_user(client, "CRUD 圈外人")

    r = client.post("/api/plans/items", json={"account_id": uid, "content": "背 50 个单词"})
    assert r.status_code == 200, r.text
    item_id = r.json()["id"]

    # 打勾 + 改内容
    r = client.put(f"/api/plans/items/{item_id}", json={"account_id": uid, "done": True})
    assert r.status_code == 200
    r = client.put(f"/api/plans/items/{item_id}", json={"account_id": uid, "content": "背 80 个单词"})
    assert r.status_code == 200
    items = client.get("/api/plans/today", params={"account_id": uid}).json()["items"]
    mine = [i for i in items if i["id"] == item_id]
    assert mine and mine[0]["done"] is True and mine[0]["content"] == "背 80 个单词"
    assert mine[0]["source"] == "custom" and mine[0]["goal_id"] is None

    # 越权：改/删别人的条目 403
    assert client.put(
        f"/api/plans/items/{item_id}", json={"account_id": other, "done": False}).status_code == 403
    assert client.delete(
        f"/api/plans/items/{item_id}", params={"account_id": other}).status_code == 403
    # 不存在的条目
    assert client.put(
        "/api/plans/items/ghost", json={"account_id": uid, "done": True}).status_code == 404

    # 校验：空内容 / 非法 kind / 非法日期 / 关联别人的目标 / 关联不存在的目标
    assert client.post("/api/plans/items", json={"account_id": uid, "content": "  "}).status_code == 400
    assert client.post(
        "/api/plans/items", json={"account_id": uid, "content": "x", "kind": "weekly"}).status_code == 400
    assert client.post(
        "/api/plans/items", json={"account_id": uid, "content": "x", "date": "08-16"}).status_code == 400
    other_goal = _create_goal(client, other, title="别人的目标")
    assert client.post("/api/plans/items", json={
        "account_id": uid, "content": "x", "goal_id": other_goal}).status_code == 403
    assert client.post("/api/plans/items", json={
        "account_id": uid, "content": "x", "goal_id": "ghost"}).status_code == 404

    # 删除：owner 删完从清单消失
    assert client.delete(f"/api/plans/items/{item_id}", params={"account_id": uid}).status_code == 200
    items = client.get("/api/plans/today", params={"account_id": uid}).json()["items"]
    assert all(i["id"] != item_id for i in items)


def test_custom_item_without_goal(client: TestClient) -> None:
    """无目标也可用：自定义条目 goal_id 为 NULL；无目标不触发懒生成。"""
    uid = _new_user(client, "无目标圈")
    r = client.post("/api/plans/items", json={
        "account_id": uid, "content": "给阳台的花浇水", "kind": "habit", "date": date.today().isoformat()})
    assert r.status_code == 200
    body = client.get("/api/plans/today", params={"account_id": uid}).json()
    assert body["generating"] is False  # 没有 active 目标
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["content"] == "给阳台的花浇水" and item["kind"] == "habit"
    assert item["goal_id"] is None and item["source"] == "custom"


# ---------- 昨日未完成进生成上下文 ----------

def test_yesterday_unfinished_in_context(client: TestClient, monkeypatch) -> None:
    """昨日未完成条目进生成上下文（context.yesterday）；已完成的与别的目标的不进。"""
    uid = _new_user(client, "昨日上下文圈")
    gid = _create_goal(client, uid, title="跑步目标")
    other_gid = _create_goal(client, uid, title="另一个目标")
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    conn = _db()
    rows = [
        (gid, "昨天没跑完的 5 公里", 0),      # 应进 context
        (gid, "昨天已完成的拉伸", 1),          # done=1 不进
        (other_gid, "别的目标的欠账", 0),      # 不属于本目标，不进本目标 context
    ]
    for goal_id, content, done in rows:
        conn.execute(
            """INSERT INTO plan_items (id, account_id, goal_id, date, content, kind, source, done, created_at)
               VALUES (?, ?, ?, ?, ?, 'task', 'ai', ?, ?)""",
            (uuid.uuid4().hex[:12], uid, goal_id, yesterday, content, done,
             datetime.now().isoformat(timespec="seconds")),
        )
    conn.commit()
    conn.close()

    captured: list[dict] = []

    def fake_generate(goal_type, framework, context):
        captured.append({"goal_type": goal_type, "framework": framework, "context": context})
        return []

    monkeypatch.setattr(ai, "generate_daily_plan", fake_generate)
    assert svc.generate_today(uid)["status"] == "generated"

    assert len(captured) == 2  # 两个 active 目标各一次
    # 两个目标同为 custom 类型，按 captured 顺序无法区分，改按 yesterday 内容区分
    hit = [c["context"] for c in captured if "昨天没跑完的 5 公里" in c["context"]["yesterday"]]
    assert len(hit) == 1
    ctx = hit[0]
    assert "昨天已完成的拉伸" not in ctx["yesterday"]
    assert "别的目标的欠账" not in ctx["yesterday"]
    assert "progress" in ctx  # 进度键恒在（可为空串）
    # 另一个目标的 context 里是自己的欠账
    other_ctx = [c["context"] for c in captured if "别的目标的欠账" in c["context"]["yesterday"]]
    assert len(other_ctx) == 1 and "昨天没跑完" not in other_ctx[0]["yesterday"]
