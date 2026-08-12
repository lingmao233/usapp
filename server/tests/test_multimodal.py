"""图片智能化测试：多模态端点 payload 形状、维度一致、图文均值向量、caption 开关、
双份上传与 _d 推导、旧图回退的服务端行为、周报素材带 caption。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_multimodal.py -v
"""
import base64
import os
import sys
import tempfile
import time

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_mm_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.ai import doubao, mock  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import fragments as frag_svc  # noqa: E402
from app.services import pipeline, reports  # noqa: E402

JPEG_A = b"\xff\xd8\xff\xe0" + b"\x11" * 128
JPEG_B = b"\xff\xd8\xff\xe0" + b"\x22" * 128
DISPLAY_BYTES = b"\xff\xd8\xff\xe0" + b"\x99" * 32


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _make_circle(client: TestClient):
    r = client.post("/api/circles", json={"name": "多模态测试圈"})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    return circle["id"], u1


def _wait_processed(client: TestClient, fid: str, author_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = client.get(f"/api/fragments/{fid}", params={"user_id": author_id}).json()
        if f.get("processed"):
            return f
        time.sleep(0.1)
    raise AssertionError(f"碎片 {fid} 异步处理超时")


def _fake_response(vec: list[float]):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": vec}]}

    return Resp()


# ---------- 多模态端点 payload 形状 ----------

def test_embed_text_payload_shape(monkeypatch) -> None:
    """文字入参：/embeddings/multimodal + {"type":"text"} + dimensions 显式指定。"""
    captured: dict = {}
    monkeypatch.setattr(
        doubao.httpx, "post",
        lambda url, headers=None, json=None, timeout=None: captured.update(url=url, payload=json)
        or _fake_response([0.1, 0.2, 0.3]),
    )
    vec = doubao.embed("海边看日出")
    assert captured["url"].endswith("/embeddings/multimodal")
    assert captured["payload"]["input"] == [{"type": "text", "text": "海边看日出"}]
    assert captured["payload"]["dimensions"] == settings.DOUBAO_EMBED_DIM
    assert captured["payload"]["model"] == settings.DOUBAO_EMBEDDING_MODEL
    assert len(vec) == 3


def test_embed_image_payload_shape(monkeypatch) -> None:
    """图片入参：同端点 + data URL base64 + 与文字相同的 dimensions（图文同空间同维度）。"""
    captured: dict = {}
    monkeypatch.setattr(
        doubao.httpx, "post",
        lambda url, headers=None, json=None, timeout=None: captured.update(url=url, payload=json)
        or _fake_response([0.1, 0.2]),
    )
    doubao.embed_image(JPEG_A, "jpeg")
    assert captured["url"].endswith("/embeddings/multimodal")
    item = captured["payload"]["input"][0]
    assert item["type"] == "image_url"
    data_url = item["image_url"]["url"]
    assert data_url.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(data_url.split(",", 1)[1]) == JPEG_A
    assert captured["payload"]["dimensions"] == settings.DOUBAO_EMBED_DIM


def test_vision_caption_payload_shape(monkeypatch) -> None:
    """caption 走 chat/completions：多模态消息（image_url + 中文提示词），模型取 DOUBAO_VISION_MODEL。"""
    captured: dict = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "一只猫在海边"}}]}

    monkeypatch.setattr(
        doubao.httpx, "post",
        lambda url, headers=None, json=None, timeout=None: captured.update(url=url, payload=json) or Resp(),
    )
    monkeypatch.setattr(settings, "DOUBAO_VISION_MODEL", "vision-test-model")
    assert doubao.vision_caption(JPEG_A, "jpeg") == "一只猫在海边"
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "vision-test-model"
    content = captured["payload"]["messages"][0]["content"]
    assert content[0]["type"] == "image_url" and content[1]["type"] == "text"


# ---------- mock 图片向量与维度一致 ----------

def test_mock_embed_image_deterministic() -> None:
    """mock 图片向量：字节哈希确定性、与文本向量同维度（EMBED_DIM）、不同图不同向量。"""
    v1, v2, v3 = mock.embed_image(JPEG_A), mock.embed_image(JPEG_A), mock.embed_image(JPEG_B)
    assert v1.shape == (mock.EMBED_DIM,) == mock.embed("海边").shape
    assert np.allclose(v1, v2)
    assert not np.allclose(v1, v3)
    assert np.linalg.norm(v1) == pytest.approx(1.0)


# ---------- 图文均值向量 ----------

def test_fragment_embedding_text_image_mean(monkeypatch) -> None:
    """图文双有：文字向量与图片向量均值 + 归一化；纯图片只用图片向量；纯文字维持文本向量。"""
    text_vec = np.zeros(4, dtype=np.float32)
    text_vec[0] = 1.0
    img_vec = np.zeros(4, dtype=np.float32)
    img_vec[1] = 1.0
    monkeypatch.setattr(ai, "embed_text", lambda t: text_vec)
    monkeypatch.setattr(ai, "embed_image", lambda d, f: img_vec)

    # 造一个真实图片文件（原图无展示副本 → 回退读原图）
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stem = "a" * 32
    (settings.upload_dir / f"{stem}.jpg").write_bytes(JPEG_A)
    url = f"/api/uploads/{stem}.jpg"

    both = pipeline.fragment_embedding("海边", url)
    assert np.allclose(both, [2**-0.5, 2**-0.5, 0, 0], atol=1e-6)  # 均值归一化

    only_img = pipeline.fragment_embedding("", url)
    assert np.allclose(only_img, [0, 1, 0, 0])  # 纯图片只用图片向量

    only_text = pipeline.fragment_embedding("海边", None)
    assert np.allclose(only_text, [1, 0, 0, 0])  # 纯文字维持文本向量


def test_read_display_image_prefers_d(client: TestClient) -> None:
    """embedding 一律用展示图：有 _d 读 _d；旧图无 _d 回退原图。"""
    r = client.post(
        "/api/uploads",
        files={"file": ("p.jpg", JPEG_A, "image/jpeg"), "display": ("d.jpg", DISPLAY_BYTES, "image/jpeg")},
    )
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    data, fmt = frag_svc.read_display_image(url)
    assert data == DISPLAY_BYTES and fmt == "jpeg"

    r = client.post("/api/uploads", files={"file": ("p.jpg", JPEG_B, "image/jpeg")})
    old_url = r.json()["url"]
    data, _ = frag_svc.read_display_image(old_url)
    assert data == JPEG_B  # 旧图回退原图


# ---------- 双份上传与 _d 白名单 ----------

def test_dual_upload_and_d_whitelist(client: TestClient) -> None:
    """双份上传：原图与 {uuid}_d.jpg 各存各的，GET 都 200 且内容一致；旧图（无 display）_d 404。"""
    r = client.post(
        "/api/uploads",
        files={"file": ("p.jpg", JPEG_A, "image/jpeg"), "display": ("d.jpg", DISPLAY_BYTES, "image/jpeg")},
    )
    url = r.json()["url"]
    stem = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    d_url = f"/api/uploads/{stem}_d.jpg"

    g = client.get(url)
    assert g.status_code == 200 and g.content == JPEG_A
    g = client.get(d_url)  # 白名单放行 _d
    assert g.status_code == 200 and g.content == DISPLAY_BYTES

    # 旧图行为：没有展示副本 → 原图 200、推导的 _d 名 404（前端据此 onError 回退原图）
    r = client.post("/api/uploads", files={"file": ("p.jpg", JPEG_B, "image/jpeg")})
    old_url = r.json()["url"]
    old_stem = old_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    assert client.get(old_url).status_code == 200
    assert client.get(f"/api/uploads/{old_stem}_d.jpg").status_code == 404


# ---------- caption 开关 ----------

def test_caption_switch_off_by_default(monkeypatch) -> None:
    """默认（未配视觉模型 / mock 模式）：caption 返回空且不发任何 http。"""
    calls: list = []
    monkeypatch.setattr(
        doubao.httpx, "post", lambda *a, **k: calls.append(1) or _fake_response([0.1])
    )
    assert ai.image_caption(JPEG_A) == ""  # DOUBAO_VISION_MODEL 默认空
    assert calls == []

    monkeypatch.setattr(settings, "DOUBAO_VISION_MODEL", "vision-test-model")
    assert ai.image_caption(JPEG_A) == ""  # embed_mock（没配 key）仍跳过
    assert calls == []


def test_caption_switch_on(monkeypatch) -> None:
    """开了视觉模型且配了 key：走 doubao.vision_caption；调用失败优雅返回空。"""
    monkeypatch.setattr(type(settings), "embed_mock", property(lambda self: False))
    monkeypatch.setattr(settings, "DOUBAO_VISION_MODEL", "vision-test-model")
    monkeypatch.setattr(doubao, "vision_caption", lambda d, f: "一只猫在海边")
    assert ai.image_caption(JPEG_A) == "一只猫在海边"

    def _boom(d, f):
        raise RuntimeError("模拟视觉模型宕机")

    monkeypatch.setattr(doubao, "vision_caption", _boom)
    assert ai.image_caption(JPEG_A) == ""  # 失败优雅跳过


def test_caption_flows_into_classify_and_report(client: TestClient, monkeypatch) -> None:
    """caption 接线：写入 fragments.caption、进分类输入（标签来自 caption）、进周报素材。"""
    monkeypatch.setattr(ai, "image_caption", lambda d, f: "一只猫在海边")
    cid, u1 = _make_circle(client)
    r = client.post("/api/uploads", files={"file": ("p.jpg", JPEG_A, "image/jpeg")})
    url = r.json()["url"]

    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "", "image_url": url},
    )
    assert r.status_code == 200, r.text
    f = _wait_processed(client, r.json()["id"], u1["user_id"])

    # caption 落列；分类输入 = caption → 标签与「一只猫在海边」的 mock 提取一致
    assert f["caption"] == "一只猫在海边"
    assert set(f["tags"]) == set(mock._extract_tags("一只猫在海边"))
    assert f["is_wish"] is False  # caption 不含愿望关键词，占位词时代行为不回归

    # 周报素材：[图片] + caption 进 fragments_repr（DeepSeek 只拿分析结果，不接触图片）
    data = reports._collect_week_data(cid, *reports.current_week_range())
    assert f"[图片] 一只猫在海边" in data["fragments_repr"]
