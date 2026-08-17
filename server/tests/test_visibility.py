"""可见性（隐私地基）测试：private 碎片/愿望对他人不可见、不进知识库 / 共同愿望 / 周报。

运行：cd server && .venv/bin/python -m pytest tests/test_visibility.py -v
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import wishes as wishes_svc  # noqa: E402


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
    r = client.post("/api/circles", json={"name": "可见性测试圈"})
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


def _wait_processed(client: TestClient, fid: str, author_id: str, timeout: float = 10.0) -> dict:
    """以作者身份轮询详情，等异步管线跑完（TestClient 会等 BackgroundTasks，这里兜底）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = client.get(f"/api/fragments/{fid}", params={"user_id": author_id}).json()
        if f.get("processed"):
            return f
        time.sleep(0.1)
    raise AssertionError(f"碎片 {fid} 异步处理超时")


def _list(client: TestClient, cid: str, **params) -> dict:
    return client.get("/api/fragments", params={"circle_id": cid, **params}).json()


def test_list_visibility(client: TestClient) -> None:
    """默认 feed = 全圈公开 + 我的隐私；不带身份只见公开。"""
    cid, u1, u2 = _make_circle(client)
    pub1 = _post(client, cid, u1["user_id"], "今天加班好累，只想躺着")
    priv1 = _post(client, cid, u1["user_id"], "这条只给自己看", visibility="private")
    pub2 = _post(client, cid, u2["user_id"], "最近单曲循环一首很温柔的歌")

    as_u2 = _list(client, cid, user_id=u2["user_id"])
    ids = {f["id"] for f in as_u2["fragments"]}
    assert pub1 in ids and pub2 in ids and priv1 not in ids
    assert as_u2["total"] == len(as_u2["fragments"])

    ids = {f["id"] for f in _list(client, cid, user_id=u1["user_id"])["fragments"]}
    assert {pub1, priv1, pub2} <= ids

    ids = {f["id"] for f in _list(client, cid)["fragments"]}
    assert pub1 in ids and pub2 in ids and priv1 not in ids


def test_detail_visibility(client: TestClient) -> None:
    """隐私碎片详情对非作者 404（不暴露存在性）。"""
    cid, u1, u2 = _make_circle(client)
    fid = _post(client, cid, u1["user_id"], "私密日记一篇", visibility="private")

    r = client.get(f"/api/fragments/{fid}", params={"user_id": u1["user_id"]})
    assert r.status_code == 200 and r.json()["visibility"] == "private"
    assert client.get(f"/api/fragments/{fid}", params={"user_id": u2["user_id"]}).status_code == 404
    assert client.get(f"/api/fragments/{fid}").status_code == 404

    pub = _post(client, cid, u1["user_id"], "公开的一条")
    assert client.get(f"/api/fragments/{pub}", params={"user_id": u2["user_id"]}).status_code == 200


def test_author_filter(client: TestClient) -> None:
    """author 筛选：author=本人含其隐私碎片，author=他人仅其公开碎片。"""
    cid, u1, u2 = _make_circle(client)
    a_pub = _post(client, cid, u1["user_id"], "阿澈的公开碎片")
    a_priv = _post(client, cid, u1["user_id"], "阿澈的隐私碎片", visibility="private")
    b_pub = _post(client, cid, u2["user_id"], "丫丫的公开碎片")

    r = _list(client, cid, user_id=u1["user_id"], author=u1["user_id"])["fragments"]
    assert {f["id"] for f in r} == {a_pub, a_priv}

    r = _list(client, cid, user_id=u2["user_id"], author=u1["user_id"])["fragments"]
    assert {f["id"] for f in r} == {a_pub}

    r = _list(client, cid, user_id=u1["user_id"], author=u2["user_id"])["fragments"]
    assert {f["id"] for f in r} == {b_pub}

    # 不传 author 的默认 feed 不受影响
    r = _list(client, cid, user_id=u2["user_id"])["fragments"]
    assert {f["id"] for f in r} == {a_pub, b_pub}


def test_related_filters_private(client: TestClient) -> None:
    """相关推荐排除他人隐私碎片；隐私碎片本身对他人 404。"""
    cid, u1, u2 = _make_circle(client)
    # 三条同文碎片：mock embedding 下余弦 = 1.0，必然 ≥ 0.7 阈值
    subject = _post(client, cid, u1["user_id"], "想去山顶看流星雨")
    priv_same = _post(client, cid, u2["user_id"], "想去山顶看流星雨", visibility="private")
    pub_same = _post(client, cid, u2["user_id"], "想去山顶看流星雨")
    for fid, uid in ((subject, u1["user_id"]), (priv_same, u2["user_id"]), (pub_same, u2["user_id"])):
        _wait_processed(client, fid, uid)

    r = client.get(f"/api/fragments/{subject}/related", params={"user_id": u1["user_id"]})
    assert r.status_code == 200
    related_ids = {f["id"] for f in r.json()["related"]}
    assert pub_same in related_ids
    assert priv_same not in related_ids

    assert (
        client.get(f"/api/fragments/{priv_same}/related", params={"user_id": u1["user_id"]}).status_code
        == 404
    )
    assert (
        client.get(f"/api/fragments/{priv_same}/related", params={"user_id": u2["user_id"]}).status_code
        == 200
    )


def test_private_not_archived(client: TestClient) -> None:
    """隐私碎片分类照常（is_knowledge=1），但不归档进知识库。"""
    cid, u1, _ = _make_circle(client)
    priv = _post(
        client, cid, u1["user_id"],
        "私藏攻略 https://example.com/secret-guide-123 自己看", visibility="private",
    )
    pub = _post(client, cid, u1["user_id"], "公开攻略 https://example.com/public-guide-456 大家看")
    f_priv = _wait_processed(client, priv, u1["user_id"])
    _wait_processed(client, pub, u1["user_id"])
    assert f_priv["is_knowledge"]

    items = client.get("/api/knowledge", params={"circle_id": cid}).json()["items"]
    urls = [i["url"] for i in items]
    assert any("public-guide-456" in u for u in urls)
    assert not any("secret-guide-123" in u for u in urls)
    assert all("私藏攻略" not in (i["title"] + i["summary"]) for i in items)


def test_private_wish_not_in_common(client: TestClient) -> None:
    """隐私愿望：列表仅本人可见，共同愿望匹配只考虑公开愿望。"""
    cid, u1, u2 = _make_circle(client)
    # 对照组：两条公开同文愿望应当匹配上（mock embedding 余弦 = 1.0）
    w1 = _post(client, cid, u1["user_id"], "想去露营看星星")
    w2 = _post(client, cid, u2["user_id"], "想去露营看星星")
    # 实验组：阿澈的隐私愿望 + 丫丫的公开同文愿望，不应产生匹配
    w3 = _post(client, cid, u1["user_id"], "想去冰岛看极光", visibility="private")
    w4 = _post(client, cid, u2["user_id"], "想去冰岛看极光")
    for fid, uid in ((w1, u1["user_id"]), (w2, u2["user_id"]), (w3, u1["user_id"]), (w4, u2["user_id"])):
        _wait_processed(client, fid, uid)

    # stale-while-revalidate：轮询到后台重算完成（TestClient 同步跑后台任务）
    for _ in range(20):
        cr = client.get("/api/wishes/common", params={"circle_id": cid}).json()
        if not cr.get("refreshing"):
            break
    common = cr["common_wishes"]
    assert any("露营" in c["content"] for c in common)
    assert all("极光" not in c["content"] for c in common)

    as_u2 = client.get("/api/wishes", params={"circle_id": cid, "user_id": u2["user_id"]}).json()["wishes"]
    assert not any(w["user_id"] == u1["user_id"] and "极光" in w["content"] for w in as_u2)
    as_u1 = client.get("/api/wishes", params={"circle_id": cid, "user_id": u1["user_id"]}).json()["wishes"]
    assert any(w["user_id"] == u1["user_id"] and "极光" in w["content"] for w in as_u1)


def test_plan_participants_exclude_private(client: TestClient) -> None:
    """「一起去」方案的参与匹配只考虑公开愿望：隐私愿望作者不出现在他人方案的 participants 里。"""
    cid, u1, u2 = _make_circle(client)
    # 丫丫公开愿望 + 阿澈同文隐私愿望（mock embedding 余弦 = 1.0，不过滤必然匹配上）
    w_pub = _post(client, cid, u2["user_id"], "想去山里徒步看云海")
    w_priv = _post(client, cid, u1["user_id"], "想去山里徒步看云海", visibility="private")
    _wait_processed(client, w_pub, u2["user_id"])
    _wait_processed(client, w_priv, u1["user_id"])

    wishes = client.get("/api/wishes", params={"circle_id": cid, "user_id": u2["user_id"]}).json()["wishes"]
    ya_wish = next(w for w in wishes if w["user_id"] == u2["user_id"] and "云海" in w["content"])
    # 方案接口已改异步（见 BUG-003 共识）：参与人口径直调服务层验证
    result = wishes_svc.generate_plan(ya_wish["id"])
    participants = result.get("participants", [])
    assert "丫丫" in participants
    assert "阿澈" not in participants


def test_report_uses_public_only(client: TestClient) -> None:
    """周报内容来源只含公开碎片 / 公开愿望。"""
    cid, u1, u2 = _make_circle(client)
    priv_wish = _post(client, cid, u1["user_id"], "想去秘密山谷求婚", visibility="private")
    pub_wish = _post(client, cid, u2["user_id"], "想去郊外野餐放风筝")
    _wait_processed(client, priv_wish, u1["user_id"])
    _wait_processed(client, pub_wish, u2["user_id"])

    r = client.post("/api/reports/generate", json={"circle_id": cid})
    assert r.status_code == 200
    report = client.get(f"/api/reports/{r.json()['report_id']}").json()
    assert "野餐" in report["content"]
    assert "秘密山谷" not in report["content"]
    assert all("秘密山谷" not in c for c in report["key_connections"])


def test_visibility_validation(client: TestClient) -> None:
    """非法 visibility 400；不传默认 public。"""
    cid, u1, _ = _make_circle(client)
    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "非法可见性", "visibility": "friends"},
    )
    assert r.status_code == 400

    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "默认公开的一条"},
    )
    assert r.status_code == 200
    f = client.get(f"/api/fragments/{r.json()['id']}").json()
    assert f["visibility"] == "public"


# ---------- 愿望（wishes）自身 visibility ----------

def test_manual_private_wish(client: TestClient) -> None:
    """手动私密愿望：本人列表可见、他人/匿名不可见；非法值 400；不传默认 public。"""
    cid, u1, u2 = _make_circle(client)

    r = client.post(
        "/api/wishes",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "想一个人去看海",
              "visibility": "private"},
    )
    assert r.status_code == 200
    priv_id = r.json()["id"]
    r = client.post(
        "/api/wishes",
        json={"circle_id": cid, "user_id": u2["user_id"], "content": "想学滑板"},
    )
    pub_id = r.json()["id"]

    as_u1 = client.get("/api/wishes", params={"circle_id": cid, "user_id": u1["user_id"]}).json()["wishes"]
    by_id = {w["id"]: w for w in as_u1}
    assert by_id[priv_id]["visibility"] == "private"
    assert by_id[pub_id]["visibility"] == "public"  # 不传默认 public

    ids = {w["id"] for w in client.get("/api/wishes", params={"circle_id": cid, "user_id": u2["user_id"]}).json()["wishes"]}
    assert priv_id not in ids and pub_id in ids
    ids = {w["id"] for w in client.get("/api/wishes", params={"circle_id": cid}).json()["wishes"]}
    assert priv_id not in ids and pub_id in ids

    r = client.post(
        "/api/wishes",
        json={"circle_id": cid, "user_id": u1["user_id"], "content": "非法可见性", "visibility": "friends"},
    )
    assert r.status_code == 400


def test_manual_private_wish_not_matched(client: TestClient) -> None:
    """手动私密愿望不进共同愿望匹配，其作者也不出现在他人方案的 participants 里。"""
    cid, u1, u2 = _make_circle(client)
    # 对照组：两条公开同文手动愿望应当匹配上（mock embedding 余弦 = 1.0）
    client.post("/api/wishes", json={"circle_id": cid, "user_id": u1["user_id"], "content": "想去露营看星星"})
    client.post("/api/wishes", json={"circle_id": cid, "user_id": u2["user_id"], "content": "想去露营看星星"})
    # 实验组：阿澈私密手动愿望 + 丫丫公开同文手动愿望，不应产生匹配
    client.post("/api/wishes", json={"circle_id": cid, "user_id": u1["user_id"],
                                     "content": "想去敦煌看壁画", "visibility": "private"})
    r = client.post("/api/wishes", json={"circle_id": cid, "user_id": u2["user_id"], "content": "想去敦煌看壁画"})
    ya_dunhuang = r.json()["id"]

    # stale-while-revalidate：轮询到后台重算完成（TestClient 同步跑后台任务）
    for _ in range(20):
        cr = client.get("/api/wishes/common", params={"circle_id": cid}).json()
        if not cr.get("refreshing"):
            break
    common = cr["common_wishes"]
    assert any("露营" in c["content"] for c in common)
    assert all("敦煌" not in c["content"] for c in common)

    # 方案接口已改异步（见 BUG-003 共识）：参与人口径直调服务层验证
    result = wishes_svc.generate_plan(ya_dunhuang)
    participants = result.get("participants", [])
    assert "丫丫" in participants and "阿澈" not in participants


def test_pipeline_wish_visibility_column(client: TestClient) -> None:
    """碎片来源愿望的 wishes.visibility 列 = 来源碎片可见性（直读库验证）。"""
    cid, u1, _ = _make_circle(client)
    priv = _post(client, cid, u1["user_id"], "想去无人岛躺平一周", visibility="private")
    pub = _post(client, cid, u1["user_id"], "想去城郊骑行一圈")
    _wait_processed(client, priv, u1["user_id"])
    _wait_processed(client, pub, u1["user_id"])

    db = _db()
    rows = {
        r["fragment_id"]: r["visibility"]
        for r in db.execute("SELECT fragment_id, visibility FROM wishes WHERE circle_id = ?", (cid,)).fetchall()
    }
    db.close()
    assert rows[priv] == "private"
    assert rows[pub] == "public"


def test_wishes_visibility_migration() -> None:
    """存量迁移（子进程独立库）：老库 wishes 无 visibility 列 → 补列，
    碎片来源愿望同步来源碎片可见性，手动愿望默认 public；重复执行幂等。"""
    db_path = os.path.join(tempfile.mkdtemp(prefix="us_test_mig_"), "old.db")
    conn = sqlite3.connect(db_path)
    # 第 1 期形态老库：fragments 已有 visibility，wishes 没有
    conn.execute(
        """CREATE TABLE fragments (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, circle_id TEXT NOT NULL,
            content TEXT NOT NULL, type TEXT DEFAULT 'text', tags TEXT DEFAULT '[]',
            mood TEXT DEFAULT '', embedding BLOB, created_at TEXT NOT NULL,
            is_knowledge INTEGER DEFAULT 0, is_wish INTEGER DEFAULT 0,
            wish_category TEXT DEFAULT '', ai_summary TEXT DEFAULT '', processed INTEGER DEFAULT 0,
            visibility TEXT NOT NULL DEFAULT 'public')"""
    )
    conn.execute(
        """CREATE TABLE wishes (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, circle_id TEXT NOT NULL,
            content TEXT NOT NULL, category TEXT DEFAULT 'do', fragment_id TEXT DEFAULT '',
            status TEXT DEFAULT 'active', matched_users TEXT DEFAULT '[]', embedding BLOB,
            plan TEXT, created_at TEXT NOT NULL)"""
    )
    conn.execute("INSERT INTO fragments (id, user_id, circle_id, content, created_at, visibility)"
                 " VALUES ('f1','u1','c1','隐私碎片','2026-08-01T10:00:00','private')")
    conn.execute("INSERT INTO fragments (id, user_id, circle_id, content, created_at, visibility)"
                 " VALUES ('f2','u1','c1','公开碎片','2026-08-01T11:00:00','public')")
    conn.execute("INSERT INTO wishes (id, user_id, circle_id, content, fragment_id, created_at)"
                 " VALUES ('w1','u1','c1','来自隐私碎片的愿望','f1','2026-08-01T10:00:00')")
    conn.execute("INSERT INTO wishes (id, user_id, circle_id, content, fragment_id, created_at)"
                 " VALUES ('w2','u1','c1','来自公开碎片的愿望','f2','2026-08-01T11:00:00')")
    conn.execute("INSERT INTO wishes (id, user_id, circle_id, content, fragment_id, created_at)"
                 " VALUES ('w3','u1','c1','手动添加的愿望','','2026-08-01T12:00:00')")
    conn.commit()
    conn.close()

    # 子进程跑真实 init_db：本进程 get_conn 已绑定会话库，无法就地换库
    code = (
        f"import sys, json; sys.path.insert(0, {SERVER_DIR!r});"
        "from app.db.database import init_db, get_conn;"
        "init_db(); init_db();"
        "rows = get_conn().execute('SELECT id, visibility FROM wishes').fetchall();"
        "print(json.dumps({r['id']: r['visibility'] for r in rows}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "DB_PATH": db_path},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    vis = json.loads(out.stdout.strip())
    assert vis == {"w1": "private", "w2": "public", "w3": "public"}
