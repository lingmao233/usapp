"""删除（碎片/愿望）测试：作者权限、碎片级联（评论/点赞/来源愿望/来源知识条目/
无引用图片文件）、受影响用户对互动分量重算与 dirty 打点、愿望删除。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_delete.py -v
"""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_del_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    """测试线程直读数据库（WAL 允许多连接一读一写）。settings 以首次 import 为准。"""
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _make_circle(client: TestClient):
    """建一个两人的测试圈，返回 (circle_id, u1, u2)。"""
    r = client.post("/api/circles", json={"name": "删除测试圈"})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    return circle["id"], u1, u2


def _post(client: TestClient, cid: str, uid: str, content: str, image_url: str | None = None) -> str:
    body: dict = {"circle_id": cid, "user_id": uid, "content": content}
    if image_url:
        body["image_url"] = image_url
    r = client.post("/api/fragments", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _wait_processed(client: TestClient, fid: str, author_id: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = client.get(f"/api/fragments/{fid}", params={"user_id": author_id}).json()
        if f.get("processed"):
            return
        time.sleep(0.1)
    raise AssertionError(f"碎片 {fid} 异步处理超时")


def _upload(client: TestClient) -> str:
    r = client.post("/api/uploads", files={"file": ("p.jpg", JPEG_BYTES, "image/jpeg")})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _image_path(url: str) -> Path:
    return settings.upload_dir / url.rsplit("/", 1)[-1]


def _count(table: str, where: str, params: tuple) -> int:
    db = _db()
    c = db.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {where}", params).fetchone()["c"]
    db.close()
    return c


def _pair_row(cid: str, uid1: str, uid2: str) -> sqlite3.Row | None:
    a, b = sorted((uid1, uid2))
    db = _db()
    row = db.execute(
        "SELECT * FROM pair_relationships WHERE circle_id=? AND user_a=? AND user_b=?",
        (cid, a, b),
    ).fetchone()
    db.close()
    return row


# ---------- 碎片删除：权限与级联 ----------

def test_delete_fragment_cascades(client: TestClient) -> None:
    """作者删除碎片：评论/点赞/来源愿望/来源知识条目/无引用图片文件全部清掉，
    受影响用户对互动分量当场重算归零，且用户对被标 dirty 等 nightly。"""
    cid, u1, u2 = _make_circle(client)
    url = _upload(client)
    # 含愿望关键词 + 链接（mock 分类：is_wish + is_knowledge），且带图
    fid = _post(
        client, cid, u1["user_id"],
        "想去露营看星星，攻略 https://example.com/camp-guide-123 先存着", image_url=url,
    )
    _wait_processed(client, fid, u1["user_id"])
    assert _image_path(url).is_file()
    assert _count("wishes", "fragment_id = ?", (fid,)) == 1
    assert _count("knowledge_items", "fragment_id = ?", (fid,)) == 1

    # 丫丫评论 + 点赞 → 用户对互动分量 > 0
    r = client.post(
        f"/api/fragments/{fid}/comments", json={"author_id": u2["user_id"], "content": "带我一个"}
    )
    assert r.status_code == 200
    assert client.put(f"/api/fragments/{fid}/like", json={"user_id": u2["user_id"]}).json()["liked"] is True
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["interaction"] > 0

    r = client.delete(f"/api/fragments/{fid}", params={"user_id": u1["user_id"]})
    assert r.status_code == 200, r.text

    # 级联：碎片本体 / 评论 / 点赞 / 来源愿望 / 来源知识条目 / 无引用图片文件
    assert client.get(f"/api/fragments/{fid}", params={"user_id": u1["user_id"]}).status_code == 404
    assert _count("comments", "fragment_id = ?", (fid,)) == 0
    assert _count("likes", "fragment_id = ?", (fid,)) == 0
    assert _count("wishes", "fragment_id = ?", (fid,)) == 0
    assert _count("knowledge_items", "fragment_id = ?", (fid,)) == 0
    assert not _image_path(url).exists()
    assert client.get(url).status_code == 404

    # 互动分量当场重算归零；用户对被标 dirty（语义/主题留给 nightly）
    pair = _pair_row(cid, u1["user_id"], u2["user_id"])
    assert pair["interaction"] == pytest.approx(0.0)
    assert pair["dirty"] == 1


def test_delete_fragment_permission(client: TestClient) -> None:
    """非作者删除 403 且原碎片完好；不存在 404；删完后他人自己的内容照旧。"""
    cid, u1, u2 = _make_circle(client)
    fid = _post(client, cid, u1["user_id"], "阿澈的碎片")
    other = _post(client, cid, u2["user_id"], "丫丫的碎片")

    assert client.delete(f"/api/fragments/{fid}", params={"user_id": u2["user_id"]}).status_code == 403
    assert client.delete("/api/fragments/nope", params={"user_id": u1["user_id"]}).status_code == 404
    # 403 之后原碎片完好
    assert client.get(f"/api/fragments/{fid}", params={"user_id": u2["user_id"]}).status_code == 200

    assert client.delete(f"/api/fragments/{fid}", params={"user_id": u1["user_id"]}).status_code == 200
    # 他人数据不受影响
    assert client.get(f"/api/fragments/{other}", params={"user_id": u1["user_id"]}).status_code == 200


def test_delete_fragment_keeps_shared_image(client: TestClient) -> None:
    """图片仍被其他行引用时删碎片不动物理文件；愿望删除是单行删除，本来就不清图片。"""
    cid, u1, u2 = _make_circle(client)
    url = _upload(client)
    f1 = _post(client, cid, u1["user_id"], "第一条带图", image_url=url)
    # 同一 url 被丫丫的愿望引用
    r = client.post(
        "/api/wishes",
        json={"circle_id": cid, "user_id": u2["user_id"], "content": "想去这里", "image_url": url},
    )
    assert r.status_code == 200
    wid = r.json()["id"]
    _wait_processed(client, f1, u1["user_id"])

    assert client.delete(f"/api/fragments/{f1}", params={"user_id": u1["user_id"]}).status_code == 200
    assert _image_path(url).is_file()  # 愿望还引用着 → 文件保留
    assert client.get(url).status_code == 200

    # 愿望删除只删行、不清图片（图片清理只发生在碎片删除路径）
    assert client.delete(f"/api/wishes/{wid}", params={"user_id": u2["user_id"]}).status_code == 200
    assert _image_path(url).is_file()


# ---------- 愿望删除 ----------

def test_delete_wish(client: TestClient) -> None:
    """作者删愿望 200 + 单行消失 + 用户对打 dirty；非作者 403；不存在 404。"""
    cid, u1, u2 = _make_circle(client)
    r = client.post(
        "/api/wishes",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "想去山里徒步"},
    )
    assert r.status_code == 200, r.text
    wid = r.json()["id"]

    assert client.delete(f"/api/wishes/{wid}", params={"user_id": u2["user_id"]}).status_code == 403
    assert client.delete("/api/wishes/nope", params={"user_id": u1["user_id"]}).status_code == 404

    assert client.delete(f"/api/wishes/{wid}", params={"user_id": u1["user_id"]}).status_code == 200
    wishes = client.get("/api/wishes", params={"circle_id": cid, "user_id": u1["user_id"]}).json()["wishes"]
    assert all(w["id"] != wid for w in wishes)
    # 相关用户对已标 dirty（共同愿望分量留给 nightly 重算）
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["dirty"] == 1
