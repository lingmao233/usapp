"""热量修复回归测试（BUG-021/022/023，见 docs/BUG记录.md）：

- BUG-021 名字纠正防震荡：改回原识别名撤销正向纠正（不追加反向行）；新纠正记录在
  raw_name 上；插入前清反向行——任何时刻一个识别名只有一个纠正方向
- BUG-022 联网值清洗：饮品先验拒绝（红茶 294）、kJ 误报折算、非饮品高值放行；
  识别→后台回填全链路不再让错值落全局 staging
- BUG-023 SSE 推送：staging 入库完成事件经 /api/calories/events 推给在线页面
- staging 管理面板：列表/改值（撞活行 409）/软删（匹配跳过、再添加复活）

运行：cd server && .venv-mac/bin/python -m pytest tests/test_calorie_fixes.py -v
"""
import os
import sys
import tempfile
import uuid

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_fixes_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.config import settings  # noqa: E402
from app.db.database import get_conn  # noqa: E402
from app.main import app  # noqa: E402
from app.services import events as events_mod  # noqa: E402
from app.services import nutrition  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def env(client: TestClient):
    """每个测试前清热量四表 + staging 两表 + 正式成分表（「查不到」是识别走模型估值的前提）；
    收尾补灌 vendor 数据，不给下游文件留空表。"""
    conn = get_conn()
    for table in ("calorie_entries", "calorie_name_corrections", "calorie_gram_corrections",
                  "calorie_food_images", "food_staging_approvals", "food_nutrition_staging",
                  "food_nutrition"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    yield client
    for table in ("food_staging_approvals", "food_nutrition_staging", "food_nutrition"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    from app.db.database import init_db
    init_db()


def _new_user(client: TestClient, name: str = "修复测试圈") -> str:
    return client.post("/api/circles", json={"name": name, "nickname": "阿澈"}).json()["account_id"]


def _image_url() -> str:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    (settings.upload_dir / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + uuid.uuid4().bytes)
    return f"/api/uploads/{stem}.jpg"


def _patch_recognize(monkeypatch, name: str, grams: float, kcal: float) -> None:
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": name, "grams": grams, "kcal": kcal}], "note": ""})


def _recognize(client: TestClient, uid: str) -> dict:
    return client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()}).json()["entry"]


def _corrections(uid: str) -> list[dict]:
    rows = get_conn().execute(
        """SELECT recognized_name, corrected_name FROM calorie_name_corrections
           WHERE account_id = ? ORDER BY rowid""", (uid,)).fetchall()
    return [{"recognized": r["recognized_name"], "corrected": r["corrected_name"]} for r in rows]


def _rename(client: TestClient, uid: str, entry_id: str, name: str):
    return client.put(f"/api/calories/{entry_id}/items",
                      json={"account_id": uid, "index": 0, "name": name})


# ---------- BUG-021：名字纠正防震荡 ----------


def test_rename_back_to_raw_name_revokes_correction(env, monkeypatch) -> None:
    """红茶被改成火腿后再改回红茶：撤销 (红茶→火腿) 正向行，不追加反向行——
    双向行会让「识别红茶变火腿、识别火腿变红茶」来回震荡（本次线上故障的根因）。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "红茶", 50, 2)
    entry = _recognize(env, uid)
    assert "raw_name" not in entry["items"][0]  # 未纠正时 raw_name 不落
    _rename(env, uid, entry["id"], "火腿")
    assert _corrections(uid) == [{"recognized": "红茶", "corrected": "火腿"}]
    _rename(env, uid, entry["id"], "红茶")
    assert _corrections(uid) == []  # 改回原名 = 撤销，不是新增反向行
    # 撤销后识别不再被改写
    entry2 = _recognize(env, uid)
    assert entry2["items"][0]["name"] == "红茶" and "raw_name" not in entry2["items"][0]


def test_rename_records_against_raw_name_and_clears_reverse_edge(env, monkeypatch) -> None:
    """被纠正过的条目再改名：纠正记在 raw_name 上（模型下次还输出这个名字）；
    插入前清反向行，保证任何时刻一个名字对只有一个方向。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "红茶", 50, 2)
    entry = _recognize(env, uid)
    _rename(env, uid, entry["id"], "火腿")     # 纠正 (红茶→火腿)
    _rename(env, uid, entry["id"], "正山小种")  # 再改：记在 raw_name(红茶) 上
    rows = _corrections(uid)
    assert {"recognized": "红茶", "corrected": "正山小种"} in rows
    assert not any(r["recognized"] == "火腿" for r in rows)  # 不产生火腿→x 反向行
    # 反向清理：已有 (柠檬茶→绿茶) 时，把别的条目从绿茶改回柠檬茶 → 旧行被清
    _patch_recognize(monkeypatch, "柠檬茶", 200, 40)
    e2 = _recognize(env, uid)
    _rename(env, uid, e2["id"], "绿茶")
    assert {"recognized": "柠檬茶", "corrected": "绿茶"} in _corrections(uid)
    _patch_recognize(monkeypatch, "绿茶", 200, 40)
    e3 = _recognize(env, uid)
    _rename(env, uid, e3["id"], "柠檬茶")
    assert {"recognized": "柠檬茶", "corrected": "绿茶"} not in _corrections(uid)


def test_rename_after_correction_applies_on_recognition(env, monkeypatch) -> None:
    """纠正生效链路回归：改名后，下次识别同名直接用纠正名（并有 raw_name 留底）。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "薯条", 100, 320)
    entry = _recognize(env, uid)
    _rename(env, uid, entry["id"], "炸薯条")
    entry2 = _recognize(env, uid)
    it = entry2["items"][0]
    assert it["name"] == "炸薯条" and it["raw_name"] == "薯条"


# ---------- BUG-022：联网值清洗 ----------


def test_sanitize_drink_prior_rejects_inflated_values() -> None:
    """饮品先验：红茶 294 拒（本次故障实值）、奶茶 120 过（放宽档）、柠檬茶 45 过。"""
    assert nutrition.sanitize_web_value("红茶", 294.0) is None
    assert nutrition.sanitize_web_value("奶茶", 120.0) == (120.0, "")
    assert nutrition.sanitize_web_value("柠檬茶", 45.0) == (45.0, "")


def test_sanitize_not_drink_by_suffix() -> None:
    """饮品判断按结尾词：茶肠/茶树菇/水果不以饮品词结尾，不会被先验误杀。"""
    assert nutrition.sanitize_web_value("茶肠", 329.0) == (329.0, "")
    assert nutrition.sanitize_web_value("茶树菇", 29.0) == (29.0, "")
    assert nutrition.sanitize_web_value("水果", 60.0) == (60.0, "")


def test_sanitize_kj_confusion_corrected() -> None:
    """kJ 误报：联网值 ≈ 模型估值 × 4.184（±20%）→ 折算回 kcal。"""
    out = nutrition.sanitize_web_value("苏打饼干", 837.0, model_per_100g=200.0)
    assert out is not None and abs(out[0] - 200.0) < 0.5  # 837/4.184 ≈ 200


def test_sanitize_bounds() -> None:
    assert nutrition.sanitize_web_value("任意", 0) is None
    assert nutrition.sanitize_web_value("任意", 1500.0) is None
    assert nutrition.sanitize_web_value("任意", "abc") is None


def test_backfill_drink_outlier_never_lands(env, monkeypatch) -> None:
    """全链路：识别红茶（模型估 2 kcal）→ 后台联网回 294 → 先验拒绝，staging 无行、
    条目保持 model 估值（不再出现「50g 红茶 147 kcal」）。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "红茶", 50, 2)
    monkeypatch.setattr(ai, "web_search_food",
                        lambda name, brand="", model_per_100g=None: {"kcal_per_100g": 294.0})
    entry = _recognize(env, uid)
    assert entry["items"][0]["source"] == "model" and entry["items"][0]["kcal"] == 2
    rows = get_conn().execute(
        "SELECT COUNT(*) AS c FROM food_nutrition_staging WHERE name = '红茶'").fetchone()
    assert rows["c"] == 0


def test_backfill_sane_drink_value_lands(env, monkeypatch) -> None:
    """联网返回合理饮品值（柠檬茶 45）→ 正常落 staging，升级提示照常。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "柠檬茶", 300, 90)
    monkeypatch.setattr(ai, "web_search_food",
                        lambda name, brand="", model_per_100g=None: {"kcal_per_100g": 45.0})
    entry = _recognize(env, uid)
    env.post("/api/calories", json={"account_id": uid, "id": entry["id"]})
    body = env.get("/api/calories", params={"account_id": uid}).json()
    it = body["items"][0]["items"][0]
    assert it["upgrade"] == {"kcal_per_100g": 45.0, "kcal": 135}  # 45 × 300g


# ---------- BUG-023：SSE 推送 ----------
# 注：TestClient 的 ASGITransport 会把响应体整个收集完才返回（不支持增量流式），
# 对无限 SSE 生成器直接 client.stream 会死等——所以这里直驱事件总线 + 直调端点函数。


def test_event_bus_cross_thread_delivery(monkeypatch) -> None:
    """总线语义：订阅注册后，从别的线程（backfill 跑在线程池里）publish 能投递；
    关闭订阅即注销。"""
    import asyncio
    import threading

    monkeypatch.setattr(events_mod, "HEARTBEAT_SECONDS", 5.0)
    acct = "sse_bus_test"

    async def scenario():
        gen = events_mod.stream(acct)
        first = await gen.__anext__()  # 首块（订阅注册完成后才 yield）
        assert first.startswith("retry:")
        t = threading.Thread(target=lambda: events_mod.publish(
            acct, {"type": "staging_ready", "name": "红茶", "kcal_per_100g": 2.0}))
        t.start()
        data = await asyncio.wait_for(gen.__anext__(), timeout=3)
        assert data.startswith("data:") and "红茶" in data and "staging_ready" in data
        t.join()
        await gen.aclose()  # 触发 finally：注销订阅
        assert acct not in events_mod._subs

    asyncio.run(scenario())


def test_event_bus_no_subscriber_is_noop() -> None:
    events_mod.publish("nobody_home", {"type": "staging_ready"})  # 不抛错即通过


def test_sse_endpoint_shape(client, env) -> None:
    """端点直调：合法账号返回 text/event-stream 的 StreamingResponse（生成器未启动，
    只是验形——流式收发的行为由 test_event_bus_cross_thread_delivery 覆盖）。"""
    import asyncio

    from app.api import ledger as api_ledger

    uid = _new_user(client)
    resp = asyncio.run(api_ledger.calorie_events(uid))
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache"


def test_sse_endpoint_rejects_unknown_account(client) -> None:
    r = client.get("/api/calories/events", params={"account_id": "nope"})
    assert r.status_code == 404


def test_upsert_web_revives_softdeleted_row(env) -> None:
    """软删的 staging 行：联网再搜到同名 → 以新值复活（ON CONFLICT 的 deleted=1 分支）。"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO food_nutrition_staging
           (name, brand, kcal_per_100g, source, verified, deleted, created_at)
           VALUES ('共建柠檬茶', '', 500.0, 'web', 1, 1, '2026-08-21T00:00:00')""")
    conn.commit()
    out = nutrition.upsert_staging_web("共建柠檬茶", {"kcal_per_100g": 40.0})
    assert out is not None and out["kcal_per_100g"] == 40.0
    row = conn.execute(
        "SELECT deleted, kcal_per_100g FROM food_nutrition_staging WHERE name = '共建柠檬茶'"
    ).fetchone()
    assert row["deleted"] == 0 and row["kcal_per_100g"] == 40.0


def test_upsert_web_keeps_live_user_row(env) -> None:
    """活着的用户手填行：联网再来同名 → 不覆盖（沿用原语义），返回行内实际值。"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO food_nutrition_staging
           (name, brand, kcal_per_100g, source, verified, created_at)
           VALUES ('共建拿铁', '', 55.0, 'user', 1, '2026-08-21T00:00:00')""")
    conn.commit()
    out = nutrition.upsert_staging_web("共建拿铁", {"kcal_per_100g": 43.0})
    assert out is not None and out["kcal_per_100g"] == 55.0
    row = conn.execute(
        "SELECT source, kcal_per_100g FROM food_nutrition_staging WHERE name = '共建拿铁'"
    ).fetchone()
    assert row["source"] == "user" and row["kcal_per_100g"] == 55.0


# ---------- staging 管理面板 ----------


def test_staging_admin_crud(env) -> None:
    """列表搜索 / 改值 / 软删（匹配即刻跳过）/ 再手动添加同名复活。"""
    uid = _new_user(env)
    conn = get_conn()
    conn.execute(
        """INSERT INTO food_nutrition_staging (name, brand, kcal_per_100g, source, verified, created_at)
           VALUES ('共建红茶', '', 294.0, 'web', 1, '2026-08-21T16:35:32')""")
    conn.commit()
    # 列表 + 搜索
    body = env.get("/api/nutrition/staging", params={"account_id": uid, "query": "红茶"}).json()
    assert body["total"] == 1 and body["items"][0]["kcal_per_100g"] == 294.0
    sid = body["items"][0]["id"]
    # 改值（治理错值：294 → 2）
    r = env.patch(f"/api/nutrition/staging/{sid}",
                  json={"account_id": uid, "kcal_per_100g": 2.0, "verified": True})
    assert r.status_code == 200, r.text
    assert r.json()["food"]["kcal_per_100g"] == 2.0
    assert r.json()["food"]["updated_by"] == uid
    # 软删：列表默认不见，匹配也跳过
    r = env.delete(f"/api/nutrition/staging/{sid}", params={"account_id": uid})
    assert r.status_code == 200
    assert env.get("/api/nutrition/staging", params={"account_id": uid}).json()["total"] == 0
    body2 = env.get("/api/nutrition/staging",
                    params={"account_id": uid, "include_deleted": True}).json()
    assert body2["total"] == 1 and body2["items"][0]["deleted"] is True
    assert nutrition.match("共建红茶") is None
    # 手动再添加同名 → 以新值复活（不是 UNIQUE 500）
    r = env.post("/api/nutrition/foods",
                 json={"account_id": uid, "name": "共建红茶", "kcal_per_100g": 3.0})
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True and r.json()["food"]["kcal_per_100g"] == 3.0
    assert r.json()["food"]["deleted"] is False


def test_staging_admin_update_name_clash_409(env) -> None:
    uid = _new_user(env)
    conn = get_conn()
    for n in ("共建甲", "共建乙"):
        conn.execute(
            """INSERT INTO food_nutrition_staging (name, brand, kcal_per_100g, source, created_at)
               VALUES (?, '', 100.0, 'user', '2026-08-22T00:00:00')""", (n,))
    conn.commit()
    rows = {r["name"]: r["id"] for r in conn.execute(
        "SELECT id, name FROM food_nutrition_staging").fetchall()}
    r = env.patch(f"/api/nutrition/staging/{rows['共建乙']}",
                  json={"account_id": uid, "name": "共建甲"})
    assert r.status_code == 409


def test_staging_softdelete_kills_upgrade_hint(env, monkeypatch) -> None:
    """已挂升级提示的条目：staging 行被软删后提示消失（查不到活行了）。"""
    uid = _new_user(env)
    _patch_recognize(monkeypatch, "冰美式", 300, 15)
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="", model_per_100g=None: {"kcal_per_100g": 3.0})
    entry = _recognize(env, uid)
    env.post("/api/calories", json={"account_id": uid, "id": entry["id"]})
    body = env.get("/api/calories", params={"account_id": uid}).json()
    assert body["items"][0]["items"][0].get("upgrade") is not None
    sid = get_conn().execute(
        "SELECT id FROM food_nutrition_staging WHERE name = '冰美式'").fetchone()["id"]
    env.delete(f"/api/nutrition/staging/{sid}", params={"account_id": uid})
    body = env.get("/api/calories", params={"account_id": uid}).json()
    assert body["items"][0]["items"][0].get("upgrade") is None
