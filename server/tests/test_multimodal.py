"""图片智能化测试：embedding payload 形状、caption 拼接向量、caption 开关、
双份上传与 _d 推导、旧图回退的服务端行为、周报素材带 caption。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_multimodal.py -v
"""
import os
import sys
import tempfile
import time

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_mm_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.ai import embedding, mock, vision  # noqa: E402
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


# ---------- 向量端点 payload 形状 ----------

def test_embed_payload_shape(monkeypatch) -> None:
    """纯文本向量：OpenAI 兼容 /embeddings + input 为文本 + dimensions 显式指定。"""
    captured: dict = {}
    monkeypatch.setattr(
        embedding.httpx, "post",
        lambda url, headers=None, json=None, timeout=None: captured.update(url=url, payload=json)
        or _fake_response([0.1, 0.2, 0.3]),
    )
    vec = embedding.embed("海边看日出")
    assert captured["url"].endswith("/embeddings")
    assert captured["payload"]["input"] == "海边看日出"
    assert captured["payload"]["dimensions"] == settings.EMBEDDING_DIM
    assert captured["payload"]["model"] == settings.EMBEDDING_MODEL
    assert len(vec) == 3


def test_vision_caption_payload_shape(monkeypatch) -> None:
    """caption 走 chat/completions：多模态消息（image_url + 中文提示词），模型取 VISION_MODEL。"""
    captured: dict = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "一只猫在海边"}}]}

    monkeypatch.setattr(
        vision.httpx, "post",
        lambda url, headers=None, json=None, timeout=None: captured.update(url=url, payload=json) or Resp(),
    )
    monkeypatch.setattr(settings, "VISION_MODEL", "vision-test-model")
    assert vision.vision_caption(JPEG_A, "jpeg") == "一只猫在海边"
    assert captured["url"].endswith("/chat/completions")
    assert captured["payload"]["model"] == "vision-test-model"
    content = captured["payload"]["messages"][0]["content"]
    assert content[0]["type"] == "image_url" and content[1]["type"] == "text"


def test_qwen3_vl_json_uses_fast_non_thinking_payload(monkeypatch, tmp_path) -> None:
    """阿里 Qwen3-VL 的结构化识别必须显式关思考，并让服务端保证 JSON。"""
    captured: dict = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"items": []}'}}]}

    monkeypatch.setattr(
        vision.httpx, "post",
        lambda url, headers=None, json=None, timeout=None: captured.update(url=url, payload=json)
        or Resp(),
    )
    monkeypatch.setattr(settings, "VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(settings, "VISION_MODEL", "qwen3-vl-flash")
    # 兼容项目原先建议的最低档配置；对 Qwen3-VL 应映射为真正关闭思考。
    monkeypatch.setattr(settings, "VISION_REASONING", "minimal")
    image = tmp_path / "receipt.jpg"
    image.write_bytes(JPEG_A)

    assert vision.vision_json(str(image), "请按 JSON 格式输出") == {"items": []}
    payload = captured["payload"]
    assert payload["enable_thinking"] is False
    assert "reasoning_effort" not in payload
    assert payload["response_format"] == {"type": "json_object"}


def test_receipt_accepts_json_object_wrapper(monkeypatch) -> None:
    """JSON Mode 要求顶层对象；账单识别从 items 解包，业务层仍收到数组。"""
    expected = [{
        "amount": 35.5,
        "merchant": "麦当劳",
        "time": "2026-08-16 12:30",
        "category": "餐饮",
        "type": "expense",
    }]
    monkeypatch.setattr(settings, "VISION_API_KEY", "x")
    monkeypatch.setattr(settings, "VISION_MODEL", "qwen3-vl-flash")
    monkeypatch.setattr(vision, "vision_json", lambda path, prompt: {"items": expected})

    assert ai.recognize_receipt("receipt.jpg") == expected


# ---------- 碎片向量：正文 + caption 拼接 ----------

def test_fragment_embedding_text_with_caption(monkeypatch) -> None:
    """带图碎片：embedding 输入 = 正文 + caption 拼接；视觉关闭（无 caption）退回 正文 or "[图片]"。"""
    captured: list[str] = []
    monkeypatch.setattr(ai, "embed_text", lambda t: captured.append(t) or mock.embed(t))

    pipeline.fragment_embedding("海边", "一只猫在海边")
    assert captured[-1] == "海边 一只猫在海边"  # 正文 + caption 拼接

    pipeline.fragment_embedding("", "一只猫在海边")
    assert captured[-1] == "一只猫在海边"  # 纯图碎片只用 caption

    pipeline.fragment_embedding("海边")
    assert captured[-1] == "海边"  # 纯文字维持原文

    pipeline.fragment_embedding("")
    assert captured[-1] == "[图片]"  # 无 caption 退回占位词


def test_read_display_image_prefers_d(client: TestClient) -> None:
    """caption 一律用展示图：有 _d 读 _d；旧图无 _d 回退原图。"""
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
    """默认（未配视觉模型 / 未配 key）：caption 返回空且不发任何 http。"""
    calls: list = []
    monkeypatch.setattr(
        vision.httpx, "post", lambda *a, **k: calls.append(1) or _fake_response([0.1])
    )
    assert ai.image_caption(JPEG_A) == ""  # VISION_MODEL 默认空
    assert calls == []

    monkeypatch.setattr(settings, "VISION_MODEL", "vision-test-model")
    assert ai.image_caption(JPEG_A) == ""  # 没配 key（VISION_API_KEY 空）仍跳过
    assert calls == []


def test_caption_switch_on(monkeypatch) -> None:
    """开了视觉模型且配了 key：走 vision.vision_caption；调用失败优雅返回空。"""
    monkeypatch.setattr(settings, "VISION_API_KEY", "x")
    monkeypatch.setattr(settings, "VISION_MODEL", "vision-test-model")
    monkeypatch.setattr(vision, "vision_caption", lambda d, f: "一只猫在海边")
    assert ai.image_caption(JPEG_A) == "一只猫在海边"

    def _boom(d, f):
        raise RuntimeError("模拟视觉模型宕机")

    monkeypatch.setattr(vision, "vision_caption", _boom)
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

    # 周报素材：[图片] + caption 进 fragments_repr（LLM 只拿分析结果，不接触图片）
    data = reports._collect_week_data(cid, *reports.current_week_range())
    assert f"[图片] 一只猫在海边" in data["fragments_repr"]
