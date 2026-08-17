"""人格系统 + 内容对齐（A+B1）测试：人格 CRUD、prompt 组装、语录边界、style 蒸馏。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_persona.py -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_persona_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.ai.prompts import PERSONAS, resolve_persona  # noqa: E402
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


def _make_circle(client: TestClient, name: str = "人格测试圈", **extra):
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


# ---------- 人格 CRUD ----------

def test_persona_create_defaults_and_get(client: TestClient) -> None:
    """建圈不带人格 → 默认观察员；带人格 → 落库；GET 响应带两字段。"""
    circle, _, _ = _make_circle(client)
    g = client.get(f"/api/circles/{circle['id']}").json()
    assert g["persona_preset"] == "observer" and g["persona_custom"] == ""

    circle2, _, _ = _make_circle(client, name="损友圈", persona_preset="sunshi")
    g2 = client.get(f"/api/circles/{circle2['id']}").json()
    assert g2["persona_preset"] == "sunshi" and g2["persona_custom"] == ""


def test_persona_member_update_and_isolation(client: TestClient) -> None:
    """任何成员可改人格（自定义优先）；非成员 403；不存在的圈 404；两圈互不影响。"""
    circle, u1, _ = _make_circle(client, persona_preset="sunshi")
    cid = circle["id"]

    r = client.put(
        f"/api/circles/{cid}/persona",
        json={"user_id": u1["user_id"], "persona_preset": "shudong", "persona_custom": ""},
    )
    assert r.status_code == 200, r.text
    assert r.json()["persona_preset"] == "shudong"

    # 自定义文本：落库 + 解析优先级（自定义 > 预设）
    r = client.put(
        f"/api/circles/{cid}/persona",
        json={"user_id": u1["user_id"], "persona_preset": "shudong", "persona_custom": "像佟掌柜"},
    )
    g = client.get(f"/api/circles/{cid}").json()
    assert g["persona_custom"] == "像佟掌柜"
    assert resolve_persona(g["persona_preset"], g["persona_custom"]) == "像佟掌柜"

    # 非成员（另一个圈的用户）→ 403
    other = client.post("/api/circles", json={"name": "别的圈"}).json()
    r = client.put(
        f"/api/circles/{cid}/persona",
        json={"user_id": other["user_id"], "persona_preset": "laba", "persona_custom": ""},
    )
    assert r.status_code == 403

    # 圈不存在 → 404
    r = client.put(
        "/api/circles/no-such-circle/persona",
        json={"user_id": u1["user_id"], "persona_preset": "laba", "persona_custom": ""},
    )
    assert r.status_code == 404

    # 两圈人格互不影响：上面 403 没改成，别的圈仍是默认观察员
    g_other = client.get(f"/api/circles/{other['id']}").json()
    assert g_other["persona_preset"] == "observer" and g_other["persona_custom"] == ""
    g = client.get(f"/api/circles/{cid}").json()
    assert g["persona_preset"] == "shudong" and g["persona_custom"] == "像佟掌柜"


# ---------- prompt 组装（纯函数断言） ----------

def test_resolve_persona_fallback_chain() -> None:
    """自定义非空优先；查预设库；查不到回退观察员。"""
    assert resolve_persona("sunshi", "") == PERSONAS["sunshi"]
    assert resolve_persona("sunshi", "  ") == PERSONAS["sunshi"]  # 空白自定义视为空
    assert resolve_persona("no-such-preset", "") == PERSONAS["observer"]
    assert resolve_persona("sunshi", "像佟掌柜") == "像佟掌柜"


def test_weekly_prompt_contains_persona_and_quotes() -> None:
    """人格文本与语录出现在最终 prompt；事实分寸规则原样保留且在人格段之后。"""
    prompt = ai.build_weekly_prompt(
        "- [2026-08-10] 阿澈：去爬山",
        "2026-08-03",
        "2026-08-09",
        persona=PERSONAS["sunshi"],
        quotes=["阿澈：爬山真累", "丫丫：哈哈哈"],
    )
    assert PERSONAS["sunshi"] in prompt
    assert "- 阿澈：爬山真累" in prompt and "- 丫丫：哈哈哈" in prompt
    assert "不要大段照抄" in prompt
    assert "事实与猜测的分寸" in prompt
    assert "绝不要写进报告" in prompt  # 分寸是写作纪律，不作为章节渲染（BUG-005）
    assert prompt.index(PERSONAS["sunshi"]) < prompt.index("事实与猜测的分寸")

    # 人格缺省回退观察员；语录为空有兜底文案
    p2 = ai.build_weekly_prompt("（本周还没有碎片）", "2026-08-03", "2026-08-09")
    assert PERSONAS["observer"] in p2
    assert "（本周还没有可引用的发言）" in p2


def test_generate_weekly_report_mock_accepts_persona_and_quotes() -> None:
    """mock 模式下带 persona/quotes 走通完整签名（模板桩不崩，周期标题正确）。"""
    content = ai.generate_weekly_report(
        "- [2026-08-10] 阿澈：去爬山",
        "2026-08-03",
        "2026-08-09",
        {"users": ["阿澈"], "top_tags": ["爬山"], "wishes": [], "knowledge_count": 0, "connections": []},
        persona=PERSONAS["laba"],
        quotes=["阿澈：爬山真累"],
    )
    assert "本周交集报告（2026-08-03 - 2026-08-09）" in content


# ---------- 语录边界与优先级 ----------

def test_quotes_boundary_and_priority(client: TestClient) -> None:
    """隐私碎片/隐私碎片的评论不进 quotes；互动多的公开碎片优先于单纯最新的。"""
    circle, u1, u2 = _make_circle(client, name="语录圈")
    cid = circle["id"]
    ws, we = reports_svc.current_week_range()

    # 9 条公开碎片（超过 8 条上限）：f1 最早但互动多，f2 次早且无互动
    fids = [
        _post(client, cid, u1["user_id"] if i % 2 == 0 else u2["user_id"], f"公开碎片第{i + 1}号")
        for i in range(9)
    ]
    priv = _post(client, cid, u1["user_id"], "隐私碎片内容SECRET", visibility="private")

    # f1 收获互动：两人点赞 + 一条评论
    client.put(f"/api/fragments/{fids[0]}/like", json={"user_id": u2["user_id"]})
    client.put(f"/api/fragments/{fids[0]}/like", json={"user_id": circle["user_id"]})
    r = client.post(
        f"/api/fragments/{fids[0]}/comments",
        json={"author_id": u2["user_id"], "content": "这条评论真精彩COMMENT"},
    )
    assert r.status_code == 200, r.text
    comment_id = r.json()["id"]

    # 直接往库里塞一条隐私碎片下的评论（API 层本就 403，验证 SQL join 护栏兜底）
    db = _db()
    db.execute(
        """INSERT INTO comments (id, circle_id, fragment_id, author_id, parent_id, content, created_at)
           VALUES (?, ?, ?, ?, NULL, ?, ?)""",
        ("c_priv_quote", cid, priv, u2["user_id"], "隐私碎片下的评论SECRET",
         datetime.now().isoformat(timespec="seconds")),
    )
    # 统一改写 created_at 为本周内递增时间戳，消除同秒并列导致的顺序不确定
    base = datetime.fromisoformat(ws) + timedelta(hours=8)
    for i, fid in enumerate(fids + [priv]):
        db.execute(
            "UPDATE fragments SET created_at = ? WHERE id = ?",
            ((base + timedelta(hours=i)).isoformat(timespec="seconds"), fid),
        )
    db.execute(
        "UPDATE comments SET created_at = ? WHERE id = ?",
        ((base + timedelta(hours=20)).isoformat(timespec="seconds"), comment_id),
    )
    db.commit()

    quotes = reports_svc._collect_quotes(db, cid, ws, we)
    db.close()

    joined = "\n".join(quotes)
    # 隐私铁律：隐私碎片与其评论绝不进语录
    assert "隐私碎片内容SECRET" not in joined
    assert "隐私碎片下的评论SECRET" not in joined
    # 上限与优先级：≤8 条；f1 互动多必入选；f2 次早无互动被挤出
    assert len(quotes) <= 8
    assert "公开碎片第1号" in joined
    assert "公开碎片第2号" not in joined
    # 公开碎片下的评论作为语录入选
    assert "这条评论真精彩COMMENT" in joined


# ---------- style 蒸馏 ----------

def test_style_distillation_mock_deterministic(client: TestClient) -> None:
    """画像 JSON 带 style 键（mock 确定性）：emoji/句长由公开摘录推导；无摘录为暂无。"""
    circle, u1, _ = _make_circle(client, name="风格圈")
    cid = circle["id"]
    fid = _post(client, cid, u1["user_id"], "今天好开心呀 😄 哈哈哈")
    _wait_processed(client, fid, u1["user_id"])

    memory.refresh_dirty(cid)

    db = _db()
    rows = {
        r["user_id"]: json.loads(r["profile"])
        for r in db.execute(
            "SELECT user_id, profile FROM user_profiles WHERE circle_id = ?", (cid,)
        ).fetchall()
    }
    db.close()

    p1 = rows[u1["user_id"]]
    assert p1["style"]["emoji"] == "常带 emoji"
    assert p1["style"]["wording"] == "口语化表达为主"
    assert p1["style"]["sentence_length"] == "短句为主"

    # 建圈人没发过言：style 各字段暂无
    p_creator = rows[circle["user_id"]]
    assert p_creator["style"]["emoji"] == "暂无"
    assert p_creator["style"]["sentence_length"] == "暂无"
