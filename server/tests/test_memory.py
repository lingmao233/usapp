"""记忆层（第 2 期）测试：任务层、四分量、共同主题可见性标记、dirty 蒸馏、周报读持久数据。

运行：cd server && .venv/bin/python -m pytest tests/test_memory.py -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import time

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_mem_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.ai import deepseek, mock  # noqa: E402
from app.config import settings  # noqa: E402
from app.db.database import init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import memory, nightly, tasks, wishes  # noqa: E402

init_db()  # 任务层单测不经 TestClient，先确保建表


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


def _make_circle(client: TestClient):
    """建一个两人的测试圈，返回 (circle_id, u1, u2)。"""
    r = client.post("/api/circles", json={"name": "记忆层测试圈"})
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


def _wait_processed(client: TestClient, fid: str, author_id: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = client.get(f"/api/fragments/{fid}", params={"user_id": author_id}).json()
        if f.get("processed"):
            return
        time.sleep(0.1)
    raise AssertionError(f"碎片 {fid} 异步处理超时")


def _pair_row(cid: str, uid1: str, uid2: str) -> sqlite3.Row:
    a, b = sorted((uid1, uid2))
    db = _db()
    row = db.execute(
        "SELECT * FROM pair_relationships WHERE circle_id=? AND user_a=? AND user_b=?",
        (cid, a, b),
    ).fetchone()
    db.close()
    return row


# ---------- 任务层 ----------

def test_task_runner_retry_then_success(monkeypatch) -> None:
    """失败按次数重试、指数退避；成功后记 success（纯 mock 模式不算 degraded）。"""
    sleeps: list[float] = []
    monkeypatch.setattr(tasks.time, "sleep", sleeps.append)
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("第一次炸了")

    status = tasks.run_task("flaky_task", "e1", flaky)
    assert status == "success"
    assert len(calls) == 2
    assert sleeps == [1]  # 首次失败后退避 2**0 = 1s

    db = _db()
    row = db.execute("SELECT status, error FROM task_runs WHERE task_name='flaky_task'").fetchone()
    db.close()
    assert row["status"] == "success" and row["error"] == ""


def test_task_runner_failed_records_error(monkeypatch) -> None:
    """重试耗尽记 failed + error，异常不外抛。"""
    monkeypatch.setattr(tasks.time, "sleep", lambda s: None)
    calls: list[int] = []

    def always() -> None:
        calls.append(1)
        raise ValueError("永远失败")

    status = tasks.run_task("always_fails", "e2", always, retries=2)
    assert status == "failed"
    assert len(calls) == 3  # 1 次 + 2 次重试

    db = _db()
    row = db.execute("SELECT status, error FROM task_runs WHERE task_name='always_fails'").fetchone()
    db.close()
    assert row["status"] == "failed" and "永远失败" in row["error"]


def test_task_runner_degraded_on_mock_fallback(monkeypatch) -> None:
    """配了 key 但真实调用失败回退 mock → degraded（不再静默）。"""
    monkeypatch.setattr(type(ai.settings), "llm_mock", property(lambda self: False))

    def _boom(prompt: str) -> dict:
        raise RuntimeError("模拟 DeepSeek 宕机")

    monkeypatch.setattr(deepseek, "chat_json", _boom)

    result: dict = {}
    status = tasks.run_task(
        "degraded_probe", "t1", lambda: result.update(ai.classify_fragment("想去测试降级"))
    )
    assert status == "degraded"
    assert result["tags"]  # 内容来自 mock 桩，功能本身没挂

    db = _db()
    row = db.execute("SELECT status FROM task_runs WHERE task_name='degraded_probe'").fetchone()
    db.close()
    assert row["status"] == "degraded"


# ---------- 亲密度纯函数 ----------

def test_compute_pair_score_weighting_and_normalization() -> None:
    """加权求和 + 非零信号归一化（互动恒 0 时自动从分母剔除）。"""
    s = memory.compute_pair_score
    # 全信号满分 → 1.0
    assert s({"semantic": 1.0, "interaction": 1.0, "common_wishes": 1.0, "common_topics": 1.0}) == 1.0
    # 互动恒 0 不参与分母：只有语义 0.5 → 0.5
    assert s({"semantic": 0.5, "interaction": 0.0, "common_wishes": 0.0, "common_topics": 0.0}) == 0.5
    # 语义 + 共同愿望双满分，分母 0.35+0.20 → 归一化后 1.0
    assert s({"semantic": 1.0, "common_wishes": 1.0}) == 1.0
    # 混合：(0.35*0.8 + 0.15*0.4) / (0.35+0.15) = 0.68
    assert s({"semantic": 0.8, "interaction": 0.0, "common_wishes": 0.0, "common_topics": 0.4}) == pytest.approx(0.68)
    # 全 0 → 0
    assert s({"semantic": 0.0, "interaction": 0.0, "common_wishes": 0.0, "common_topics": 0.0}) == 0.0
    # 计数型分量（共同愿望数）>1 封顶按 1 计
    assert s({"common_wishes": 5}) == 1.0


# ---------- 分量计算与可见性标记 ----------

def test_components_and_topic_visibility_markers(client: TestClient) -> None:
    """四分量落库 + 共同主题三类来源标记 + dirty → nightly 后画像/摘要生成且 dirty 清除。"""
    cid, u1, u2 = _make_circle(client)
    # 三组内容（bigram 互不重叠）：公开+公开 / 隐私+公开 / 隐私+隐私
    posts = [
        (u1["user_id"], "爬山赏枫叶", "public"),
        (u2["user_id"], "爬山赏枫叶", "public"),
        (u1["user_id"], "夜里写代码", "private"),
        (u2["user_id"], "夜里写代码", "public"),
        (u1["user_id"], "攒钱买相机", "private"),
        (u2["user_id"], "攒钱买相机", "private"),
    ]
    for uid, content, vis in posts:
        _wait_processed(client, _post(client, cid, uid, content, vis), uid)

    # 写路径打点：画像与用户对已标 dirty
    assert _pair_row(cid, u1["user_id"], u2["user_id"])["dirty"] == 1

    # 碎片管线全部经任务层落库
    db = _db()
    runs = db.execute("SELECT status FROM task_runs WHERE task_name='fragment_pipeline'").fetchall()
    db.close()
    assert len(runs) >= 6 and all(r["status"] == "success" for r in runs)

    # 每晚蒸馏（走任务层）
    stats = nightly.run()
    assert stats.get("success", 0) >= 1
    db = _db()
    run_row = db.execute(
        "SELECT status FROM task_runs WHERE task_name='nightly_distill' AND entity_id=?", (cid,)
    ).fetchone()
    db.close()
    assert run_row["status"] == "success"

    # 分量正确性（同文碎片：mock embedding 余弦 1.0，tags 完全一致 Jaccard 1.0）
    row = _pair_row(cid, u1["user_id"], u2["user_id"])
    assert row["semantic"] == pytest.approx(1.0, abs=1e-3)
    assert row["common_topics"] == 1.0
    assert row["common_wishes"] == 0
    assert row["interaction"] == 0.0  # 本圈无任何评论/点赞 → 互动分量为 0
    assert row["dirty"] == 0
    assert u1["nickname"] in row["summary"] and u2["nickname"] in row["summary"]

    # 共同主题三类来源可见性标记（隐私碎片照常参与计算，标记即证据）
    stored = {t["tag"]: t["source"] for t in json.loads(row["topics"])}
    for tag in mock._extract_tags("爬山赏枫叶"):
        assert stored[tag] == "public-public"
    for tag in mock._extract_tags("夜里写代码"):
        assert stored[tag] == "private-public"
    for tag in mock._extract_tags("攒钱买相机"):
        assert stored[tag] == "private-private"

    # 画像生成且 dirty 清除（建圈人「新朋友」也在圈里，共 3 行）
    db = _db()
    profiles = {
        r["user_id"]: r
        for r in db.execute("SELECT * FROM user_profiles WHERE circle_id=?", (cid,)).fetchall()
    }
    db.close()
    assert len(profiles) == 3
    for uid in (u1["user_id"], u2["user_id"]):
        p = profiles[uid]
        assert p["dirty"] == 0
        profile = json.loads(p["profile"])
        assert profile["topics"] and profile["summary"]


def test_common_wishes_component(client: TestClient) -> None:
    """共同愿望分量 = 两人经确认的共同愿望数；匹配管线经任务层。"""
    cid, u1, u2 = _make_circle(client)
    _wait_processed(client, _post(client, cid, u1["user_id"], "想去露营看星星"), u1["user_id"])
    _wait_processed(client, _post(client, cid, u2["user_id"], "想去露营看星星"), u2["user_id"])

    # 匹配管线走任务层（stale-while-revalidate：轮询到后台重算完成）
    for _ in range(20):
        cr = client.get("/api/wishes/common", params={"circle_id": cid}).json()
        if not cr.get("refreshing"):
            break
    common = cr["common_wishes"]
    assert any("露营" in c["content"] for c in common)
    db = _db()
    run_row = db.execute(
        "SELECT status FROM task_runs WHERE task_name='common_wishes' AND entity_id=?", (cid,)
    ).fetchone()
    db.close()
    assert run_row["status"] == "success"

    memory.refresh_dirty(cid)
    row = _pair_row(cid, u1["user_id"], u2["user_id"])
    assert row["common_wishes"] == 1  # mock embedding 余弦 1.0 ≥ 0.7，聚成同一簇
    assert row["secret_common_wishes"] == 0  # 双方公开，无秘密共同愿望


def test_private_wishes_count_in_component(client: TestClient) -> None:
    """隐私来源愿望计入 common_wishes 分量（算分对称）；双隐匹配记 secret_common_wishes。

    对照：include_private=False（对外端点现状）不计隐私愿望；摘要不含秘密愿望内容。
    """
    cid, u1, u2 = _make_circle(client)
    posts = [
        (u1["user_id"], "想去冰岛看极光", "private"),
        (u2["user_id"], "想去冰岛看极光", "public"),   # 一隐一公 → 计入分量，非 secret
        (u1["user_id"], "想去雪山泡温泉", "private"),
        (u2["user_id"], "想去雪山泡温泉", "private"),  # 双隐 → 计入分量且 secret+1
    ]
    for uid, content, vis in posts:
        _wait_processed(client, _post(client, cid, uid, content, vis), uid)

    # 对外口径（默认）：隐私愿望不参与匹配，两簇都不可见
    assert wishes.compute_common_wishes(cid) == []
    common = client.get("/api/wishes/common", params={"circle_id": cid}).json()["common_wishes"]
    assert common == []
    # 记忆层口径：隐私来源照常算分
    assert len(wishes.compute_common_wishes(cid, include_private=True)) == 2

    memory.refresh_dirty(cid)
    row = _pair_row(cid, u1["user_id"], u2["user_id"])
    assert row["common_wishes"] == 2  # 一隐一公 + 双隐都计入
    assert row["secret_common_wishes"] == 1  # 仅双隐那簇，只记数量不记内容
    # 摘要只基于可展示材料：隐私主题与秘密愿望内容不进摘要文本
    assert "极光" not in row["summary"] and "温泉" not in row["summary"]


def test_pair_wish_counts_three_buckets(client: TestClient) -> None:
    """_pair_wish_counts 三口径读 wishes.visibility 列：total / secret（双隐）/ public（双公开）。"""
    cid, u1, u2 = _make_circle(client)
    posts = [
        (u1["user_id"], "想去露营看星星", "public"),
        (u2["user_id"], "想去露营看星星", "public"),   # 双公开 → total+1, public+1
        (u1["user_id"], "想去冰岛看极光", "private"),
        (u2["user_id"], "想去冰岛看极光", "public"),   # 一隐一公 → 仅 total+1
        (u1["user_id"], "想去雪山泡温泉", "private"),
        (u2["user_id"], "想去雪山泡温泉", "private"),  # 双隐 → total+1, secret+1
    ]
    for uid, content, vis in posts:
        _wait_processed(client, _post(client, cid, uid, content, vis), uid)

    a, b = sorted((u1["user_id"], u2["user_id"]))
    db = _db()
    stats = memory._pair_wish_counts(db, cid)
    db.close()
    assert stats[(a, b)] == {"total": 3, "secret": 1, "public": 1}


# ---------- 周报改读持久数据 ----------

def test_report_key_connections_from_pair_table(client: TestClient) -> None:
    """key_connections 来自 pair_relationships（不再现算），且只含公开来源的连接。"""
    cid, u1, u2 = _make_circle(client)
    for uid, content, vis in (
        (u1["user_id"], "爬山赏枫叶", "public"),
        (u2["user_id"], "爬山赏枫叶", "public"),
        (u1["user_id"], "攒钱买相机", "private"),
        (u2["user_id"], "攒钱买相机", "private"),
    ):
        _wait_processed(client, _post(client, cid, uid, content, vis), uid)

    # 生成（内部先补跑 dirty）：公开来源的共同主题进连接，隐私来源不进
    r = client.post("/api/reports/generate", json={"circle_id": cid})
    assert r.status_code == 200, r.text
    report = client.get(f"/api/reports/{r.json()['report_id']}").json()
    assert len(report["key_connections"]) == 1
    assert "爬山" in report["key_connections"][0]
    assert "攒钱" not in report["key_connections"][0]
    assert "攒钱" not in report["content"]
    db = _db()
    run_row = db.execute(
        "SELECT status FROM task_runs WHERE task_name='weekly_report' AND entity_id LIKE ?",
        (f"{cid}:%",),
    ).fetchone()
    db.close()
    assert run_row["status"] == "success"

    # 证明读的是表而不是现算：手工改写 topics 后用另一周再生成，
    # 「望远镜观星」没有任何碎片出现过，只能来自 pair_relationships
    a, b = sorted((u1["user_id"], u2["user_id"]))
    crafted = json.dumps(
        [
            {"tag": "望远镜观星", "source": "public-public"},
            {"tag": "秘密花园", "source": "private-private"},
        ],
        ensure_ascii=False,
    )
    db = _db()
    db.execute(
        "UPDATE pair_relationships SET topics=? WHERE circle_id=? AND user_a=? AND user_b=?",
        (crafted, cid, a, b),
    )
    db.commit()
    db.close()

    r = client.post(
        "/api/reports/generate",
        json={"circle_id": cid, "week_start": "2026-08-03", "week_end": "2026-08-09"},
    )
    assert r.status_code == 200, r.text
    report2 = client.get(f"/api/reports/{r.json()['report_id']}").json()
    nick = {u1["user_id"]: u1["nickname"], u2["user_id"]: u2["nickname"]}
    assert report2["key_connections"] == [
        f"{nick[a]} 和 {nick[b]} 都关注着「望远镜观星」，要不要聊聊？"
    ]
    assert all("秘密花园" not in c for c in report2["key_connections"])
