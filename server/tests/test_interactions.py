"""互动（第 4 期）测试：楼中楼评论、点赞 toggle、隐私铁律（403）、互动分量计分口径（§4）、
写路径实时更新 pair_relationships 并反映到图 API score。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_interactions.py -v
"""
import os
import sqlite3
import sys
import tempfile

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_act_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import memory  # noqa: E402


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
    r = client.post("/api/circles", json={"name": "互动测试圈"})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    return circle["id"], u1, u2


def _post(client: TestClient, cid: str, uid: str, content: str, visibility: str = "public") -> str:
    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": uid, "content": content, "visibility": visibility},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _comment(client: TestClient, fid: str, author_id: str, content: str, parent_id: str | None = None):
    return client.post(
        f"/api/fragments/{fid}/comments",
        json={"author_id": author_id, "content": content, "parent_id": parent_id},
    )


def _like(client: TestClient, fid: str, user_id: str):
    return client.put(f"/api/fragments/{fid}/like", json={"user_id": user_id})


def _pair_row(cid: str, uid1: str, uid2: str) -> sqlite3.Row | None:
    a, b = sorted((uid1, uid2))
    db = _db()
    row = db.execute(
        "SELECT * FROM pair_relationships WHERE circle_id=? AND user_a=? AND user_b=?",
        (cid, a, b),
    ).fetchone()
    db.close()
    return row


# ---------- 楼中楼结构与校验 ----------

def test_comment_threading_and_parent_validation(client: TestClient) -> None:
    """顶级评论 + 楼中楼回复（含回复的回复）平铺返回、parent_id 正确、按时间正序；
    父评论不存在 / 跨碎片 → 404；空内容 → 400。"""
    cid, u1, u2 = _make_circle(client)
    fid = _post(client, cid, u1["user_id"], "周末去爬山，风景绝了")

    r = _comment(client, fid, u2["user_id"], "看着就凉快")
    assert r.status_code == 200, r.text
    top_id = r.json()["id"]
    r = _comment(client, fid, u1["user_id"], "下次一起", parent_id=top_id)
    assert r.status_code == 200, r.text
    reply_id = r.json()["id"]
    r = _comment(client, fid, u2["user_id"], "好呀带上我", parent_id=reply_id)
    assert r.status_code == 200

    comments = client.get(f"/api/fragments/{fid}/comments").json()["comments"]
    assert [c["content"] for c in comments] == ["看着就凉快", "下次一起", "好呀带上我"]
    assert comments[0]["parent_id"] is None
    assert comments[1]["parent_id"] == top_id
    assert comments[2]["parent_id"] == reply_id  # 回复的回复也允许（同一碎片内即可）
    assert all(c["author_nickname"] for c in comments)
    # 碎片详情/列表带互动计数
    detail = client.get(f"/api/fragments/{fid}", params={"user_id": u1["user_id"]}).json()
    assert detail["comment_count"] == 3 and detail["like_count"] == 0
    assert detail["liked_by_me"] is False

    # 父评论校验：不存在 / 属于别的碎片 → 404
    assert _comment(client, fid, u1["user_id"], "x", parent_id="nope").status_code == 404
    other = _post(client, cid, u1["user_id"], "另一条碎片")
    r = _comment(client, other, u2["user_id"], "另一条下的评论")
    assert r.status_code == 200
    assert _comment(client, fid, u1["user_id"], "x", parent_id=r.json()["id"]).status_code == 404
    # 空内容 400；非本圈成员 403
    assert _comment(client, fid, u1["user_id"], "   ").status_code == 400
    assert _comment(client, fid, "outsider", "蹭一下").status_code == 403


# ---------- 隐私铁律与 404 ----------

def test_private_fragment_not_interactable(client: TestClient) -> None:
    """隐私碎片评论/点赞/读评论一律 403（作者本人也不行）；碎片不存在 → 404。"""
    cid, u1, u2 = _make_circle(client)
    priv = _post(client, cid, u1["user_id"], "只给自己看的日记", visibility="private")

    assert _comment(client, priv, u2["user_id"], "看不见我").status_code == 403
    assert _like(client, priv, u2["user_id"]).status_code == 403
    assert client.get(f"/api/fragments/{priv}/comments").status_code == 403
    # 作者本人同样不可互动（铁律无例外）
    assert _comment(client, priv, u1["user_id"], "自言自语").status_code == 403
    assert _like(client, priv, u1["user_id"]).status_code == 403

    for r in (
        _comment(client, "nope", u1["user_id"], "x"),
        _like(client, "nope", u1["user_id"]),
        client.get("/api/fragments/nope/comments"),
    ):
        assert r.status_code == 404


# ---------- 点赞 toggle ----------

def test_like_toggle_idempotent(client: TestClient) -> None:
    """赞→取消→赞 往返状态与计数正确；(fragment_id, user_id) 唯一约束兜底。"""
    cid, u1, u2 = _make_circle(client)
    fid = _post(client, cid, u1["user_id"], "今天做了一顿大餐")

    r = _like(client, fid, u2["user_id"]).json()
    assert r == {"liked": True, "like_count": 1}
    r = _like(client, fid, u2["user_id"]).json()
    assert r == {"liked": False, "like_count": 0}
    r = _like(client, fid, u2["user_id"]).json()
    assert r == {"liked": True, "like_count": 1}

    # liked_by_me 按请求者身份区分
    as_u2 = client.get(f"/api/fragments/{fid}", params={"user_id": u2["user_id"]}).json()
    as_u1 = client.get(f"/api/fragments/{fid}", params={"user_id": u1["user_id"]}).json()
    assert as_u2["liked_by_me"] is True and as_u2["like_count"] == 1
    assert as_u1["liked_by_me"] is False and as_u1["like_count"] == 1
    # 列表同样带计数
    feed = client.get("/api/fragments", params={"circle_id": cid, "user_id": u2["user_id"]}).json()
    f = next(x for x in feed["fragments"] if x["id"] == fid)
    assert f["like_count"] == 1 and f["liked_by_me"] is True
    # 非本圈成员点赞 403
    assert _like(client, fid, "outsider").status_code == 403


# ---------- 互动分量计分口径（§4） ----------

def test_one_way_comment_half_then_filled(client: TestClient) -> None:
    """单向评论半分（1.5/条）；对方也评论后双向补满（3/条）。写路径即时落 pair 行。"""
    cid, u1, u2 = _make_circle(client)
    f1 = _post(client, cid, u1["user_id"], "阿澈的公开碎片")
    f2 = _post(client, cid, u2["user_id"], "丫丫的公开碎片")

    assert _comment(client, f2, u1["user_id"], "单向一条").status_code == 200
    row = _pair_row(cid, u1["user_id"], u2["user_id"])
    assert row is not None and row["interaction"] == pytest.approx(1.5)  # 单向半分

    assert _comment(client, f2, u1["user_id"], "单向第二条").status_code == 200
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["interaction"] == pytest.approx(3.0)

    # 丫丫回评阿澈的碎片 → 双向都有评论，双方按 3 分/条补满：(2+1)*3
    assert _comment(client, f1, u2["user_id"], "来回一句").status_code == 200
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["interaction"] == pytest.approx(9.0)


def test_one_way_like_discounted(client: TestClient) -> None:
    """单向点赞打五折（0.5/个）；双向都有点赞后 1 分/个；取消赞即时回算。"""
    cid, u1, u2 = _make_circle(client)
    f1 = _post(client, cid, u1["user_id"], "阿澈的碎片")
    f2 = _post(client, cid, u2["user_id"], "丫丫的碎片")

    assert _like(client, f2, u1["user_id"]).json()["liked"] is True
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["interaction"] == pytest.approx(0.5)

    assert _like(client, f1, u2["user_id"]).json()["liked"] is True
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["interaction"] == pytest.approx(2.0)

    # 丫丫取消赞 → 回到单向 0.5
    assert _like(client, f1, u2["user_id"]).json()["liked"] is False
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["interaction"] == pytest.approx(0.5)


def test_self_interaction_not_counted(client: TestClient) -> None:
    """自己评论/点赞自己的碎片不计分，也不落自闭合行（user_a == user_b）。"""
    cid, u1, _ = _make_circle(client)
    fid = _post(client, cid, u1["user_id"], "自言自语的一条")

    assert _comment(client, fid, u1["user_id"], "自己评论自己").status_code == 200
    assert _like(client, fid, u1["user_id"]).json()["liked"] is True

    db = _db()
    self_rows = db.execute(
        "SELECT COUNT(*) AS c FROM pair_relationships WHERE circle_id=? AND user_a=user_b", (cid,)
    ).fetchone()["c"]
    nonzero = db.execute(
        "SELECT COUNT(*) AS c FROM pair_relationships WHERE circle_id=? AND interaction != 0", (cid,)
    ).fetchone()["c"]
    db.close()
    assert self_rows == 0
    assert nonzero == 0  # 唯一的互动是自互动，任何用户对的分量都不动
    # 计数照常展示（互动 UI 不受影响，只是不进亲密度）
    detail = client.get(f"/api/fragments/{fid}", params={"user_id": u1["user_id"]}).json()
    assert detail["comment_count"] == 1 and detail["like_count"] == 1


def test_cross_pair_isolation(client: TestClient) -> None:
    """互动只影响当事用户对；第三人与任何一方的用户对不受影响。"""
    r = client.post("/api/circles", json={"name": "互动隔离测试圈"})
    circle = r.json()
    cid = circle["id"]
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    fid = _post(client, cid, u2["user_id"], "丫丫的碎片")
    assert _like(client, fid, u1["user_id"]).json()["liked"] is True

    assert _pair_row(cid, u1["user_id"], u2["user_id"])["interaction"] == pytest.approx(0.5)
    # 建圈人是第三人：互动分量不受影响（行因碎片打点存在与否都有可能，分数必须为 0）
    for other in (u1["user_id"], u2["user_id"]):
        row = _pair_row(cid, circle["user_id"], other)
        assert row is None or row["interaction"] == 0


# ---------- 写路径 → 图 API ----------

def test_interaction_flows_into_graph_score(client: TestClient) -> None:
    """写路径后不等 nightly：pair 行即时更新，图 API 读取时现算 score 反映互动分量。"""
    cid, u1, u2 = _make_circle(client)
    fid = _post(client, cid, u2["user_id"], "丫丫的碎片")

    # 无互动：发碎片打点建过行，但边 score 全 0
    graph = client.get(f"/api/circles/{cid}/graph", params={"user_id": u1["user_id"]}).json()
    edge = next(e for e in graph["edges"] if {e["user_a"], e["user_b"]} == {u1["user_id"], u2["user_id"]})
    assert edge["score"] == 0.0

    assert _comment(client, fid, u1["user_id"], "第一条评论").status_code == 200
    row = _pair_row(cid, u1["user_id"], u2["user_id"])
    assert row["interaction"] == pytest.approx(1.5)

    graph = client.get(f"/api/circles/{cid}/graph", params={"user_id": u1["user_id"]}).json()
    edge = next(e for e in graph["edges"] if {e["user_a"], e["user_b"]} == {u1["user_id"], u2["user_id"]})
    # 图 score 与 compute_pair_score 同口径（唯一信号归一化后满分，界面只映射线宽不显示分数）
    assert edge["score"] == memory.compute_pair_score(
        {"semantic": 0.0, "interaction": 1.5, "common_wishes": 0.0, "common_topics": 0.0}
    )
    assert edge["score"] > 0

    # nightly 重算与写路径同一口径：幂等收敛，互动分量不丢不重
    memory.refresh_dirty(cid)
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["interaction"] == pytest.approx(1.5)
