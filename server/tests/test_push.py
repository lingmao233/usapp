"""推送（第 5 期）测试：订阅存取/去重/退订、互动触发推送（不推自己/不推取消赞）、
失效订阅（404/410）清理、任务层落 task_runs。

pywebpush 的网络发送一律 mock 掉，只断言调用参数与落库行为。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_push.py -v
"""
import json
import os
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_push_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pywebpush import WebPushException  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

KEYS = {"p256dh": "BNcRd" + "a" * 80, "auth": "tBHItJ" + "b" * 16}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    """测试线程直读数据库（WAL 允许多连接一读一写）。"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _make_circle(client: TestClient):
    """建一个两人的测试圈，返回 (circle_id, u1, u2)。"""
    r = client.post("/api/circles", json={"name": "推送测试圈"})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    return circle["id"], u1, u2


def _post(client: TestClient, cid: str, uid: str, content: str = "一条公开碎片") -> str:
    r = client.post(
        "/api/fragments", json={"circle_id": cid, "user_id": uid, "content": content}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _subscribe(client: TestClient, user_id: str, endpoint: str):
    return client.post(
        "/api/push/subscribe",
        json={"user_id": user_id, "endpoint": endpoint, "keys": KEYS},
    )


def _sub_rows(user_id: str) -> list[sqlite3.Row]:
    db = _db()
    rows = db.execute(
        "SELECT * FROM push_subscriptions WHERE user_id = ? ORDER BY endpoint", (user_id,)
    ).fetchall()
    db.close()
    return rows


# ---------- VAPID 公钥 ----------

def test_vapid_key_stable(client: TestClient) -> None:
    """公钥端点返回 base64url 公钥（87 字符），两次调用一致，密钥文件落盘在数据库同目录。"""
    k1 = client.get("/api/push/vapid-key").json()["public_key"]
    k2 = client.get("/api/push/vapid-key").json()["public_key"]
    assert k1 == k2 and len(k1) == 87
    vapid_file = os.path.join(os.path.dirname(settings.DB_PATH), "vapid.json")
    assert os.path.exists(vapid_file)


# ---------- 订阅存取 / 去重 / 退订 ----------

def test_subscribe_dedup_and_rebind(client: TestClient) -> None:
    """存订阅；同 endpoint 重复订阅去重（仍 1 行）；换 user_id 重订等于换绑。"""
    cid, u1, u2 = _make_circle(client)
    ep = "https://push.example.com/dedup-ep"

    assert _subscribe(client, u1["user_id"], ep).status_code == 200
    assert _subscribe(client, u1["user_id"], ep).status_code == 200  # 重复订阅
    rows = _sub_rows(u1["user_id"])
    assert len(rows) == 1 and rows[0]["endpoint"] == ep
    assert json.loads(rows[0]["keys_json"]) == KEYS

    # 同一设备换身份：endpoint 不变，user_id 换绑
    assert _subscribe(client, u2["user_id"], ep).status_code == 200
    assert _sub_rows(u1["user_id"]) == []
    assert len(_sub_rows(u2["user_id"])) == 1

    # 缺 keys 的非法请求 → 422
    r = client.post("/api/push/subscribe", json={"user_id": u1["user_id"], "endpoint": ep})
    assert r.status_code == 422


def test_unsubscribe_idempotent(client: TestClient) -> None:
    """退订删行；重复退订幂等返回 200。"""
    _, u1, _ = _make_circle(client)
    ep = "https://push.example.com/unsub-ep"
    _subscribe(client, u1["user_id"], ep)
    assert len(_sub_rows(u1["user_id"])) == 1

    assert client.post("/api/push/unsubscribe", json={"endpoint": ep}).status_code == 200
    assert _sub_rows(u1["user_id"]) == []
    assert client.post("/api/push/unsubscribe", json={"endpoint": ep}).status_code == 200


# ---------- 互动触发推送 ----------

def test_comment_pushes_author(client: TestClient) -> None:
    """评论他人碎片 → 作者的每个订阅各发一次，payload 为 {title, body, url}，url 指向 /wall；
    任务层落 task_runs 成功行。"""
    cid, u1, u2 = _make_circle(client)
    _subscribe(client, u1["user_id"], "https://push.example.com/c-ep1")
    _subscribe(client, u1["user_id"], "https://push.example.com/c-ep2")
    fid = _post(client, cid, u1["user_id"])

    with patch("app.services.push.webpush") as mock_send:
        r = client.post(
            f"/api/fragments/{fid}/comments",
            json={"author_id": u2["user_id"], "content": "说得好"},
        )
        assert r.status_code == 200, r.text
    assert mock_send.call_count == 2  # 两个订阅各一次
    endpoints = {c.kwargs["subscription_info"]["endpoint"] for c in mock_send.call_args_list}
    assert endpoints == {"https://push.example.com/c-ep1", "https://push.example.com/c-ep2"}
    payload = json.loads(mock_send.call_args_list[0].kwargs["data"])
    assert payload["url"] == "/wall" and payload["title"]
    assert "丫丫" in payload["body"] and "说得好" in payload["body"]

    db = _db()
    run = db.execute(
        "SELECT * FROM task_runs WHERE task_name = 'push_comment' AND entity_id = ?", (fid,)
    ).fetchone()
    db.close()
    assert run is not None and run["status"] == "success"


def test_no_push_for_self_comment(client: TestClient) -> None:
    """作者评论自己的碎片不推。"""
    cid, u1, _ = _make_circle(client)
    _subscribe(client, u1["user_id"], "https://push.example.com/self-ep")
    fid = _post(client, cid, u1["user_id"])

    with patch("app.services.push.webpush") as mock_send:
        r = client.post(
            f"/api/fragments/{fid}/comments",
            json={"author_id": u1["user_id"], "content": "自言自语"},
        )
        assert r.status_code == 200, r.text
    mock_send.assert_not_called()


def test_like_push_but_not_unlike(client: TestClient) -> None:
    """赞 → 推一次；取消赞 → 不推；再赞 → 再推；自赞 → 不推。"""
    cid, u1, u2 = _make_circle(client)
    _subscribe(client, u1["user_id"], "https://push.example.com/like-ep")
    fid = _post(client, cid, u1["user_id"])

    with patch("app.services.push.webpush") as mock_send:
        assert client.put(
            f"/api/fragments/{fid}/like", json={"user_id": u2["user_id"]}
        ).json()["liked"] is True
        assert mock_send.call_count == 1
        payload = json.loads(mock_send.call_args_list[0].kwargs["data"])
        assert payload["url"] == "/wall" and "丫丫" in payload["body"]

        # 取消赞不推
        assert client.put(
            f"/api/fragments/{fid}/like", json={"user_id": u2["user_id"]}
        ).json()["liked"] is False
        assert mock_send.call_count == 1

        # 再赞再推
        assert client.put(
            f"/api/fragments/{fid}/like", json={"user_id": u2["user_id"]}
        ).json()["liked"] is True
        assert mock_send.call_count == 2

        # 自赞不推
        assert client.put(
            f"/api/fragments/{fid}/like", json={"user_id": u1["user_id"]}
        ).json()["liked"] is True
        assert mock_send.call_count == 2


# ---------- 失效订阅清理 ----------

def test_gone_subscription_deleted(client: TestClient) -> None:
    """推送返回 404/410 → 该订阅被删除，任务仍记成功；其余订阅不受影响。"""
    cid, u1, u2 = _make_circle(client)
    _subscribe(client, u1["user_id"], "https://push.example.com/gone-ep")
    _subscribe(client, u1["user_id"], "https://push.example.com/live-ep")
    fid = _post(client, cid, u1["user_id"])

    def fake_webpush(subscription_info, **kwargs):
        if subscription_info["endpoint"].endswith("gone-ep"):
            raise WebPushException("gone", response=SimpleNamespace(status_code=410))

    with patch("app.services.push.webpush", side_effect=fake_webpush) as mock_send:
        r = client.post(
            f"/api/fragments/{fid}/comments",
            json={"author_id": u2["user_id"], "content": "触发一次推送"},
        )
        assert r.status_code == 200, r.text
    assert mock_send.call_count == 2
    rows = _sub_rows(u1["user_id"])
    assert [row["endpoint"] for row in rows] == ["https://push.example.com/live-ep"]

    db = _db()
    run = db.execute(
        "SELECT * FROM task_runs WHERE task_name = 'push_comment' AND entity_id = ?", (fid,)
    ).fetchone()
    db.close()
    assert run["status"] == "success"  # 失效清理不算失败
