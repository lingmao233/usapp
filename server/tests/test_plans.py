"""共同愿望制度 + 方案（真实数据/缓存/预生成/追问）测试。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_plans.py -v
"""
import json
import os
import sqlite3
import sys
import tempfile

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_plan_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import nightly  # noqa: E402
from app.services import wishes as wishes_svc  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    """测试线程直读数据库（WAL 允许多连接一读一写）。

    用 settings.DB_PATH 而不是环境变量：多测试模块同进程时 settings 以首次 import 为准。
    """
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _make_circle(client: TestClient):
    """建一个两人的测试圈，返回 (circle_id, u1, u2)。"""
    r = client.post("/api/circles", json={"name": "方案测试圈"})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    return circle["id"], u1, u2


def _add_wish(client: TestClient, cid: str, uid: str, content: str) -> str:
    """手动加愿望（后台向量化在 TestClient 内同步跑完，返回时向量已就绪）。"""
    r = client.post("/api/wishes", json={"circle_id": cid, "user_id": uid, "content": content})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _common(client: TestClient, cid: str) -> list:
    """拉共同愿望：stale-while-revalidate 下首轮可能返回旧结果 + refreshing，轮询到刷新完成。

    TestClient 会同步执行 BackgroundTasks，第二轮必为新鲜结果。
    """
    for _ in range(20):
        r = client.get("/api/wishes/common", params={"circle_id": cid}).json()
        if not r.get("refreshing"):
            return r["common_wishes"]
    raise AssertionError("共同愿望刷新未收敛")


# ---------- 勾选完成 ↔ 匹配池 ----------

def test_done_toggle_excludes_from_pool(client: TestClient) -> None:
    """勾选完成 → 移出共同愿望匹配池；取消勾选 → 回到池子（可逆）。"""
    cid, u1, u2 = _make_circle(client)
    w1 = _add_wish(client, cid, u1["user_id"], "想去露营看星星")
    _add_wish(client, cid, u2["user_id"], "想去露营看星星")
    assert any("露营" in c["content"] for c in _common(client, cid))

    r = client.put(f"/api/wishes/{w1}/done", json={"user_id": u1["user_id"], "done": True})
    assert r.status_code == 200 and r.json()["status"] == "done"
    assert all("露营" not in c["content"] for c in _common(client, cid))

    r = client.put(f"/api/wishes/{w1}/done", json={"user_id": u1["user_id"], "done": False})
    assert r.json()["status"] == "active"
    assert any("露营" in c["content"] for c in _common(client, cid))

    # 他人不能替你勾选
    r = client.put(f"/api/wishes/{w1}/done", json={"user_id": u2["user_id"], "done": True})
    assert r.status_code == 403


def test_generate_plan_keeps_wish_active(client: TestClient) -> None:
    """BUG-004 回归：生成方案不再把愿望踢出匹配池，共同愿望继续展示。"""
    cid, u1, u2 = _make_circle(client)
    w1 = _add_wish(client, cid, u1["user_id"], "想去海边看日出")
    _add_wish(client, cid, u2["user_id"], "想去海边看日出")

    result = wishes_svc.generate_plan(w1)
    assert result["plan"]["steps"]  # mock 模板方案

    db = _db()
    row = db.execute("SELECT status, plan FROM wishes WHERE id = ?", (w1,)).fetchone()
    db.close()
    assert row["status"] == "active"  # 不再翻成 matched
    plan = json.loads(row["plan"])
    assert plan["steps"] and "participants" in plan  # 参与人随方案落库
    # 共同愿望仍在
    assert any("海边" in c["content"] for c in _common(client, cid))


# ---------- 圈级匹配缓存 ----------

def test_common_wishes_cache_and_fingerprint_invalidation(client: TestClient) -> None:
    """第一次现算落缓存；指纹不变走缓存（不进任务层）；愿望变化指纹变 → 自动重算。"""
    cid, u1, u2 = _make_circle(client)
    _add_wish(client, cid, u1["user_id"], "想去雪山泡温泉")
    _add_wish(client, cid, u2["user_id"], "想去雪山泡温泉")

    first = _common(client, cid)
    assert any("温泉" in c["content"] for c in first)

    db = _db()
    cache_row = db.execute(
        "SELECT fingerprint FROM common_wishes_cache WHERE circle_id = ?", (cid,)
    ).fetchone()
    runs_after_first = db.execute(
        "SELECT COUNT(*) AS c FROM task_runs WHERE task_name='common_wishes' AND entity_id=?", (cid,)
    ).fetchone()["c"]
    db.close()
    assert cache_row is not None

    # 指纹不变：第二次读取命中缓存，不再进任务层重算
    assert _common(client, cid) == first
    db = _db()
    runs_after_second = db.execute(
        "SELECT COUNT(*) AS c FROM task_runs WHERE task_name='common_wishes' AND entity_id=?", (cid,)
    ).fetchone()["c"]
    db.close()
    assert runs_after_second == runs_after_first

    # 愿望变化 → 指纹变化 → 自动重算（任务层计数 +1），结果随之更新
    w3 = _add_wish(client, cid, u1["user_id"], "想去看展")
    _add_wish(client, cid, u2["user_id"], "想去看展")
    third = _common(client, cid)
    assert any("看展" in c["content"] for c in third)
    db = _db()
    runs_after_change = db.execute(
        "SELECT COUNT(*) AS c FROM task_runs WHERE task_name='common_wishes' AND entity_id=?", (cid,)
    ).fetchone()["c"]
    db.close()
    assert runs_after_change == runs_after_first + 1

    # 勾选完成同样触发重算并把愿望移出池子
    client.put(f"/api/wishes/{w3}/done", json={"user_id": u1["user_id"], "done": True})
    assert all("看展" not in c["content"] for c in _common(client, cid))


def test_common_wishes_stale_while_revalidate(client: TestClient) -> None:
    """指纹变化时接口不阻塞：先返回旧结果（refreshing=True），后台重算后第二轮拿到新结果。"""
    cid, u1, u2 = _make_circle(client)
    _add_wish(client, cid, u1["user_id"], "想去露营看星星")
    _add_wish(client, cid, u2["user_id"], "想去露营看星星")
    first = _common(client, cid)
    assert any("露营" in c["content"] for c in first)

    # 新愿望入池 → 指纹变：首轮返回旧结果并标记 refreshing（不阻塞等待 LLM）
    _add_wish(client, cid, u1["user_id"], "想去潜水")
    _add_wish(client, cid, u2["user_id"], "想去潜水")
    r1 = client.get("/api/wishes/common", params={"circle_id": cid}).json()
    assert r1["refreshing"] is True
    assert r1["common_wishes"] == first
    # TestClient 同步跑完后台重算：第二轮即为新鲜结果
    r2 = client.get("/api/wishes/common", params={"circle_id": cid}).json()
    assert r2["refreshing"] is False
    assert any("潜水" in c["content"] for c in r2["common_wishes"])


# ---------- 每晚预生成 ----------

def test_nightly_pregenerates_plans(client: TestClient) -> None:
    """nightly 顺带为没有方案的共同愿望预生成：wishes.plan 落库且含参与人。"""
    cid, u1, u2 = _make_circle(client)
    w1 = _add_wish(client, cid, u1["user_id"], "想去骑行环城")
    _add_wish(client, cid, u2["user_id"], "想去骑行环城")

    stats = nightly.run()
    assert stats.get("success", 0) >= 1
    db = _db()
    plan_raw = db.execute("SELECT plan FROM wishes WHERE id = ?", (w1,)).fetchone()["plan"]
    db.close()
    plan = json.loads(plan_raw)
    assert plan["steps"] and set(plan["participants"]) == {"阿澈", "丫丫"}


# ---------- 方案追问（轻量对话） ----------

def test_plan_chat_flow(client: TestClient) -> None:
    """追问：方案未生成 400；非成员 403；正常一问一答落库可回读（mock 确定性）。"""
    cid, u1, u2 = _make_circle(client)
    w1 = _add_wish(client, cid, u1["user_id"], "想去音乐节")
    _add_wish(client, cid, u2["user_id"], "想去音乐节")

    # 方案未生成 → 400
    r = client.post(f"/api/chat/plan/{w1}", json={"user_id": u1["user_id"], "message": "预算能砍吗"})
    assert r.status_code == 400

    wishes_svc.generate_plan(w1)

    # 非成员 → 403
    other = client.post("/api/circles", json={"name": "圈外人"}).json()
    r = client.post(f"/api/chat/plan/{w1}", json={"user_id": other["user_id"], "message": "hi"})
    assert r.status_code == 403

    # 正常追问：用户消息 + mock 助手回复
    r = client.post(f"/api/chat/plan/{w1}", json={"user_id": u1["user_id"], "message": "预算能砍吗"})
    assert r.status_code == 200, r.text
    messages = r.json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "预算能砍吗"
    assert "mock 助手" in messages[1]["content"] and "音乐节" in messages[1]["content"]

    # 回读：再来一条，历史保留
    client.post(f"/api/chat/plan/{w1}", json={"user_id": u1["user_id"], "message": "周六改周日"})
    r = client.get(f"/api/chat/plan/{w1}", params={"user_id": u1["user_id"]})
    roles = [m["role"] for m in r.json()["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
