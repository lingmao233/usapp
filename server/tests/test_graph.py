"""关系图（第 3 期）测试：观看者相对的主题过滤、has_secret 仅当事人可见、
score 归一在 0-1、没跑过 nightly 的空圈不报错。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_graph.py -v
"""
import os
import sys
import tempfile
import time

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_graph_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import fakes  # noqa: E402
from app.main import app  # noqa: E402
from app.services import memory  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _make_circle(client: TestClient):
    """建圈返回 (circle_id, 建圈人, u1, u2)：建圈人天然充当第三方观看者。"""
    r = client.post("/api/circles", json={"name": "关系图测试圈"})
    assert r.status_code == 200, r.text
    circle = r.json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    u2 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "丫丫"}
    ).json()
    return circle["id"], circle["user_id"], u1, u2


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


def _graph(client: TestClient, cid: str, viewer_id: str) -> dict:
    r = client.get(f"/api/circles/{cid}/graph", params={"user_id": viewer_id})
    assert r.status_code == 200, r.text
    return r.json()


def _edge(graph: dict, uid1: str, uid2: str) -> dict:
    """取出某用户对的边（与 user_a/user_b 排序无关）。"""
    for e in graph["edges"]:
        if {e["user_a"], e["user_b"]} == {uid1, uid2}:
            return e
    raise AssertionError(f"图里没有 {uid1}-{uid2} 的边")


def _tags(content: str) -> set:
    return set(fakes._extract_tags(content))


# ---------- 空圈引导态 ----------

def test_graph_empty_circle_ok(client: TestClient) -> None:
    """没跑过 nightly 的圈（无 pair 行）：200 + 空 edges，前端据此显示引导态。"""
    cid, creator, _, _ = _make_circle(client)
    g = _graph(client, cid, creator)
    assert len(g["nodes"]) == 3  # 建圈人 + 阿澈 + 丫丫
    assert g["edges"] == []
    # 节点只带成员字段，不含任何分数
    assert all(set(n) == {"id", "nickname", "avatar", "created_at"} for n in g["nodes"])
    # 圈子不存在 → 404
    assert client.get("/api/circles/nope/graph", params={"user_id": creator}).status_code == 404


# ---------- 观看者相对的主题过滤（§5.1） ----------

def test_graph_topic_filtering_by_viewer(client: TestClient) -> None:
    """同一条边三种观看者三种过滤结果；private-private 主题对任何人（含当事人）都不展示。"""
    cid, creator, u1, u2 = _make_circle(client)
    posts = [
        (u1["user_id"], "爬山赏枫叶", "public"),
        (u2["user_id"], "爬山赏枫叶", "public"),
        (u1["user_id"], "夜里写代码", "private"),  # 隐私来源在 u1
        (u2["user_id"], "夜里写代码", "public"),
        (u1["user_id"], "攒钱买相机", "private"),
        (u2["user_id"], "攒钱买相机", "private"),
    ]
    for uid, content, vis in posts:
        _wait_processed(client, _post(client, cid, uid, content, vis), uid)
    memory.refresh_dirty(cid)

    pub_tags = _tags("爬山赏枫叶")

    # 当事人 u1：public-public + 本人隐私来源的 private-public；private-private 不展示
    tags_u1 = {t["tag"] for t in _edge(_graph(client, cid, u1["user_id"]), u1["user_id"], u2["user_id"])["topics"]}
    assert pub_tags <= tags_u1
    assert _tags("夜里写代码") <= tags_u1
    assert not (_tags("攒钱买相机") & tags_u1)

    # 当事人 u2：仅 public-public（「夜里写代码」的隐私来源是 u1，对 u2 同样隐藏）
    tags_u2 = {t["tag"] for t in _edge(_graph(client, cid, u2["user_id"]), u1["user_id"], u2["user_id"])["topics"]}
    assert tags_u2 == pub_tags

    # 第三方（建圈人）看 (u1,u2) 这条边：仅 public-public
    tags_3rd = {t["tag"] for t in _edge(_graph(client, cid, creator), u1["user_id"], u2["user_id"])["topics"]}
    assert tags_3rd == pub_tags

    # 关系摘要全员可见（生成时只用过可展示材料）
    assert _edge(_graph(client, cid, creator), u1["user_id"], u2["user_id"])["summary"]


# ---------- 秘密共同愿望提示 ----------

def test_graph_has_secret_only_for_parties(client: TestClient) -> None:
    """双隐共同愿望 → has_secret 仅当事人双方可见，且只提示存在、不揭晓主题。"""
    cid, creator, u1, u2 = _make_circle(client)
    for uid in (u1["user_id"], u2["user_id"]):
        _wait_processed(client, _post(client, cid, uid, "想去雪山泡温泉", "private"), uid)
    memory.refresh_dirty(cid)

    pair = (u1["user_id"], u2["user_id"])
    assert _edge(_graph(client, cid, u1["user_id"]), *pair)["has_secret"] is True
    assert _edge(_graph(client, cid, u2["user_id"]), *pair)["has_secret"] is True
    # 第三方视角：同一条边 has_secret 为 False
    assert _edge(_graph(client, cid, creator), *pair)["has_secret"] is False
    # 秘密愿望的主题对当事人也不揭晓（private-private 主题永不下发）
    for viewer in (u1["user_id"], u2["user_id"], creator):
        topics = _edge(_graph(client, cid, viewer), *pair)["topics"]
        assert not (_tags("想去雪山泡温泉") & {t["tag"] for t in topics})


# ---------- score 归一 ----------

def test_graph_score_normalized(client: TestClient) -> None:
    """全部边的 score 落在 0-1；同文公开碎片的一对满分，无信号的一对为 0。"""
    cid, creator, u1, u2 = _make_circle(client)
    for uid in (u1["user_id"], u2["user_id"]):
        _wait_processed(client, _post(client, cid, uid, "爬山赏枫叶"), uid)
    memory.refresh_dirty(cid)

    g = _graph(client, cid, u1["user_id"])
    assert len(g["edges"]) == 3  # C(3,2)
    assert all(0.0 <= e["score"] <= 1.0 for e in g["edges"])
    # 确定性桩 embedding 同文余弦 1.0、tags 完全一致：语义 0.35 + 主题 0.15 归一化后满分
    assert _edge(g, u1["user_id"], u2["user_id"])["score"] == pytest.approx(1.0)
    # 建圈人没有任何碎片：全信号为 0 → 0 分
    assert _edge(g, creator, u1["user_id"])["score"] == 0.0
    # 没有秘密共同愿望时 has_secret 一律为 False
    assert all(e["has_secret"] is False for e in g["edges"])
