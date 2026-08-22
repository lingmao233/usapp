"""设备令牌鉴权测试（fakes 确定性桩）：签发格式、Bearer 校验、过渡期放行、ENFORCE 收紧。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_tokens.py -v
"""
import os
import sys
import tempfile

# 独立测试数据库 + 清空厂商 key 与鉴权配置（挡住 .env 回填；AI 由 conftest 装 tests/fakes
# 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_tokens_"), "test.db")
for _k in ("LLM_API_KEY", "EMBEDDING_API_KEY", "VISION_API_KEY", "VISION_MODEL",
           "TREEHOLE_API_KEY", "TREEHOLE_BASE_URL", "TREEHOLE_MODEL",
           "DEVICE_SECRET", "TOKEN_ENFORCE"):
    os.environ[_k] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.config as config  # noqa: E402
from app.main import app  # noqa: E402
from app.services import tokens as tokens_svc  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _new_account(client: TestClient) -> dict:
    """建圈即建账号：响应必须带 device_token（格式 {account_id}.{hmac[:24]}）。"""
    r = client.post("/api/circles", json={"name": "令牌测试圈", "nickname": "阿澈"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["device_token"], "建圈响应必须带 device_token"
    head, sig = body["device_token"].split(".", 1)
    assert head == body["account_id"] and len(sig) == 24
    return body


def test_token_is_stateless_and_stable(client: TestClient) -> None:
    """无状态恒定令牌：同账号再次签发结果一致（HMAC(secret, account_id)，不落表不轮换）。"""
    body = _new_account(client)
    assert tokens_svc.issue_token(body["account_id"]) == body["device_token"]


def test_bearer_ok_wrong_401_headerless_passes(client: TestClient) -> None:
    """正确 Bearer 放行；带错必拒 401（与过渡期无关）；无头默认放行（过渡期）。"""
    body = _new_account(client)
    acc, token = body["account_id"], body["device_token"]
    ok = client.get("/api/treehole/history", params={"account_id": acc},
                    headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200 and ok.json()["items"] == []
    bad = client.get("/api/treehole/history", params={"account_id": acc},
                     headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401
    headerless = client.get("/api/treehole/history", params={"account_id": acc})
    assert headerless.status_code == 200  # 存量用户 localStorage 里还没有 token，先放行


def test_enforce_on_rejects_headerless(client: TestClient, monkeypatch) -> None:
    """TOKEN_ENFORCE=on：无头 401；带对令牌照常放行。
    注：test_config 会 reload(config)，进程内可能同时存活新旧 settings 实例
    （tokens 持其中一份、config.settings 是另一份）——patch 要打全部活实例。"""
    body = _new_account(client)
    acc, token = body["account_id"], body["device_token"]
    live = {id(s): s for s in (config.settings, tokens_svc.settings)}
    for s in live.values():
        monkeypatch.setattr(s, "TOKEN_ENFORCE", "on")
    assert client.get(
        "/api/treehole/history", params={"account_id": acc}).status_code == 401
    ok = client.get("/api/treehole/history", params={"account_id": acc},
                    headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200


def test_unknown_account_404(client: TestClient) -> None:
    """账号不存在：404（先于令牌校验，明文 account_id 模式下的存在性探测也挡住）。"""
    r = client.get("/api/treehole/history", params={"account_id": "no-such-account"})
    assert r.status_code == 404
