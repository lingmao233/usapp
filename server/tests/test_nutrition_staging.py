"""营养共建信任管线测试：手动添加+联网核验（通过/离谱）、查表未命中联网兜底入 staging、
确认入账计认可（同人同物去重）、3 次认可晋升正式表并删 staging、staging 匹配优先级（正式表优先）。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_nutrition_staging.py -v
"""
import os
import sys
import tempfile
import uuid

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_staging_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import fakes  # noqa: E402
from app import ai  # noqa: E402
from app.config import settings  # noqa: E402
from app.db.database import encode_embedding, get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import nutrition  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def env(client: TestClient):
    """每个测试前清 staging 两表 + 正式成分表；收尾清掉测试加的行并补灌 vendor 数据。

    「正式表查不到」是本文件全部用例的前提——共享库的表状态取决于上游文件的执行顺序
    （test_nutrition 的清表/补灌都会改变它），不可依赖，每个测试前显式清空；
    收尾 init_db 把 vendor 数据补灌回去（空表才灌，幂等），不给下游文件留空表。
    测试食物名统一「共建」前缀，正式表测试行按此前缀清理（vendor 数据无此前缀）。
    """
    conn = get_conn()
    conn.execute("DELETE FROM food_staging_approvals")
    conn.execute("DELETE FROM food_nutrition_staging")
    conn.execute("DELETE FROM food_nutrition")
    conn.commit()
    yield client
    conn.execute("DELETE FROM food_staging_approvals")
    conn.execute("DELETE FROM food_nutrition_staging")
    conn.execute("DELETE FROM food_nutrition WHERE name LIKE '共建%'")
    conn.commit()
    init_db()


def _new_user(client: TestClient, name: str = "共建测试圈") -> str:
    return client.post("/api/circles", json={"name": name, "nickname": "阿澈"}).json()["account_id"]


def _image_url() -> str:
    """造一张站内存量的假图（识别路由只校验文件存在，不解析内容）。
    每次内容唯一：图片型 RAG 按字节哈希取向量，相同字节会被当成同一张图命中历史。"""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    (settings.upload_dir / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + uuid.uuid4().bytes)
    return f"/api/uploads/{stem}.jpg"


def _staging_row(name: str):
    return get_conn().execute(
        "SELECT * FROM food_nutrition_staging WHERE name = ?", (name,)
    ).fetchone()


def _web(kcal: float):
    """受控联网搜索结果（宏量可空）。"""
    return {"kcal_per_100g": kcal, "protein_per_100g": 1.0, "fat_per_100g": None, "cho_per_100g": None}


# ---------- ai.web_search_food：开关与降级 ----------

def test_web_search_food_off_by_default() -> None:
    """LLM_WEB_SEARCH 非 on（默认 off/空串等效）：返回 None（不联网降级），存量行为不变。"""
    assert settings.web_search_enabled is False
    assert ai.web_search_food("米饭") is None


def test_web_search_food_fake_stub(monkeypatch) -> None:
    """开关 on：返回确定性桩；空名搜不到返回 None。"""
    monkeypatch.setattr(settings, "LLM_WEB_SEARCH", "on")
    assert ai.web_search_food("苹果") == {
        "kcal_per_100g": 200.0,
        "protein_per_100g": 8.0,
        "fat_per_100g": 5.0,
        "cho_per_100g": 30.0,
    }
    assert ai.web_search_food("  ") is None


def test_web_search_food_kimi_channel(monkeypatch) -> None:
    """树洞配置是 Kimi 系时，联网查询走 $web_search 回声协议（真联网）；
    阿里 enable_search 写法在 Kimi 端点会被忽略（假联网），所以必须分流。"""
    monkeypatch.setattr(settings, "LLM_WEB_SEARCH", "on")
    monkeypatch.setattr(settings, "TREEHOLE_API_KEY", "test-key")
    captured: dict = {}

    def fake_cm(messages, cfg=None, tools=None, timeout=120.0, max_tool_rounds=3):
        captured.update(cfg=cfg, tools=tools)
        return '{"kcal_per_100g": 45.0, "protein_per_100g": null}'

    monkeypatch.setattr(ai.llm, "chat_messages", fake_cm)
    monkeypatch.setattr(ai, "web_search_food", fakes.REAL_IMPLS["web_search_food"])  # 脱桩走真身
    out = ai.web_search_food("乌龙茶")
    assert out is not None and out["kcal_per_100g"] == 45.0
    assert captured["tools"] == [{"type": "builtin_function", "function": {"name": "$web_search"}}]
    # TREEHOLE_BASE_URL 空 → Kimi 默认值
    assert captured["cfg"] == ("test-key", "https://api.moonshot.cn/v1", "kimi-k2.6")


# ---------- 手动添加：入 staging + 异步联网核验 ----------

def test_add_food_verify_pass(env, monkeypatch) -> None:
    """手动添加：响应即 staging 行（verified=false 待核验）；后台核验与联网值差 ≤50% → verified=1。"""
    uid = _new_user(env)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="": _web(110.0))
    r = env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": "共建燕麦奶", "kcal_per_100g": 100, "protein_per_100g": 1.2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["food"]["verified"] is False  # 响应时核验尚未完成（异步）
    assert body["food"]["source"] == "user" and body["food"]["approvals"] == 0
    assert "待核实" in body["message"]
    # TestClient 会同步跑完 BackgroundTasks：核验通过 → verified=1
    row = _staging_row("共建燕麦奶")
    assert row["verified"] == 1 and row["protein_per_100g"] == 1.2


def test_add_food_verify_outlier_stays_unverified(env, monkeypatch) -> None:
    """离谱核验：联网值与用户值差 >50% → verified 保持 0（待核实），不晋升为可信数据。"""
    uid = _new_user(env)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="": _web(300.0))  # 用户填 100，差 200%
    r = env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": "共建能量胶", "kcal_per_100g": 100})
    assert r.status_code == 200, r.text
    assert r.json()["food"]["verified"] is False
    assert _staging_row("共建能量胶")["verified"] == 0


def test_add_food_verify_skipped_when_search_fails(env, monkeypatch) -> None:
    """搜索失败/未开启（返回 None）：不核验，verified 保持 0，添加本身不受影响。"""
    uid = _new_user(env)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="": None)
    r = env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": "共建魔芋面", "kcal_per_100g": 20})
    assert r.status_code == 200, r.text
    assert _staging_row("共建魔芋面")["verified"] == 0


def test_add_food_duplicate_name_idempotent(env, monkeypatch) -> None:
    """同名重复添加幂等：不插新行，created=false 返回已存在行。"""
    uid = _new_user(env)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="": _web(100.0))
    r1 = env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": "共建魔芋面", "kcal_per_100g": 20})
    assert r1.json()["created"] is True
    r2 = env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": "共建魔芋面", "kcal_per_100g": 99})
    assert r2.status_code == 200, r2.text
    assert r2.json()["created"] is False
    assert r2.json()["food"]["kcal_per_100g"] == 20  # 保留首次录入值
    assert get_conn().execute(
        "SELECT COUNT(*) AS c FROM food_nutrition_staging WHERE name = '共建魔芋面'"
    ).fetchone()["c"] == 1


def test_add_food_validation(env) -> None:
    """入参校验：名称必填、kcal ∈ (0, 1000]、宏量 ≤100g；账号不存在 404。"""
    uid = _new_user(env)
    assert env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": " ", "kcal_per_100g": 100}).status_code == 400
    assert env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": "共建X", "kcal_per_100g": 0}).status_code == 400
    assert env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": "共建X", "kcal_per_100g": 1001}).status_code == 400
    assert env.post("/api/nutrition/foods", json={
        "account_id": uid, "name": "共建X", "kcal_per_100g": 100, "fat_per_100g": 101}).status_code == 400
    assert env.post("/api/nutrition/foods", json={
        "account_id": "不存在的账号", "name": "共建X", "kcal_per_100g": 100}).status_code == 404


def test_migrate_staging_approvals_fk_repair(env) -> None:
    """悬空外键修复（BUG-020）：模拟被 RENAME 改写引用的老库 → 迁移重建后
    认可写入不再报 no such table，存量行搬回。"""
    from app.db import database as db
    conn = get_conn()
    conn.commit()  # 先落干净，PRAGMA foreign_keys 在事务里不生效
    conn.execute("PRAGMA foreign_keys=OFF")  # 造遗迹要绕开 FK（悬空引用写不进去）
    conn.execute("DROP TABLE food_staging_approvals")
    conn.execute("""CREATE TABLE food_staging_approvals (
        staging_id INTEGER NOT NULL REFERENCES "food_nutrition_staging_old"(id),
        account_id TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY (staging_id, account_id))""")
    conn.execute(
        "INSERT INTO food_nutrition_staging (name, kcal_per_100g, source, verified, approvals, created_at)"
        " VALUES ('修复测试食品', 100, 'web', 1, 0, '2026-01-01')")
    sid = conn.execute(
        "SELECT id FROM food_nutrition_staging WHERE name = '修复测试食品'").fetchone()["id"]
    conn.execute(
        "INSERT INTO food_staging_approvals (staging_id, account_id, created_at)"
        " VALUES (?, 'acc-x', '2026-01-01')", (sid,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()

    db._migrate_staging_approvals_fk()

    n = conn.execute(
        "SELECT COUNT(*) AS c FROM food_staging_approvals WHERE staging_id = ?", (sid,)
    ).fetchone()["c"]
    assert n == 1  # 存量行搬回
    # 修复后写入正常（修复前报 no such table: main.food_nutrition_staging_old）
    conn.execute(
        "INSERT OR IGNORE INTO food_staging_approvals (staging_id, account_id, created_at)"
        " VALUES (?, 'acc-y', '2026-01-02')", (sid,))
    conn.commit()


# ---------- 查表未命中 → 联网搜 → staging ----------

def _patch_recognize(monkeypatch, name: str, grams: float, model_kcal: float) -> None:
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": name, "grams": grams, "kcal": model_kcal}],
        "note": "伪装识别",
    })


def test_recognize_web_hit_writes_staging(env, monkeypatch) -> None:
    """联网兜底已移出热路径（5s 目标）：查表未命中 → 本次按模型估值标 model 立即返回；
    响应后后台任务联网入库（TestClient 内联执行）——与用户是否确认入账解耦，
    估值被丢弃也不影响入库；下次识别同食物直接命中 staging。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "共建神秘果", 150, 999)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="": _web(120.0))
    r = env.post("/api/calories/recognize", json={"account_id": uid, "image_url": _image_url()})
    assert r.status_code == 200, r.text
    item = r.json()["entry"]["items"][0]
    assert item == {"name": "共建神秘果", "kcal": 999, "source": "model", "grams": 150}
    # 后台已把联网值写入 staging（本条 pending 从未确认也已在库）
    row = _staging_row("共建神秘果")
    assert row is not None
    assert row["source"] == "web" and row["verified"] == 1 and row["approvals"] == 0
    assert row["kcal_per_100g"] == 120.0
    assert row["protein_per_100g"] == 1.0 and row["fat_per_100g"] is None

    # 下次识别：staging LIKE 命中，单价来自联网入库值（不再联网）
    r = env.post("/api/calories/recognize", json={"account_id": uid, "image_url": _image_url()})
    item2 = r.json()["entry"]["items"][0]
    assert item2["source"] == "staging" and item2["kcal"] == 180  # 120 × 150g
    assert item2["kcal_per_100g"] == 120.0


def test_recognize_web_miss_falls_back_to_model(env, monkeypatch) -> None:
    """查表未命中 + 联网搜不到（None）：回退模型估值 source=model，不写 staging。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "共建幻影菜", 100, 456)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="": None)
    r = env.post("/api/calories/recognize", json={"account_id": uid, "image_url": _image_url()})
    assert r.status_code == 200, r.text
    item = r.json()["entry"]["items"][0]
    assert item == {"name": "共建幻影菜", "kcal": 456, "source": "model", "grams": 100}
    assert _staging_row("共建幻影菜") is None


# ---------- 认可与晋升 ----------

def _recognize_and_confirm(client: TestClient, uid: str) -> dict:
    """走一遍 识别 → 确认入账，返回识别 entry（确认即对该 entry 里的 staging 条目计认可）。"""
    entry = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()}).json()["entry"]
    r = client.post("/api/calories", json={"account_id": uid, "id": entry["id"]})
    assert r.status_code == 200, r.text
    return entry


def test_confirm_counts_approval_dedup_same_account(env, monkeypatch) -> None:
    """确认入账计认可：同一账号对同一食物确认多次只计一次（去重表兜底）。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "共建能量棒", 100, 1)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="": _web(250.0))
    _recognize_and_confirm(env, uid)
    row = _staging_row("共建能量棒")
    assert row["approvals"] == 1
    # 同一账号第二次识别：staging LIKE 命中（source=staging，同一 staging_id），确认后不重复计
    entry2 = _recognize_and_confirm(env, uid)
    item2 = entry2["items"][0]
    assert item2["source"] == "staging" and item2["staging_id"] == row["id"]
    assert _staging_row("共建能量棒")["approvals"] == 1
    assert get_conn().execute(
        "SELECT COUNT(*) AS c FROM food_staging_approvals WHERE staging_id = ?", (row["id"],)
    ).fetchone()["c"] == 1


def test_promote_after_three_approvals(env, monkeypatch) -> None:
    """3 个不同账号认可 → 晋升正式 food_nutrition（含 embedding），staging 行与认可记录删除。"""
    _patch_recognize(monkeypatch, "共建花生酱", 100, 1)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="": _web(600.0))
    uids = [_new_user(env) for _ in range(3)]
    _recognize_and_confirm(env, uids[0])
    assert _staging_row("共建花生酱")["approvals"] == 1
    _recognize_and_confirm(env, uids[1])
    assert _staging_row("共建花生酱")["approvals"] == 2
    _recognize_and_confirm(env, uids[2])
    # 晋升：staging 行与认可去重记录删除
    assert _staging_row("共建花生酱") is None
    conn = get_conn()
    assert conn.execute("SELECT COUNT(*) AS c FROM food_staging_approvals").fetchone()["c"] == 0
    formal = conn.execute(
        "SELECT * FROM food_nutrition WHERE name = '共建花生酱'"
    ).fetchone()
    assert formal is not None and formal["kcal_per_100g"] == 600.0
    assert formal["embedding"] is not None
    # 晋升后走正式表匹配（source=table）
    hit = nutrition.match("共建花生酱")
    assert hit is not None and hit["source"] == "table" and hit["kcal_per_100g"] == 600.0


# ---------- staging 参与匹配：正式表优先 ----------

def test_match_formal_table_wins_over_staging(env) -> None:
    """同名数据正式表与 staging 都有时，match 命中正式表（source=table，无 staging_id）。"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO food_nutrition
           (name, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g, embedding)
           VALUES ('共建优先米饭', 100, NULL, NULL, NULL, ?)""",
        (encode_embedding(ai.embed_text("共建优先米饭")),),
    )
    conn.execute(
        """INSERT INTO food_nutrition_staging
           (name, kcal_per_100g, source, verified, approvals, created_at)
           VALUES ('共建优先米饭', 500, 'user', 0, 0, '2026-08-19T00:00:00')"""
    )
    conn.commit()
    hit = nutrition.match("共建优先米饭")
    assert hit is not None and hit["source"] == "table" and hit["kcal_per_100g"] == 100
    assert "staging_id" not in hit


def test_match_staging_hit_marked_pending(env) -> None:
    """正式表不中时 staging 参与匹配：命中标 source=staging 并带 staging_id（UI 显示「待核实」）。"""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO food_nutrition_staging
           (name, kcal_per_100g, protein_per_100g, source, verified, approvals, created_at)
           VALUES ('共建紫薯粥', 80, 1.1, 'web', 1, 0, '2026-08-19T00:00:00')"""
    )
    conn.commit()
    hit = nutrition.match("共建紫薯粥")
    assert hit is not None
    assert hit["source"] == "staging" and hit["staging_id"] == cur.lastrowid
    assert hit["kcal_per_100g"] == 80 and hit["protein_per_100g"] == 1.1
    assert nutrition.match("查无此菜共建") is None


# ---------- 品牌两级：种类+品牌 ----------

def _insert_food(name: str, brand: str, kcal: float) -> None:
    conn = get_conn()
    conn.execute(
        """INSERT OR IGNORE INTO food_nutrition
           (name, brand, kcal_per_100g, embedding) VALUES (?, ?, ?, ?)""",
        (name, brand, kcal, encode_embedding(ai.embed_text(name))),
    )
    conn.commit()


def test_brand_row_preferred_then_generic(env) -> None:
    """识别出品牌 → 品牌行优先；品牌行没有 → 落回通用行；不带品牌 → 通用行。"""
    _insert_food("共建火鸡面", "", 400.0)
    _insert_food("共建火鸡面", "三养", 470.0)

    hit = nutrition.match("共建火鸡面", "三养")
    assert hit["brand"] == "三养" and hit["kcal_per_100g"] == 470.0

    hit = nutrition.match("共建火鸡面", "白象")  # 白象行不存在 → 通用行兜底
    assert hit["brand"] == "" and hit["kcal_per_100g"] == 400.0

    hit = nutrition.match("共建火鸡面")
    assert hit["brand"] == "" and hit["kcal_per_100g"] == 400.0


def test_staging_brand_dedup_and_promote_keeps_brand(env) -> None:
    """同名不同品牌是两行 staging；晋升正式表时 brand 随行保留。"""
    acc = _new_user(env)
    r1 = nutrition.add_staging_food(acc, "共建品牌面", 500, brand="三养")
    r2 = nutrition.add_staging_food(acc, "共建品牌面", 380, brand="白象")
    assert r1["created"] and r2["created"]
    assert r1["food"]["id"] != r2["food"]["id"]

    sid = r1["food"]["id"]
    for _ in range(3):
        res = nutrition.approve_staging(sid, "acc_" + uuid.uuid4().hex[:6])
    assert res["promoted"] is True
    row = get_conn().execute(
        "SELECT * FROM food_nutrition WHERE name = '共建品牌面' AND brand = '三养'"
    ).fetchone()
    assert row is not None and row["kcal_per_100g"] == 500
    # 白象行还在 staging，不受晋升影响
    assert _staging_row("共建品牌面")["brand"] == "白象"
