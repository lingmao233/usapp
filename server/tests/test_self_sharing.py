"""Self 共享开关测试：UPSERT（校验/档位/幂等更新）、列表、删除；goal/plan 两档，ledger/calorie 只有开关。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_self_sharing.py -v
"""
import os
import sys
import tempfile
import uuid

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_sharing_"), "test.db")
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


def _make_circle(client: TestClient, account_id: str, name: str = "共享圈") -> dict:
    r = client.post("/api/circles", json={"name": name, "account_id": account_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_upsert_list_update_delete(client: TestClient) -> None:
    """开共享（默认 progress）→ 列表可见 → UPSERT 调档 → 删除关闭（幂等）。"""
    acc = _register(client)
    circle = _make_circle(client, acc)

    # 缺省 level = progress
    r = client.put("/api/self/sharing", json={
        "account_id": acc, "circle_id": circle["id"], "category": "goal"})
    assert r.status_code == 200 and r.json()["level"] == "progress"

    r = client.get("/api/self/sharing", params={"account_id": acc})
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["circle_id"] == circle["id"] and items[0]["circle_name"] == "共享圈"
    assert items[0]["category"] == "goal" and items[0]["level"] == "progress"

    # UPSERT 调档 detail；不插重复行
    r = client.put("/api/self/sharing", json={
        "account_id": acc, "circle_id": circle["id"], "category": "goal", "level": "detail"})
    assert r.status_code == 200 and r.json()["level"] == "detail"
    items = client.get("/api/self/sharing", params={"account_id": acc}).json()["items"]
    assert len(items) == 1 and items[0]["level"] == "detail"

    # 删除关闭；幂等再删也 200
    r = client.delete("/api/self/sharing", params={
        "account_id": acc, "circle_id": circle["id"], "category": "goal"})
    assert r.status_code == 200 and r.json()["shared"] is False
    assert client.delete("/api/self/sharing", params={
        "account_id": acc, "circle_id": circle["id"], "category": "goal"}).status_code == 200
    assert client.get("/api/self/sharing", params={"account_id": acc}).json()["items"] == []


def test_upsert_validation(client: TestClient) -> None:
    """非法 category/level 400；非本圈成员 403；账号/圈子不存在 404；ledger 无档位。"""
    acc = _register(client)
    circle = _make_circle(client, acc)
    stranger = _register(client)  # 不在圈里

    base = {"account_id": acc, "circle_id": circle["id"]}
    assert client.put("/api/self/sharing", json={**base, "category": "mood"}).status_code == 400
    assert client.put("/api/self/sharing", json={
        **base, "category": "plan", "level": "everything"}).status_code == 400
    assert client.put("/api/self/sharing", json={
        "account_id": "ghost", "circle_id": circle["id"], "category": "goal"}).status_code == 404
    assert client.put("/api/self/sharing", json={
        "account_id": acc, "circle_id": "ghost", "category": "goal"}).status_code == 404
    # 只能设置自己所在圈子的共享
    assert client.put("/api/self/sharing", json={
        "account_id": stranger, "circle_id": circle["id"], "category": "goal"}).status_code == 403
    assert client.delete("/api/self/sharing", params={
        "account_id": acc, "circle_id": circle["id"], "category": "mood"}).status_code == 400

    # ledger/calorie 只有开关无档位：level 强制 ''（传了也忽略）
    r = client.put("/api/self/sharing", json={
        **base, "category": "ledger", "level": "progress"})
    assert r.status_code == 200 and r.json()["level"] == ""
    r = client.put("/api/self/sharing", json={**base, "category": "calorie"})
    assert r.status_code == 200 and r.json()["level"] == ""
    items = client.get("/api/self/sharing", params={"account_id": acc}).json()["items"]
    assert {i["category"] for i in items} == {"ledger", "calorie"}


def test_sharing_lists_are_account_scoped(client: TestClient) -> None:
    """列表只含本人开关；别人看不到我的共享设置。"""
    a = _register(client)
    b = _register(client)
    circle = _make_circle(client, a, "列表隔离圈")
    client.put("/api/self/sharing", json={
        "account_id": a, "circle_id": circle["id"], "category": "plan", "level": "detail"})
    assert client.get("/api/self/sharing", params={"account_id": b}).json()["items"] == []
    assert client.get("/api/self/sharing", params={"account_id": "ghost"}).status_code == 404
