"""账号认证测试：注册（账号名唯一/可选密码/找回凭证）、登录（无密码账号只验账号名）、
找回（凭证核验 → 重设密码 / 自设新凭证）。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_auth.py -v
"""
import os
import re
import sys
import tempfile

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_auth_"), "test.db")
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


# ---------- 注册 ----------

def test_register_returns_session_and_recovery_code(client: TestClient) -> None:
    """注册成功：返回 account_id/username/nickname/has_password + 找回凭证（强制展示用）。"""
    r = client.post("/api/auth/register", json={"username": "ache", "password": "pw123"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["account_id"] and d["username"] == "ache" and d["nickname"] == "ache"
    assert d["has_password"] is True
    assert re.fullmatch(r"[A-Z2-9]{6}", d["recovery_code"])  # 6 位无易混淆字符


def test_register_validation_and_uniqueness(client: TestClient) -> None:
    """空账号名/超长 400；重复（含 ASCII 大小写折叠）409；nickname 可自定义。"""
    r = client.post("/api/auth/register", json={"username": "yaya", "nickname": "丫丫"})
    assert r.status_code == 200 and r.json()["nickname"] == "丫丫"
    assert r.json()["has_password"] is False  # 不传密码 = 无密码账号

    assert client.post("/api/auth/register", json={"username": "  "}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "x" * 33}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "yaya"}).status_code == 409
    # ASCII 大小写折叠也算重复
    assert client.post("/api/auth/register", json={"username": "YAYA"}).status_code == 409


# ---------- 登录 ----------

def test_login_with_and_without_password(client: TestClient) -> None:
    """有密码账号：密码错/缺 403，对则 200；无密码账号只校验账号名；不存在 404。"""
    client.post("/api/auth/register", json={"username": "lock", "password": "s3cret"})
    client.post("/api/auth/register", json={"username": "open"})

    r = client.post("/api/auth/login", json={"username": "lock", "password": "s3cret"})
    assert r.status_code == 200 and r.json()["has_password"] is True
    assert "recovery_code" not in r.json()  # 登录不回凭证
    assert client.post("/api/auth/login", json={"username": "lock", "password": "wrong"}).status_code == 403
    assert client.post("/api/auth/login", json={"username": "lock"}).status_code == 403

    r = client.post("/api/auth/login", json={"username": "open"})
    assert r.status_code == 200 and r.json()["has_password"] is False
    # 无密码账号带了密码也照常放行（忽略）
    assert client.post("/api/auth/login", json={"username": "open", "password": "whatever"}).status_code == 200

    assert client.post("/api/auth/login", json={"username": "ghost"}).status_code == 404
    # 账号名 ASCII 大小写不敏感
    assert client.post("/api/auth/login", json={"username": "OPEN"}).status_code == 200


# ---------- 找回 ----------

def test_reset_flow(client: TestClient) -> None:
    """凭证核验 → 重设密码；旧密码立即失效；响应带回当前凭证供再次强制展示。"""
    reg = client.post("/api/auth/register", json={"username": "forgot", "password": "old"}).json()
    code = reg["recovery_code"]

    # 凭证错 403；账号不存在 404；空凭证 400
    assert client.post("/api/auth/reset", json={
        "username": "forgot", "recovery_code": "WRONG1", "new_password": "new"}).status_code == 403
    assert client.post("/api/auth/reset", json={
        "username": "ghost", "recovery_code": code, "new_password": "new"}).status_code == 404
    assert client.post("/api/auth/reset", json={
        "username": "forgot", "recovery_code": "  ", "new_password": "new"}).status_code == 400

    r = client.post("/api/auth/reset", json={
        "username": "forgot", "recovery_code": code, "new_password": "new"})
    assert r.status_code == 200, r.text
    assert r.json()["recovery_code"] == code and r.json()["has_password"] is True

    assert client.post("/api/auth/login", json={"username": "forgot", "password": "old"}).status_code == 403
    assert client.post("/api/auth/login", json={"username": "forgot", "password": "new"}).status_code == 200


def test_reset_custom_recovery_code_and_clear_password(client: TestClient) -> None:
    """找回时可顺便自设新凭证（旧凭证即刻失效）；密码传空串 = 清空成无密码账号。"""
    reg = client.post("/api/auth/register", json={"username": "resetme", "password": "pw"}).json()
    other = client.post("/api/auth/register", json={"username": "occupied"}).json()

    # 新凭证与别人撞车 409
    r = client.post("/api/auth/reset", json={
        "username": "resetme", "recovery_code": reg["recovery_code"],
        "new_recovery_code": other["recovery_code"]})
    assert r.status_code == 409

    r = client.post("/api/auth/reset", json={
        "username": "resetme", "recovery_code": reg["recovery_code"],
        "new_password": "", "new_recovery_code": "发财凭证"})
    assert r.status_code == 200 and r.json()["recovery_code"] == "发财凭证"
    assert r.json()["has_password"] is False

    # 旧凭证失效；新凭证可用；密码已清空 → 无密码登录
    assert client.post("/api/auth/reset", json={
        "username": "resetme", "recovery_code": reg["recovery_code"]}).status_code == 403
    assert client.post("/api/auth/login", json={"username": "resetme"}).status_code == 200
    r = client.post("/api/auth/reset", json={
        "username": "resetme", "recovery_code": "发财凭证", "new_password": "back"})
    assert r.status_code == 200 and r.json()["has_password"] is True
