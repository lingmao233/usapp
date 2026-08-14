"""身份码放开（任意字符）+ 按名字找回（特定码门）测试。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_recovery.py -v
"""
import os
import sys
import tempfile

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_recovery_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _make_circle(client: TestClient, name: str = "找回测试圈"):
    """建圈（圈主账号）+ 阿澈加入，返回 (circle, u1)。"""
    r = client.post("/api/circles", json={"name": name})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    return circle, u1


def _join(client: TestClient, invite_code: str, nickname: str) -> None:
    r = client.post("/api/circles/join", json={"invite_code": invite_code, "nickname": nickname})
    assert r.status_code == 200, r.text


# ---------- 自定义身份码：不限字符 ----------

def test_custom_code_any_charset(client: TestClient) -> None:
    """汉字/符号/空格混合码都能设；空与超长拒绝；重复（含 ASCII 折叠）409。"""
    circle, _ = _make_circle(client)
    acc = circle["account_id"]

    # 汉字码
    r = client.put(f"/api/accounts/{acc}/recovery_code", json={"code": "发财密码"})
    assert r.status_code == 200 and r.json()["recovery_code"] == "发财密码"
    # claim 精确匹配
    r = client.post("/api/accounts/claim", json={"recovery_code": "发财密码"})
    assert r.status_code == 200 and r.json()["account_id"] == acc

    # 重复码 409（另一个账号，此时 acc 仍持有"发财密码"）
    circle2, _ = _make_circle(client, "找回测试圈2")
    r = client.put(f"/api/accounts/{circle2['account_id']}/recovery_code", json={"code": "发财密码"})
    assert r.status_code == 409

    # 符号 + 空格混合码（首尾空格被 strip，中间保留）
    r = client.put(f"/api/accounts/{acc}/recovery_code", json={"code": " 暴富吧 2026! "})
    assert r.status_code == 200 and r.json()["recovery_code"] == "暴富吧 2026!"

    # 空码 400；超长 400
    r = client.put(f"/api/accounts/{acc}/recovery_code", json={"code": "   "})
    assert r.status_code == 400
    r = client.put(f"/api/accounts/{acc}/recovery_code", json={"code": "长" * 65})
    assert r.status_code == 400

    # ASCII 大小写折叠冲突也 409
    client.put(f"/api/accounts/{acc}/recovery_code", json={"code": "mixedCase01"})
    r = client.put(f"/api/accounts/{circle2['account_id']}/recovery_code", json={"code": "MIXEDCASE01"})
    assert r.status_code == 409


# ---------- 按名字找回（特定码门） ----------

def test_recover_lookup_gate_and_results(client: TestClient, monkeypatch) -> None:
    """特定码核验（多码、汉字/字母均可）；按圈内昵称返回所有圈的 圈子名+身份码。"""
    monkeypatch.setattr(settings, "RECOVERY_ACCESS_CODES", ("芝麻开门", "opensesame"))
    # 本测试独占昵称（多测试模块共享库，别的测试也建过"阿澈"）
    name = "阿茶茶"
    circle = client.post("/api/circles", json={"name": "找回甲圈"}).json()
    _join(client, circle["invite_code"], name)
    circle2 = client.post("/api/circles", json={"name": "找回乙圈"}).json()
    _join(client, circle2["invite_code"], name)

    # 错码 403；未配置特定码时一律 403
    r = client.post("/api/accounts/recover-lookup", json={"access_code": "错码", "nickname": name})
    assert r.status_code == 403
    monkeypatch.setattr(settings, "RECOVERY_ACCESS_CODES", ())
    r = client.post("/api/accounts/recover-lookup", json={"access_code": "芝麻开门", "nickname": name})
    assert r.status_code == 403
    monkeypatch.setattr(settings, "RECOVERY_ACCESS_CODES", ("芝麻开门", "opensesame"))

    # 汉字特定码：两个圈的同名成员都列出
    r = client.post("/api/accounts/recover-lookup", json={"access_code": "芝麻开门", "nickname": name})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 2
    assert {x["circle_name"] for x in results} == {"找回甲圈", "找回乙圈"}
    assert all(x["recovery_code"] for x in results)

    # 第二个特定码（字母）同样可用
    r = client.post("/api/accounts/recover-lookup", json={"access_code": "opensesame", "nickname": name})
    assert r.status_code == 200 and len(r.json()["results"]) == 2

    # 不存在的名字 → 空列表；空名字 400
    r = client.post("/api/accounts/recover-lookup", json={"access_code": "芝麻开门", "nickname": "不存在的人"})
    assert r.status_code == 200 and r.json()["results"] == []
    r = client.post("/api/accounts/recover-lookup", json={"access_code": "芝麻开门", "nickname": "  "})
    assert r.status_code == 400
