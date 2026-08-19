"""今日计划鞭策测试（account 级）：正常发送、自我鞭策 400、非圈子成员 403、
未共享计划 404、被屏蔽 403、目标+计划合并限频 429、owner 查留言/他人不可见。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_plan_nudges.py -v
"""
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import date

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_plan_nudges_"), "test.db")
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

TODAY = date.today().isoformat()


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


def _make_circle(client: TestClient, name: str = "计划鞭策圈"):
    """建圈 + 三名成员，返回 (circle_id, a1, a2, a3)（account_id）。"""
    accs = [_register(client) for _ in range(3)]
    circle = client.post("/api/circles", json={
        "name": name, "account_id": accs[0], "nickname": "阿澈"}).json()
    for acc, nick in zip(accs[1:], ("丫丫", "老周")):
        client.post("/api/circles/join", json={
            "invite_code": circle["invite_code"], "account_id": acc, "nickname": nick})
    return circle["id"], *accs


def _share_plan(client: TestClient, account_id: str, circle_id: str, level: str = "detail") -> None:
    r = client.put("/api/self/sharing", json={
        "account_id": account_id, "circle_id": circle_id, "category": "plan", "level": level})
    assert r.status_code == 200, r.text


def _shared_goal(client: TestClient, account_id: str, circle_id: str, title: str = "每日早起") -> str:
    r = client.post("/api/goals", json={
        "account_id": account_id, "type": "custom", "title": title})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    r = client.put("/api/self/sharing", json={
        "account_id": account_id, "circle_id": circle_id, "category": "goal", "level": "progress"})
    assert r.status_code == 200, r.text
    return gid


def _nudge_plan(client: TestClient, frm: str, to: str, circle_id: str, message: str = ""):
    return client.post("/api/plans/nudge", json={
        "account_id": frm, "to_account_id": to, "circle_id": circle_id, "message": message})


# ---------- 正常发送 + owner 列表 ----------

def test_send_plan_nudge_ok_and_owner_list(client: TestClient) -> None:
    """圈友计划鞭策 200；落库 goal_id 为空、plan_date 为当天；owner 查留言含圈内昵称。"""
    cid, a1, a2, _a3 = _make_circle(client)
    _share_plan(client, a1, cid)

    r = _nudge_plan(client, a2, a1, cid, "今天的计划别又鸽了")
    assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["id"]

    # 落库形态：计划鞭策 = goal_id NULL + plan_date=当天
    conn = _db()
    row = conn.execute(
        "SELECT goal_id, plan_date, message FROM nudges WHERE from_account_id = ? AND to_account_id = ?",
        (a2, a1),
    ).fetchone()
    conn.close()
    assert row is not None and row["goal_id"] is None and row["plan_date"] == TODAY
    assert row["message"] == "今天的计划别又鸽了"

    # owner 查当天：全见留言（发送者用圈内昵称「丫丫」）
    body = client.get("/api/plans/nudges", params={"account_id": a1, "date": TODAY}).json()
    assert body["count"] == 1 and len(body["nudges"]) == 1
    n = body["nudges"][0]
    assert n["message"] == "今天的计划别又鸽了" and n["from_nickname"] == "丫丫"
    assert n["from_account_id"] == a2 and n["plan_date"] == TODAY


def test_plan_nudge_list_others_invisible(client: TestClient) -> None:
    """他人不可见：接口只查本人收件箱——别人查不到我收到的留言；不存在的账号 404。"""
    cid, a1, a2, a3 = _make_circle(client, "计划鞭策隐私圈")
    _share_plan(client, a1, cid)
    assert _nudge_plan(client, a2, a1, cid, "冲").status_code == 200

    # 发送者 a2 与旁观者 a3 查自己当天：都看不到 a1 收到的留言
    for viewer in (a2, a3):
        body = client.get("/api/plans/nudges", params={"account_id": viewer, "date": TODAY}).json()
        assert body["count"] == 0 and body["nudges"] == []

    # 不存在的账号 404
    assert client.get("/api/plans/nudges", params={
        "account_id": "ghost", "date": TODAY}).status_code == 404


# ---------- 校验链 ----------

def test_nudge_self_400(client: TestClient) -> None:
    """不能鞭策自己（to 存在且是自己）→ 400。"""
    cid, a1, _a2, _a3 = _make_circle(client, "自我鞭策圈")
    _share_plan(client, a1, cid)
    r = _nudge_plan(client, a1, a1, cid)
    assert r.status_code == 400 and "自己" in r.json()["detail"]


def test_non_member_403(client: TestClient) -> None:
    """发起者不在圈里 403；对方不在圈里同样 403。"""
    cid, a1, a2, _a3 = _make_circle(client, "成员校验圈")
    _share_plan(client, a1, cid)

    outsider = _register(client)
    client.post("/api/circles", json={"name": "外人圈", "account_id": outsider, "nickname": "路人"})

    r = _nudge_plan(client, outsider, a1, cid)
    assert r.status_code == 403 and "圈子" in r.json()["detail"]
    r = _nudge_plan(client, a2, outsider, cid)
    assert r.status_code == 403 and "圈子" in r.json()["detail"]
    # 圈子本身不存在 404
    assert _nudge_plan(client, a2, a1, "ghost-circle").status_code == 404
    # to 账号不存在 404
    assert _nudge_plan(client, a2, "ghost", cid).status_code == 404


def test_plan_not_shared_404(client: TestClient) -> None:
    """未共享 plan 类别一律 404（不泄露）；只共享到对方不在的圈子同样 404。"""
    cid, a1, a2, _a3 = _make_circle(client, "未共享计划圈")
    # 完全没共享 → 404
    assert _nudge_plan(client, a2, a1, cid).status_code == 404

    # 只共享到 a2 不在的另一个圈子 → share_level 判空 → 404
    solo = client.post("/api/circles", json={
        "name": "私房圈", "account_id": a1, "nickname": "阿澈"}).json()
    _share_plan(client, a1, solo["id"])
    assert _nudge_plan(client, a2, a1, cid).status_code == 404


def test_blocked_403(client: TestClient) -> None:
    """被 owner 按人屏蔽后 403（与目标鞭策同一份屏蔽名单）。"""
    cid, a1, a2, _a3 = _make_circle(client, "计划屏蔽圈")
    _share_plan(client, a1, cid)
    r = client.post("/api/nudge-blocks", json={"account_id": a1, "blocked_account_id": a2})
    assert r.status_code == 200
    r = _nudge_plan(client, a2, a1, cid, "在吗")
    assert r.status_code == 403 and "屏蔽" in r.json()["detail"]


# ---------- 合并限频：目标+计划对人一天一次 ----------

def test_goal_then_plan_nudge_same_day_429(client: TestClient) -> None:
    """先发目标鞭策，当天再发计划鞭策必须 429（对人不对类型）。"""
    cid, a1, a2, _a3 = _make_circle(client, "合并限频圈甲")
    gid = _shared_goal(client, a1, cid)
    _share_plan(client, a1, cid)

    r = client.post(f"/api/goals/{gid}/nudges", json={"account_id": a2, "message": "先目标"})
    assert r.status_code == 200
    r = _nudge_plan(client, a2, a1, cid, "再计划")
    assert r.status_code == 429 and "已经鞭策过" in r.json()["detail"]

    # 落库只有目标鞭策一条
    conn = _db()
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM nudges WHERE from_account_id = ? AND to_account_id = ?",
        (a2, a1),
    ).fetchone()["c"]
    conn.close()
    assert cnt == 1


def test_plan_then_goal_nudge_same_day_429(client: TestClient) -> None:
    """先发计划鞭策，当天再发目标鞭策同样 429（反向亦合并）。"""
    cid, a1, a2, _a3 = _make_circle(client, "合并限频圈乙")
    gid = _shared_goal(client, a1, cid)
    _share_plan(client, a1, cid)

    assert _nudge_plan(client, a2, a1, cid, "先计划").status_code == 200
    r = client.post(f"/api/goals/{gid}/nudges", json={"account_id": a2, "message": "再目标"})
    assert r.status_code == 429 and "已经鞭策过" in r.json()["detail"]

    # 计划鞭策当天第二次也 429
    assert _nudge_plan(client, a2, a1, cid).status_code == 429

    conn = _db()
    cnt = conn.execute(
        "SELECT COUNT(*) AS c FROM nudges WHERE from_account_id = ? AND to_account_id = ?",
        (a2, a1),
    ).fetchone()["c"]
    conn.close()
    assert cnt == 1
