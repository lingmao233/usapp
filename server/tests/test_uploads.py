"""图片上传与配图（碎片/愿望发图片）测试：上传校验、纯图片创建与回读、
image_url 前缀校验、隐私碎片带图对他人仍不可见。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_uploads.py -v
"""
import os
import sys
import tempfile
import time

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_img_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # 服务端只验 content-type，不验内容


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _make_circle(client: TestClient):
    """建一个两人的测试圈，返回 (circle_id, u1, u2)。"""
    r = client.post("/api/circles", json={"name": "图片测试圈"})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    return circle["id"], u1, u2


def _upload(client: TestClient, data: bytes = JPEG_BYTES, content_type: str = "image/jpeg"):
    return client.post(
        "/api/uploads", files={"file": ("photo.jpg", data, content_type)}
    )


def _wait_processed(client: TestClient, fid: str, author_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = client.get(f"/api/fragments/{fid}", params={"user_id": author_id}).json()
        if f.get("processed"):
            return f
        time.sleep(0.1)
    raise AssertionError(f"碎片 {fid} 异步处理超时")


# ---------- 上传校验 ----------

def test_upload_and_get_roundtrip(client: TestClient) -> None:
    """jpeg 上传 → {url}；GET 读回 200 + 内容一致 + Content-Type 正确。"""
    r = _upload(client)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith("/api/uploads/") and url.endswith(".jpg")

    g = client.get(url)
    assert g.status_code == 200
    assert g.content == JPEG_BYTES
    assert g.headers["content-type"] == "image/jpeg"


def test_upload_rejects_bad_type(client: TestClient) -> None:
    """非图片 content-type → 400；非 multipart 请求 → 400。"""
    r = _upload(client, b"plain text", "text/plain")
    assert r.status_code == 400
    r = client.post("/api/uploads", content=b"not multipart")
    assert r.status_code == 400


def test_upload_rejects_oversize(client: TestClient) -> None:
    """超过 20MB → 413（10MB 量级现在是合法原图）。"""
    ok = b"\xff\xd8\xff\xe0" + b"\x00" * (10 * 1024 * 1024)
    assert _upload(client, ok, "image/jpeg").status_code == 200
    big = b"\xff\xd8\xff\xe0" + b"\x00" * (20 * 1024 * 1024)
    r = _upload(client, big, "image/jpeg")
    assert r.status_code == 413


def test_get_upload_path_traversal(client: TestClient) -> None:
    """文件名白名单：非 {32hex}.{ext} 形状一律 404（防目录穿越）。

    含 / 的穿越串在 HTTP 客户端/服务器层就被规范化，到不了处理函数；
    真正能到达路由的单段非法名由白名单正则拒绝。
    """
    assert client.get("/api/uploads/not-an-upload.jpg").status_code == 404
    assert client.get("/api/uploads/..app.db").status_code == 404
    assert client.get("/api/uploads/" + "0" * 32 + ".jpg").status_code == 404  # 形状对但不存在


# ---------- 纯图片碎片 / 愿望 ----------

def test_image_only_fragment(client: TestClient) -> None:
    """纯图片碎片：content 空 + image_url 合法 → 创建、管线跑完（占位词）、列表/详情回读 image_url。"""
    cid, u1, _ = _make_circle(client)
    url = _upload(client).json()["url"]

    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "", "image_url": url},
    )
    assert r.status_code == 200, r.text
    fid = r.json()["id"]

    f = _wait_processed(client, fid, u1["user_id"])  # 占位词喂管线，照常 processed
    assert f["image_url"] == url and f["content"] == ""

    feed = client.get("/api/fragments", params={"circle_id": cid, "user_id": u1["user_id"]}).json()
    item = next(x for x in feed["fragments"] if x["id"] == fid)
    assert item["image_url"] == url


def test_image_url_validation(client: TestClient) -> None:
    """image_url 只接受 /api/uploads/ 前缀；内容与图片都空 → 400；图文混排合法。"""
    cid, u1, _ = _make_circle(client)
    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "带图", "image_url": "https://evil.com/x.jpg"},
    )
    assert r.status_code == 400

    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "  ", "image_url": None},
    )
    assert r.status_code == 400

    url = _upload(client).json()["url"]
    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "图文混排", "image_url": url},
    )
    assert r.status_code == 200


def test_image_only_wish(client: TestClient) -> None:
    """纯图片愿望：content 空 + image_url 合法 → 创建与列表回读；外链 image_url → 400。"""
    cid, u1, _ = _make_circle(client)
    url = _upload(client).json()["url"]

    r = client.post(
        "/api/wishes",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "", "image_url": url},
    )
    assert r.status_code == 200, r.text

    wishes = client.get("/api/wishes", params={"circle_id": cid, "user_id": u1["user_id"]}).json()["wishes"]
    w = next(x for x in wishes if x["id"] == r.json()["id"])
    assert w["image_url"] == url and w["content"] == ""

    r = client.post(
        "/api/wishes",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "", "image_url": "https://evil.com/x.jpg"},
    )
    assert r.status_code == 400


# ---------- 隐私规则不变 ----------

def test_private_fragment_with_image_stays_hidden(client: TestClient) -> None:
    """隐私碎片带图：他人详情 404、列表不可见（图片跟着碎片走，可见性过滤在服务端早已生效）。"""
    cid, u1, u2 = _make_circle(client)
    url = _upload(client).json()["url"]
    r = client.post(
        "/api/fragments",
        json={
            "circle_id": cid, "user_id": u1["user_id"],
            "content": "", "image_url": url, "visibility": "private",
        },
    )
    assert r.status_code == 200, r.text
    fid = r.json()["id"]

    assert client.get(f"/api/fragments/{fid}", params={"user_id": u2["user_id"]}).status_code == 404
    feed = client.get("/api/fragments", params={"circle_id": cid, "user_id": u2["user_id"]}).json()
    assert all(x["id"] != fid for x in feed["fragments"])
    # 作者本人照常可见
    mine = client.get(f"/api/fragments/{fid}", params={"user_id": u1["user_id"]})
    assert mine.status_code == 200 and mine.json()["image_url"] == url
