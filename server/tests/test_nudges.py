"""鞭策测试（account 级）：正常发送、对人一日一次限频、按人屏蔽、按目标关闭、
未共享目标 404、圈友只见次数。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_nudges.py -v
"""
import os
import sqlite3
import sys
import tempfile
import uuid

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_nudges_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _register(client: TestClient) -> str:
    r = client.post("/api/auth/register", json={"username": f"u-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 200, r.text
    return r.json()["account_id"]


def _make_circle(client: TestClient, name: str = "鞭策测试圈"):
    """建圈 + 三名成员，返回 (circle_id, a1, a2, a3)（account_id）。"""
    accs = [_register(client) for _ in range(3)]
    circle = client.post("/api/circles", json={
        "name": name, "account_id": accs[0], "nickname": "阿澈"}).json()
    for acc, nick in zip(accs[1:], ("丫丫", "老周")):
        client.post("/api/circles/join", json={
            "invite_code": circle["invite_code"], "account_id": acc, "nickname": nick})
    return circle["id"], *accs


def _shared_goal(client: TestClient, account_id: str, circle_id: str, title: str = "每日早起") -> str:
    r = client.post("/api/goals", json={
        "account_id": account_id, "type": "custom", "title": title})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    r = client.put("/api/self/sharing", json={
        "account_id": account_id, "circle_id": circle_id, "category": "goal", "level": "progress"})
    assert r.status_code == 200, r.text
    return gid


# ---------- 正常发送 + 列表口径 ----------

def test_send_nudge_ok_and_list_visibility(client: TestClient) -> None:
    """圈友鞭策 200；owner 见留言与昵称；圈友只见 count 不见留言内容。"""
    cid, a1, a2, a3 = _make_circle(client)
    gid = _shared_goal(client, a1, cid)

    r = client.post(f"/api/goals/{gid}/nudges", json={"account_id": a2, "message": "别躺了，起来嗨"})
    assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["id"]

    # owner：全见留言（含发送者昵称）
    body = client.get(f"/api/goals/{gid}/nudges", params={"account_id": a1}).json()
    assert body["count"] == 1 and len(body["nudges"]) == 1
    n = body["nudges"][0]
    assert n["message"] == "别躺了，起来嗨" and n["from_nickname"] == "丫丫"
    assert n["from_account_id"] == a2

    # 圈友（含发送者本人与旁观者）：只见次数，nudges 恒空——留言不外泄
    for viewer in (a2, a3):
        body = client.get(f"/api/goals/{gid}/nudges", params={"account_id": viewer}).json()
        assert body["count"] == 1 and body["nudges"] == []

    # 圈友视角目标详情带出「今日已鞭策」置灰状态
    d = client.get(f"/api/goals/{gid}", params={"account_id": a2}).json()
    assert d["viewer_nudged_today"] is True
    d = client.get(f"/api/goals/{gid}", params={"account_id": a3}).json()
    assert d["viewer_nudged_today"] is False


# ---------- 限频：对人不对目标，一日一次 ----------

def test_second_nudge_same_day_429(client: TestClient) -> None:
    """同一 from→to 当日第二次 429——换同一个人的另一个目标也照样 429（对人不对目标）。"""
    cid, a1, a2, _a3 = _make_circle(client, "限频圈")
    g1 = _shared_goal(client, a1, cid, "目标甲")
    g2 = _shared_goal(client, a1, cid, "目标乙")

    r = client.post(f"/api/goals/{g1}/nudges", json={"account_id": a2, "message": "冲"})
    assert r.status_code == 200
    r = client.post(f"/api/goals/{g1}/nudges", json={"account_id": a2, "message": "再冲"})
    assert r.status_code == 429 and "已经鞭策过" in r.json()["detail"]
    # 限频按人对：同日的另一个目标同样被挡
    assert client.post(f"/api/goals/{g2}/nudges", json={"account_id": a2}).status_code == 429

    # 落库只有一条
    conn = _db()
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM nudges WHERE from_account_id = ? AND to_account_id = ?",
        (a2, a1),
    ).fetchone()["c"]
    conn.close()
    assert cnt == 1


# ---------- 屏蔽与关闭 ----------

def test_block_user_403(client: TestClient) -> None:
    """按人屏蔽（account 级）：屏蔽后 403；屏蔽幂等不插重复行；自我屏蔽 400。"""
    cid, a1, a2, _a3 = _make_circle(client, "屏蔽圈")
    gid = _shared_goal(client, a1, cid)

    assert client.post("/api/nudge-blocks", json={
        "account_id": a1, "blocked_account_id": a1}).status_code == 400  # 屏蔽自己不合法
    r = client.post("/api/nudge-blocks", json={"account_id": a1, "blocked_account_id": a2})
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    # 幂等：重复屏蔽返回一致且不插重复行
    assert client.post("/api/nudge-blocks", json={
        "account_id": a1, "blocked_account_id": a2}).status_code == 200
    conn = _db()
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM nudge_blocks WHERE account_id = ? AND blocked_account_id = ?",
        (a1, a2),
    ).fetchone()["c"]
    conn.close()
    assert cnt == 1

    r = client.post(f"/api/goals/{gid}/nudges", json={"account_id": a2, "message": "在吗"})
    assert r.status_code == 403 and "屏蔽" in r.json()["detail"]


def test_nudge_disabled_403(client: TestClient) -> None:
    """按目标关闭鞭策后 403；重新打开恢复 200；toggle 仅 owner 可用。"""
    cid, a1, a2, _a3 = _make_circle(client, "关闭鞭策圈")
    gid = _shared_goal(client, a1, cid)

    assert client.post(f"/api/goals/{gid}/nudge-toggle", json={
        "account_id": a2, "enabled": False}).status_code == 403  # 非 owner
    r = client.post(f"/api/goals/{gid}/nudge-toggle", json={"account_id": a1, "enabled": False})
    assert r.status_code == 200 and r.json()["nudge_enabled"] is False

    r = client.post(f"/api/goals/{gid}/nudges", json={"account_id": a2, "message": "早"})
    assert r.status_code == 403 and "关闭" in r.json()["detail"]

    r = client.post(f"/api/goals/{gid}/nudge-toggle", json={"account_id": a1, "enabled": True})
    assert r.json()["nudge_enabled"] is True
    assert client.post(f"/api/goals/{gid}/nudges", json={"account_id": a2}).status_code == 200


# ---------- 不可见目标 ----------

def test_private_or_invisible_goal_404(client: TestClient) -> None:
    """未共享目标鞭策/列表一律 404（不泄露存在性）；圈外人对共享目标同样 404；自我鞭策 400。"""
    cid, a1, a2, _a3 = _make_circle(client, "不可见圈")
    # 未共享目标
    r = client.post("/api/goals", json={"account_id": a1, "type": "custom", "title": "私房目标"})
    priv = r.json()["id"]
    assert client.post(f"/api/goals/{priv}/nudges", json={"account_id": a2}).status_code == 404
    assert client.get(f"/api/goals/{priv}/nudges", params={"account_id": a2}).status_code == 404

    # 共享目标：别的圈子的人（无共同圈子）也 404
    gid = _shared_goal(client, a1, cid)
    outsider = _register(client)
    client.post("/api/circles", json={"name": "外人圈", "account_id": outsider, "nickname": "路人"})
    assert client.post(f"/api/goals/{gid}/nudges", json={"account_id": outsider}).status_code == 404
    assert client.get(f"/api/goals/{gid}/nudges", params={"account_id": outsider}).status_code == 404

    # 不能鞭策自己（owner 对自己目标）
    assert client.post(f"/api/goals/{gid}/nudges", json={"account_id": a1}).status_code == 400
    # 目标不存在
    assert client.post("/api/goals/ghost/nudges", json={"account_id": a2}).status_code == 404
