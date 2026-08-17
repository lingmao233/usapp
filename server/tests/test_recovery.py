"""找回凭证测试：任意字符自设 + 唯一性校验 + 凭证在 auth reset 端生效（个人码登录已作废，
/api/accounts/claim 与 recover-lookup 均已下线，找回只走 /api/auth/reset）。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_recovery.py -v
"""
import os
import sys
import tempfile
import uuid

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_recovery_"), "test.db")
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


def _register(client: TestClient, password: str | None = None) -> dict:
    body = {"username": f"u-{uuid.uuid4().hex[:8]}"}
    if password:
        body["password"] = password
    r = client.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_custom_code_any_charset(client: TestClient) -> None:
    """汉字/符号/空格混合凭证都能设；空与超长 400；重复（含 ASCII 折叠）409。"""
    acc = _register(client)["account_id"]
    acc2 = _register(client)["account_id"]

    # 汉字码
    r = client.put(f"/api/accounts/{acc}/recovery_code", json={"code": "发财密码"})
    assert r.status_code == 200 and r.json()["recovery_code"] == "发财密码"
    # 凭证在找回端生效（精确匹配）
    r = client.post("/api/auth/reset", json={
        "username": _username(client, acc), "recovery_code": "发财密码", "new_password": "x"})
    assert r.status_code == 200

    # 重复码 409（另一个账号，此时 acc 仍持有"发财密码"）
    r = client.put(f"/api/accounts/{acc2}/recovery_code", json={"code": "发财密码"})
    assert r.status_code == 409

    # 符号 + 空格混合码（首尾空格被 strip，中间保留）
    r = client.put(f"/api/accounts/{acc}/recovery_code", json={"code": " 暴富吧 2026! "})
    assert r.status_code == 200 and r.json()["recovery_code"] == "暴富吧 2026!"

    # 空码 400；超长 400
    assert client.put(f"/api/accounts/{acc}/recovery_code", json={"code": "   "}).status_code == 400
    assert client.put(f"/api/accounts/{acc}/recovery_code", json={"code": "长" * 65}).status_code == 400

    # ASCII 大小写折叠冲突也 409
    client.put(f"/api/accounts/{acc}/recovery_code", json={"code": "mixedCase01"})
    r = client.put(f"/api/accounts/{acc2}/recovery_code", json={"code": "MIXEDCASE01"})
    assert r.status_code == 409


def test_reset_recovery_code_endpoint(client: TestClient) -> None:
    """随机重置找回凭证：旧凭证即刻失效，新码立即可用；账号不存在 404。"""
    reg = _register(client, password="pw")
    acc, old_code = reg["account_id"], reg["recovery_code"]

    r = client.post(f"/api/accounts/{acc}/recovery_code/reset")
    assert r.status_code == 200 and r.json()["recovery_code"] != old_code
    new_code = r.json()["recovery_code"]

    assert client.post("/api/auth/reset", json={
        "username": reg["username"], "recovery_code": old_code}).status_code == 403
    assert client.post("/api/auth/reset", json={
        "username": reg["username"], "recovery_code": new_code}).status_code == 200

    assert client.post("/api/accounts/ghost/recovery_code/reset").status_code == 404
    assert client.get("/api/accounts/ghost").status_code == 404


def test_get_account_exposes_auth_fields(client: TestClient) -> None:
    """账号详情带 username / has_password / recovery_code（设置页展示用）。"""
    reg = _register(client, password="pw")
    d = client.get(f"/api/accounts/{reg['account_id']}").json()
    assert d["username"] == reg["username"] and d["has_password"] is True
    assert d["recovery_code"] == reg["recovery_code"]


def _username(client: TestClient, account_id: str) -> str:
    return client.get(f"/api/accounts/{account_id}").json()["username"]


def test_old_claim_endpoints_gone(client: TestClient) -> None:
    """个人码登录体系作废：claim / recover-lookup 路由已下线（404 路径无存 / 405 方法无存）。"""
    assert client.post("/api/accounts/claim", json={"recovery_code": "X"}).status_code in (404, 405)
    assert client.post("/api/accounts/recover-lookup", json={
        "access_code": "x", "nickname": "y"}).status_code in (404, 405)
