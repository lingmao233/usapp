"""记忆消费（画像注入生成管线）测试：style 渲染、prompt 组装、viewer-relative 隐私边界。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_memory_consumption.py -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_memcon_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import memory  # noqa: E402
from app.services import reports as reports_svc  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    """测试线程直读数据库（WAL 允许多连接一读一写）。

    用 settings.DB_PATH 而不是环境变量：多测试模块同进程时 settings 以首次 import 为准。
    """
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _make_circle(client: TestClient, name: str = "记忆消费圈", **extra):
    """建一个两人的测试圈，返回 (circle, u1, u2)。"""
    r = client.post("/api/circles", json={"name": name, **extra})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    return circle, u1, u2


def _post(client: TestClient, cid: str, uid: str, content: str, visibility: str = "public") -> str:
    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": uid, "content": content, "visibility": visibility},
    )
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


# ---------- style 渲染（纯函数） ----------

def test_format_style_digest() -> None:
    """全字段渲染成一行；暂无/缺失字段跳过；全空返回空串。"""
    profile = {
        "style": {
            "catchphrases": ["绝了", "笑死"],
            "wording": "口语化表达为主",
            "emoji": "常带 emoji",
            "sentence_length": "短句为主",
        }
    }
    line = ai.format_style_digest("阿澈", profile)
    assert line.startswith("阿澈：")
    assert "口头禅「绝了」「笑死」" in line
    assert "口语化表达为主" in line and "常带 emoji" in line and "短句为主" in line

    # 暂无与空字段不出现；全无效时返回空串
    line2 = ai.format_style_digest(
        "丫丫", {"style": {"catchphrases": [], "wording": "暂无", "emoji": "暂无", "sentence_length": ""}}
    )
    assert line2 == ""
    assert ai.format_style_digest("丫丫", {}) == ""


# ---------- 周报 styles 注入 ----------

def test_weekly_prompt_contains_styles() -> None:
    """成员风格行出现在最终 prompt；缺省有兜底文案；分寸规则仍在。"""
    prompt = ai.build_weekly_prompt(
        "- [2026-08-10] 阿澈：去爬山",
        "2026-08-03",
        "2026-08-09",
        persona="像佟掌柜",
        quotes=["阿澈：爬山真累"],
        styles=["阿澈：短句为主，常带 emoji"],
    )
    assert "像佟掌柜" in prompt
    assert "- 阿澈：短句为主，常带 emoji" in prompt
    assert "事实与猜测的分寸" in prompt

    p2 = ai.build_weekly_prompt("（本周还没有碎片）", "2026-08-03", "2026-08-09")
    assert "（暂无成员风格画像）" in p2


def test_weekly_styles_exclude_private_topics(client: TestClient) -> None:
    """隐私碎片参与画像统计（topics），但周报 styles 只渲染公开发言蒸馏的 style 维。"""
    circle, u1, _ = _make_circle(client, name="风格边界圈")
    cid = circle["id"]
    fid = _post(client, cid, u1["user_id"], "今天好累但开心 😄 哈哈哈")
    _wait_processed(client, fid, u1["user_id"])
    priv = _post(client, cid, u1["user_id"], "昨晚偷偷去看了演唱会，别告诉别人", visibility="private")
    _wait_processed(client, priv, u1["user_id"])

    memory.refresh_dirty(cid)

    profiles = memory.get_profiles(cid)
    topics = profiles[u1["user_id"]].get("topics", [])
    assert topics  # 隐私碎片照常参与画像统计（含隐私来源 tag）

    data = reports_svc._collect_week_data(cid, *reports_svc.current_week_range())
    assert any(s.startswith("阿澈：") and "常带 emoji" in s for s in data["styles"])
    # viewer-relative 边界：统计维（topics，含隐私来源）绝不随 styles 进周报 prompt 数据
    for s in data["styles"]:
        for t in topics:
            assert t not in s


# ---------- 方案追问画像注入（viewer-relative） ----------

def test_plan_chat_prompt_viewer_relative() -> None:
    """纯函数：自己全量画像 JSON 进 prompt；他人仅 style 行；缺省有兜底。"""
    viewer = {"topics": ["演唱会"], "summary": "老朋友眼中的 TA", "style": {"wording": "口语化"}}
    prompt = ai.build_plan_chat_prompt(
        "想去海边",
        ["阿澈", "丫丫"],
        {"time": "周六"},
        ["阿澈：冲"],
        [{"role": "user", "content": "住哪儿"}],
        "预算呢",
        viewer_profile=viewer,
        member_styles=["丫丫：偏长句"],
    )
    assert '"topics": ["演唱会"]' in prompt  # 自己全量画像（含统计维）可见
    assert "- 丫丫：偏长句" in prompt

    p2 = ai.build_plan_chat_prompt("想去海边", ["阿澈"], {}, [], [], "在吗")
    assert "（暂无画像）" in p2 and "（暂无）" in p2


def test_send_plan_chat_injects_profiles(client: TestClient, monkeypatch) -> None:
    """集成：追问时 viewer 拿到自己全量画像，其他参与者只注入 style 行。"""
    circle, u1, u2 = _make_circle(client, name="追问画像圈")
    cid = circle["id"]
    f1 = _post(client, cid, u1["user_id"], "我想去海边吃海鲜 😄")
    _wait_processed(client, f1, u1["user_id"])
    f2 = _post(client, cid, u2["user_id"], "周末爬山走起，简简单单")
    _wait_processed(client, f2, u2["user_id"])
    memory.refresh_dirty(cid)

    # 直接落库一条带方案的愿望（绕开共同愿望匹配流程）
    wish_id = "w_plan_chat_inject"
    plan = {"time": "周六", "location": "海边", "steps": [], "participants": ["阿澈", "丫丫"]}
    db = _db()
    db.execute(
        "INSERT INTO wishes (id, user_id, circle_id, content, category, fragment_id, status,"
        " matched_users, plan, created_at, visibility)"
        " VALUES (?, ?, ?, ?, 'go', '', 'active', '[]', ?, ?, 'public')",
        (wish_id, u1["user_id"], cid, "想去海边",
         json.dumps(plan, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
    )
    db.commit()
    db.close()

    captured: dict = {}

    def _spy(wish, plan, participants, quotes, history, message,
             viewer_profile=None, member_styles=None):
        captured["viewer_profile"] = viewer_profile
        captured["member_styles"] = member_styles
        return "（spy 回复）"

    monkeypatch.setattr(ai, "plan_chat", _spy)

    r = client.post(
        f"/api/chat/plan/{wish_id}", json={"user_id": u1["user_id"], "message": "预算呢"}
    )
    assert r.status_code == 200, r.text

    # viewer 自己：全量画像（含 topics 统计维，仅本人视角可见）
    assert captured["viewer_profile"] is not None
    assert "topics" in captured["viewer_profile"] and "style" in captured["viewer_profile"]
    # 其他参与者：仅 style 行；viewer 本人不出现在 member_styles
    assert any(s.startswith("丫丫：") for s in captured["member_styles"])
    assert not any(s.startswith("阿澈：") for s in captured["member_styles"])
    # style 行不含对方 topics 统计维内容
    other_topics = memory.get_profiles(cid)[u2["user_id"]].get("topics", [])
    for s in captured["member_styles"]:
        for t in other_topics:
            assert t not in s
