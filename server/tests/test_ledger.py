"""记账 + 热量测试：手动记账 CRUD、识别→确认流转（monkeypatch 伪装识别函数）、
热量超预算 adjust 联动（幂等更新）、存款月度结算懒触发。

运行：cd server && .venv-win/Scripts/python -m pytest tests/test_ledger.py -v
"""
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import date, datetime

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_ledger_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
# 开关类也要清：本机 .env 开了联网的话，web_search_food 桩会从 None 变成给确定性结果，
# 「全不中回退模型估值」的断言就被污染（BUG-016 同款环境泄漏）
os.environ["LLM_WEB_SEARCH"] = ""
os.environ["TREEHOLE_API_KEY"] = ""
os.environ["TREEHOLE_BASE_URL"] = ""
os.environ["TREEHOLE_MODEL"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ledger as ledger_svc  # noqa: E402

TODAY = date.today().isoformat()
MONTH = TODAY[:7]


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _new_user(client: TestClient, name: str = "记账测试圈") -> str:
    return client.post("/api/circles", json={"name": name, "nickname": "阿澈"}).json()["account_id"]


def _image_url() -> str:
    """造一张站内存量的假图（识别路由只校验文件存在，不解析内容）。
    每次内容唯一：图片型 RAG 按字节哈希取向量，相同字节会被当成同一张图命中历史。"""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    (settings.upload_dir / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + uuid.uuid4().bytes)
    return f"/api/uploads/{stem}.jpg"


def _month_add(month: str, n: int) -> str:
    """'YYYY-MM' 加 n 个月（与 goals 服务内同口径）。"""
    total = int(month[:4]) * 12 + (int(month[5:7]) - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


# ---------- 手动记账 ----------

def test_manual_expense_crud_and_month_total(client: TestClient) -> None:
    """手动记账直接 confirmed；月合计只加正数支出；改/删仅 owner。"""
    uid = _new_user(client)
    other = _new_user(client, "记账圈外人")

    r = client.post("/api/ledger/expenses", json={
        "account_id": uid, "amount_fen": 3550, "category": "餐饮", "merchant": "麦当劳"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    e1 = r.json()["id"]
    # 收入记负数；不认得的分类归「其他」
    r = client.post("/api/ledger/expenses", json={
        "account_id": uid, "amount_fen": -5000, "category": "工资外快", "note": "闲鱼出货"})
    assert r.status_code == 200
    e2 = r.json()["id"]

    body = client.get("/api/ledger/expenses", params={"account_id": uid, "month": MONTH}).json()
    assert body["month_total_fen"] == 3550  # 负数收入不计入支出合计
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[e1]["amount_fen"] == 3550 and by_id[e1]["category"] == "餐饮"
    assert by_id[e1]["source"] == "manual" and by_id[e1]["status"] == "confirmed"
    assert by_id[e2]["amount_fen"] == -5000 and by_id[e2]["category"] == "其他"

    # 校验：金额必填/非零；浮点被路由层 pydantic 挡下（422）；时间与月份格式
    assert client.post("/api/ledger/expenses", json={"account_id": uid}).status_code == 400
    assert client.post(
        "/api/ledger/expenses", json={"account_id": uid, "amount_fen": 0}).status_code == 400
    assert client.post(
        "/api/ledger/expenses", json={"account_id": uid, "amount_fen": 10.5}).status_code == 422
    assert client.post("/api/ledger/expenses", json={
        "account_id": uid, "amount_fen": 100, "spent_at": "昨天"}).status_code == 400
    assert client.get(
        "/api/ledger/expenses", params={"account_id": uid, "month": "2026-8"}).status_code == 400

    # 改账：传什么改什么；越权 403
    r = client.put(f"/api/ledger/expenses/{e1}", json={"account_id": uid, "amount_fen": 3600})
    assert r.status_code == 200
    assert client.put(
        f"/api/ledger/expenses/{e1}", json={"account_id": other, "amount_fen": 1}).status_code == 403
    body = client.get("/api/ledger/expenses", params={"account_id": uid, "month": MONTH}).json()
    assert body["month_total_fen"] == 3600

    # 删账：越权 403；owner 删完列表与合计都缩回去
    assert client.delete(
        f"/api/ledger/expenses/{e1}", params={"account_id": other}).status_code == 403
    assert client.delete(f"/api/ledger/expenses/{e1}", params={"account_id": uid}).status_code == 200
    body = client.get("/api/ledger/expenses", params={"account_id": uid, "month": MONTH}).json()
    assert body["month_total_fen"] == 0 and [i["id"] for i in body["items"]] == [e2]


# ---------- 识别 → 确认流转 ----------

def test_add_expense_service_bool_rejected(client: TestClient) -> None:
    """bool 金额：HTTP 层被 pydantic 收成 1（拦不住），服务层 isinstance 防御直调仍 400。"""
    uid = _new_user(client, "bool防御圈")
    with pytest.raises(HTTPException) as exc:
        ledger_svc.add_expense(uid, True)
    assert exc.value.status_code == 400

def test_recognize_400_without_vision(client: TestClient) -> None:
    """未配视觉模型：识别路由恒 400（手动录入兜底），不产生任何数据。"""
    uid = _new_user(client, "识别400圈")
    url = _image_url()
    r = client.post("/api/ledger/recognize", json={"account_id": uid, "image_url": url})
    assert r.status_code == 400 and "手动录入" in r.json()["detail"]
    r = client.post("/api/calories/recognize", json={"account_id": uid, "image_url": url})
    assert r.status_code == 400 and "手动录入" in r.json()["detail"]
    # 站外地址直接 400（不泄露任何信息）
    assert client.post("/api/ledger/recognize", json={
        "account_id": uid, "image_url": "https://evil.com/x.jpg"}).status_code == 400
    body = client.get("/api/ledger/expenses", params={"account_id": uid, "month": MONTH}).json()
    assert body["items"] == [] and body["month_total_fen"] == 0


def test_recognize_then_confirm_expense_flow(client: TestClient, monkeypatch) -> None:
    """伪装识别成功：一图多笔 pending → 确认（可改金额/时间）→ 进账本；重复确认幂等。"""
    uid = _new_user(client, "识别确认圈")
    monkeypatch.setattr(ai, "recognize_receipt", lambda path: [
        {"amount": 35.5, "merchant": "麦当劳", "time": "12:30", "category": "餐饮", "type": "expense"},
        {"amount": 6.0, "merchant": "地铁", "time": "", "category": "交通", "type": "expense"},
        {"amount": 100.0, "merchant": "退款", "time": "", "category": "其他", "type": "income"},
    ])
    r = client.post("/api/ledger/recognize", json={"account_id": uid, "image_url": _image_url()})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 3
    assert all(i["status"] == "pending" and i["source"] == "vision" for i in items)
    assert items[0]["amount_fen"] == 3550 and items[0]["merchant"] == "麦当劳"
    assert items[2]["amount_fen"] == -10000  # 收入识别为负数

    # pending 不进账本
    body = client.get("/api/ledger/expenses", params={"account_id": uid, "month": MONTH}).json()
    assert body["items"] == [] and body["month_total_fen"] == 0

    # 确认：同时把 35.5 改成 36.0，时间补成今天（进当月账本）
    r = client.post("/api/ledger/expenses", json={
        "account_id": uid, "id": items[0]["id"], "amount_fen": 3600, "spent_at": TODAY})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    client.post("/api/ledger/expenses", json={"account_id": uid, "id": items[1]["id"], "spent_at": TODAY})

    body = client.get("/api/ledger/expenses", params={"account_id": uid, "month": MONTH}).json()
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[items[0]["id"]]["amount_fen"] == 3600
    assert by_id[items[0]["id"]]["status"] == "confirmed"
    assert by_id[items[1]["id"]]["amount_fen"] == 600
    assert body["month_total_fen"] == 4200  # 未确认的退款不入账

    # 重复确认幂等：再确认一次金额不被改写
    r = client.post("/api/ledger/expenses", json={
        "account_id": uid, "id": items[0]["id"], "amount_fen": 9999})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    body = client.get("/api/ledger/expenses", params={"account_id": uid, "month": MONTH}).json()
    assert {i["id"]: i for i in body["items"]}[items[0]["id"]]["amount_fen"] == 3600


def test_recognize_then_confirm_calorie_flow(client: TestClient, monkeypatch) -> None:
    """食物识别 pending（菜品明细 + MET 运动等效）→ 确认时改数字重算等效；重复确认幂等。"""
    uid = _new_user(client, "热量确认圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "kcal": 232}, {"name": "番茄炒蛋", "kcal": 170}],
        "note": "伪装识别",
    })
    r = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url(), "hint": "家常一份"})
    assert r.status_code == 200, r.text
    entry = r.json()["entry"]
    assert entry["status"] == "pending" and entry["total_kcal"] == 402
    assert [i["name"] for i in entry["items"]] == ["米饭", "番茄炒蛋"]
    assert set(entry["exercise_equiv"]) == {"running", "walking", "cycling", "swimming"}

    # pending 不进当日清单
    body = client.get("/api/calories", params={"account_id": uid, "date": TODAY}).json()
    assert body["items"] == [] and body["consumed_kcal"] == 0

    # 确认时改成 500 kcal：等效按 500、默认体重 65kg 重算
    r = client.post("/api/calories", json={"account_id": uid, "id": entry["id"], "total_kcal": 500})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert r.json()["adjustment"] is None  # 无减肥目标，无联动
    body = client.get("/api/calories", params={"account_id": uid, "date": TODAY}).json()
    assert body["consumed_kcal"] == 500
    saved = body["items"][0]
    assert saved["exercise_equiv"]["running"]["minutes"] == round(500 / (8.3 * 65) * 60)

    # 重复确认幂等：数字不再被改
    client.post("/api/calories", json={"account_id": uid, "id": entry["id"], "total_kcal": 9999})
    assert client.get("/api/calories", params={"account_id": uid, "date": TODAY}).json()["consumed_kcal"] == 500


# ---------- 改克数：重算 + 纠正落库 + 校准注入 ----------

def test_update_grams_recalculates(client: TestClient, monkeypatch) -> None:
    """查表命中的菜品改克数：kcal 按 kcal_per_100g 重算，total/运动等效/当日累计同步；
    pending 与已入账两种状态都可改。"""
    uid = _new_user(client, "克数圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "grams": 200, "kcal": 232}], "note": ""})
    r = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()})
    entry = r.json()["entry"]
    item = entry["items"][0]
    assert item["grams"] == 200 and item["kcal"] == 232  # 116 kcal/100g × 200g
    assert item["kcal_per_100g"] == 116.0 and item["source"] == "table"

    # pending 态改 200 → 400g：热量翻倍，总热量跟着变
    r = client.put(f"/api/calories/{entry['id']}/items",
                   json={"account_id": uid, "index": 0, "grams": 400})
    assert r.status_code == 200, r.text
    entry = r.json()["entry"]
    assert entry["items"][0]["grams"] == 400 and entry["items"][0]["kcal"] == 464
    assert entry["total_kcal"] == 464
    running = entry["exercise_equiv"]["running"]["minutes"]
    assert running == round(464 / (8.3 * 65) * 60)  # 默认体重 65kg 重算

    # 确认入账后再改 400 → 300g：当日累计同步为 348
    client.post("/api/calories", json={"account_id": uid, "id": entry["id"]})
    r = client.put(f"/api/calories/{entry['id']}/items",
                   json={"account_id": uid, "index": 0, "grams": 300})
    assert r.status_code == 200
    body = client.get("/api/calories", params={"account_id": uid, "date": TODAY}).json()
    assert body["consumed_kcal"] == 348


def test_update_grams_model_fallback_scales_linearly(client: TestClient, monkeypatch) -> None:
    """模型估值（查表全不中、无 kcal_per_100g）改克数：按旧值线性缩放。"""
    uid = _new_user(client, "火星料理圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "火星料理", "grams": 200, "kcal": 500}], "note": ""})
    r = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()})
    item = r.json()["entry"]["items"][0]
    assert item["source"] == "model" and "kcal_per_100g" not in item
    r = client.put(f"/api/calories/{r.json()['entry']['id']}/items",
                   json={"account_id": uid, "index": 0, "grams": 100})
    assert r.status_code == 200
    assert r.json()["entry"]["items"][0]["kcal"] == 250  # 500 × 100/200


def test_update_grams_records_correction_and_injects_calibration(client: TestClient, monkeypatch) -> None:
    """改克数落一条纠正（ai_grams/user_grams）；下次识别时该账号的纠正注入 prompt 校准。"""
    uid = _new_user(client, "校准圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "grams": 200, "kcal": 232}], "note": ""})
    r = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()})
    entry = r.json()["entry"]
    client.put(f"/api/calories/{entry['id']}/items",
               json={"account_id": uid, "index": 0, "grams": 350})
    conn = _db()
    rows = conn.execute(
        "SELECT name, ai_grams, user_grams FROM calorie_gram_corrections WHERE account_id = ?",
        (uid,)).fetchall()
    assert [(r["name"], r["ai_grams"], r["user_grams"]) for r in rows] == [("米饭", 200.0, 350.0)]

    # 同值再保存不灌水；随后新一次识别应带上这条校准样例
    client.put(f"/api/calories/{entry['id']}/items",
               json={"account_id": uid, "index": 0, "grams": 350})
    seen: dict = {}
    def capture(path, hint="", calibration=None, **_):
        seen["calibration"] = calibration
        return {"items": [{"name": "米饭", "grams": 350, "kcal": 406}], "note": ""}
    monkeypatch.setattr(ai, "recognize_food", capture)
    client.post("/api/calories/recognize", json={"account_id": uid, "image_url": _image_url()})
    calib = seen["calibration"]
    assert len(calib) == 1 and calib[0]["name"] == "米饭"
    assert calib[0]["ai_grams"] == 200 and calib[0]["user_grams"] == 350
    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM calorie_gram_corrections WHERE account_id = ?", (uid,)
    ).fetchone()
    assert rows["c"] == 1  # 同值幂等，没写第二行


def test_update_grams_validation(client: TestClient, monkeypatch) -> None:
    """改克数校验：记录不存在 404、别人的记录 403、序号越界/克数非法/无克数条目 400。"""
    uid = _new_user(client, "校验圈")
    other = _new_user(client, "校验圈外人")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "grams": 200, "kcal": 232}], "note": ""})
    entry = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()}).json()["entry"]
    base = f"/api/calories/{entry['id']}/items"
    assert client.put(base, json={"account_id": other, "index": 0, "grams": 300}).status_code == 403
    assert client.put(base, json={"account_id": uid, "index": 5, "grams": 300}).status_code == 400
    assert client.put(base, json={"account_id": uid, "index": 0, "grams": 0}).status_code == 400
    assert client.put(base, json={"account_id": uid, "index": 0, "grams": 99999}).status_code == 400
    assert client.put("/api/calories/ghost/items",
                      json={"account_id": uid, "index": 0, "grams": 300}).status_code == 404


# ---------- 改名字：重匹配链 + 名字纠正 ----------

def test_rename_item_table_hit(client: TestClient, monkeypatch) -> None:
    """识别错的菜名改成成分表里有的：按新名单价×克数重算 kcal，记名字纠正。"""
    uid = _new_user(client, "改名圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "grams": 200, "kcal": 232}], "note": ""})
    entry = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()}).json()["entry"]
    r = client.put(f"/api/calories/{entry['id']}/items",
                   json={"account_id": uid, "index": 0, "name": "鸡蛋"})
    assert r.status_code == 200, r.text
    item = r.json()["entry"]["items"][0]
    assert item["name"] == "鸡蛋" and item["source"] == "table"
    assert item["kcal_per_100g"] == 143.0          # 命中 鸡蛋(煮)（原名最短者胜）
    assert item["kcal"] == round(143.0 * 200 / 100)  # 286
    assert r.json()["entry"]["total_kcal"] == 286
    rows = _db().execute(
        "SELECT recognized_name, corrected_name FROM calorie_name_corrections WHERE account_id = ?",
        (uid,)).fetchall()
    assert [(r["recognized_name"], r["corrected_name"]) for r in rows] == [("米饭", "鸡蛋")]


def test_rename_item_web_hit_then_kept_estimate(client: TestClient, monkeypatch) -> None:
    """改名查表不中 → 联网搜到：写入 staging 标 web_pending 并按联网单价重算；
    联网也搜不到 → 保留当前热量标 model。"""
    uid = _new_user(client, "联网改名圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "grams": 200, "kcal": 232}], "note": ""})
    entry = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()}).json()["entry"]

    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="", model_per_100g=None: {
        "kcal_per_100g": 50.0, "protein_per_100g": None, "fat_per_100g": None, "cho_per_100g": None})
    r = client.put(f"/api/calories/{entry['id']}/items",
                   json={"account_id": uid, "index": 0, "name": "神秘果昔"})
    item = r.json()["entry"]["items"][0]
    assert item["name"] == "神秘果昔" and item["source"] == "web_pending"
    assert item["kcal"] == 100 and item["kcal_per_100g"] == 50.0  # 50 × 200g
    assert _db().execute(
        "SELECT verified, source FROM food_nutrition_staging WHERE name = '神秘果昔'"
    ).fetchone()["verified"] == 1

    # 联网搜不到（None）：保留当前热量标 model，单价标记移除
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="", model_per_100g=None: None)
    r = client.put(f"/api/calories/{entry['id']}/items",
                   json={"account_id": uid, "index": 0, "name": "幻影料理"})
    item = r.json()["entry"]["items"][0]
    assert item["name"] == "幻影料理" and item["source"] == "model"
    assert item["kcal"] == 100 and "kcal_per_100g" not in item


def test_name_correction_applies_on_next_recognize(client: TestClient, monkeypatch) -> None:
    """改名纠正后：下次识别出同一个错名，直接用纠正名查表与落库。"""
    uid = _new_user(client, "纠正生效圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "grams": 100, "kcal": 116}], "note": ""})
    entry = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()}).json()["entry"]
    client.put(f"/api/calories/{entry['id']}/items",
               json={"account_id": uid, "index": 0, "name": "鸡蛋"})
    # 再次识别出「米饭」→ 应用纠正：item 直接是鸡蛋（143/100g × 100g）
    r = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()})
    item = r.json()["entry"]["items"][0]
    assert item["name"] == "鸡蛋" and item["kcal"] == 143 and item["source"] == "table"


# ---------- 手动录入即时匹配（lookup） ----------

def test_lookup_food(client: TestClient) -> None:
    """lookup：命中带克数算好总热量；不带克数只给单价；未命中 found=False。"""
    uid = _new_user(client, "查询圈")
    r = client.get("/api/calories/lookup", params={"account_id": uid, "name": "米饭", "grams": 200})
    assert r.status_code == 200 and r.json()["found"] is True
    assert r.json()["kcal_per_100g"] == 116.0 and r.json()["kcal"] == 232
    r = client.get("/api/calories/lookup", params={"account_id": uid, "name": "米饭"})
    assert "kcal" not in r.json() and r.json()["found"] is True
    r = client.get("/api/calories/lookup", params={"account_id": uid, "name": "不存在的食物"})
    assert r.json()["found"] is False


def test_manual_add_with_items(client: TestClient) -> None:
    """手动录入带结构化明细（食物名+克数+热量）：明细落库，当日记录可见可再改。"""
    uid = _new_user(client, "手动明细圈")
    r = client.post("/api/calories", json={
        "account_id": uid, "total_kcal": 232, "note": "米饭",
        "items": [{"name": "米饭", "kcal": 232, "grams": 200, "kcal_per_100g": 116.0,
                   "source": "table"}]})
    assert r.status_code == 200, r.text
    body = client.get("/api/calories", params={"account_id": uid, "date": TODAY}).json()
    saved = body["items"][0]
    assert saved["items"][0]["name"] == "米饭" and saved["items"][0]["grams"] == 200
    # 带单价的手动明细也能再改克数
    r = client.put(f"/api/calories/{saved['id']}/items",
                   json={"account_id": uid, "index": 0, "grams": 100})
    assert r.status_code == 200 and r.json()["entry"]["items"][0]["kcal"] == 116


# ---------- 图片型 RAG：以图搜图复用历史确认 ----------

def _plant_image_bytes(content: bytes) -> str:
    """造一张指定内容的站内图（图片向量桩按字节哈希：同内容=同图=同向量）。"""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    (settings.upload_dir / f"{stem}.jpg").write_bytes(content)
    return f"/api/uploads/{stem}.jpg"


def test_image_rag_hit_reuses_confirmed(client: TestClient, monkeypatch) -> None:
    """确认入账后图片向量入库；再拍同一张图：直接复用上次的菜名/品牌/单价
    （source=image_rag），识别模型把名字叫错了也被拉回确认值。"""
    uid = _new_user(client, "图库圈")
    img = _plant_image_bytes(b"\xff\xd8\xff\xd9-rag-hit")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "grams": 200, "kcal": 232}], "note": ""})
    entry = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": img}).json()["entry"]
    client.post("/api/calories", json={"account_id": uid, "id": entry["id"]})
    rows = _db().execute(
        "SELECT name, kcal_per_100g, typical_grams FROM calorie_food_images WHERE account_id = ?",
        (uid,)).fetchall()
    assert [(r["name"], r["kcal_per_100g"], r["typical_grams"]) for r in rows] == [("米饭", 116.0, 200.0)]

    # 再拍同一张图，识别抖成「白米饭 250g」：名字/单价被图库拉回，克数按新图
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "白米饭", "grams": 250, "kcal": 999}], "note": ""})
    entry2 = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": img}).json()["entry"]
    item = entry2["items"][0]
    assert item["source"] == "image_rag" and item["name"] == "米饭"
    assert item["kcal_per_100g"] == 116.0 and item["kcal"] == round(116.0 * 250 / 100)


def test_image_rag_miss_falls_back(client: TestClient, monkeypatch) -> None:
    """不同的图（向量不相似）→ 走常管线（查表），不误复用。"""
    uid = _new_user(client, "图库圈外")
    img1 = _plant_image_bytes(b"\xff\xd8\xff\xd9-rag-A")
    img2 = _plant_image_bytes(b"\xff\xd8\xff\xd9-rag-B-totally-different-image")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "米饭", "grams": 200, "kcal": 232}], "note": ""})
    entry = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": img1}).json()["entry"]
    client.post("/api/calories", json={"account_id": uid, "id": entry["id"]})
    entry2 = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": img2}).json()["entry"]
    assert entry2["items"][0]["source"] == "table"  # 常管线查表（米饭在成分表）


# ---------- 估值钳制与联网升级 ----------

def test_model_estimate_physical_clamp(client: TestClient, monkeypatch) -> None:
    """模型估值的隐含单价超物理上限（1000 kcal/100g，纯油约 900）→ 钳回上限。"""
    uid = _new_user(client, "钳制圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "神秘能量汤", "grams": 100, "kcal": 5000}], "note": ""})
    r = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()})
    item = r.json()["entry"]["items"][0]
    assert item["source"] == "model" and item["kcal"] == 1000  # 5000 → 钳到 1000


def test_model_estimate_upgrade_after_web_backfill(client: TestClient, monkeypatch) -> None:
    """估值入账 + 联网后台入库后：记录挂 upgrade 提示；同名重匹配按联网单价钱重算，
    source 升 staging；名字没变不写名字纠正。"""
    uid = _new_user(client, "升级圈")
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [{"name": "神秘果汁", "grams": 250, "kcal": 1412}], "note": ""})
    monkeypatch.setattr(ai, "web_search_food", lambda name, brand="", model_per_100g=None: {
        "kcal_per_100g": 45.0, "protein_per_100g": None, "fat_per_100g": None, "cho_per_100g": None})
    r = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url()})
    entry = r.json()["entry"]
    assert entry["items"][0]["source"] == "model" and entry["items"][0]["kcal"] == 1412
    client.post("/api/calories", json={"account_id": uid, "id": entry["id"]})
    # 后台补库（TestClient 内联跑完）→ 今日记录该条目挂 upgrade
    body = client.get("/api/calories", params={"account_id": uid, "date": TODAY}).json()
    it = body["items"][0]["items"][0]
    assert it["upgrade"] == {"kcal_per_100g": 45.0, "kcal": 112}  # 45 × 250g（round-half-even）
    # 用户点「更新」：同名重匹配 → 按联网单价重算，source 升 staging
    r = client.put(f"/api/calories/{entry['id']}/items",
                   json={"account_id": uid, "index": 0, "name": "神秘果汁"})
    assert r.status_code == 200, r.text
    item2 = r.json()["entry"]["items"][0]
    assert item2["kcal"] == 112 and item2["source"] == "staging"
    rows = _db().execute(
        "SELECT COUNT(*) AS c FROM calorie_name_corrections WHERE account_id = ?", (uid,)
    ).fetchone()
    assert rows["c"] == 0


# ---------- 热量超预算 ↔ 计划 adjust 联动 ----------

def _weight_loss_goal_with_budget(client: TestClient, uid: str, budget_kcal: int) -> str:
    """建减肥目标后直接把 framework 拨成小预算（确定性联动测试）。"""
    r = client.post("/api/goals", json={
        "account_id": uid, "type": "weight_loss", "title": "减重", "answers": {"weight_kg": 70}})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    conn = _db()
    conn.execute("UPDATE goals SET framework = ? WHERE id = ?",
                 (json.dumps({"budget_kcal": budget_kcal}), gid))
    conn.commit()
    conn.close()
    return gid


def _adjust_items(client: TestClient, uid: str) -> list[dict]:
    items = client.get("/api/plans/today", params={"account_id": uid}).json()["items"]
    return [i for i in items if i["source"] == "adjust"]


def test_calorie_over_budget_adjust_item(client: TestClient) -> None:
    """确认热量累计超今日预算 → 今日计划出现 source='adjust' 条目；再超 → 同一条目更新不重复。"""
    uid = _new_user(client, "热量联动圈")
    gid = _weight_loss_goal_with_budget(client, uid, 1500)

    # 未超预算：无联动、无 adjust 条目
    r = client.post("/api/calories", json={"account_id": uid, "total_kcal": 1000, "note": "早饭"})
    assert r.status_code == 200 and r.json()["adjustment"] is None
    assert _adjust_items(client, uid) == []

    # 确认一笔识别热量把今天推到 1800（超 300）：出现 adjust 条目
    conn = _db()
    conn.execute(
        """INSERT INTO calorie_entries (id, account_id, total_kcal, items, exercise_equiv, note,
           source, image_url, status, created_at)
           VALUES (?, ?, 800, '[]', '{}', '', 'vision', '', 'pending', ?)""",
        (uuid.uuid4().hex[:12], uid, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    pending_id = conn.execute(
        "SELECT id FROM calorie_entries WHERE account_id = ? AND status = 'pending'", (uid,)
    ).fetchone()["id"]
    conn.close()
    r = client.post("/api/calories", json={"account_id": uid, "id": pending_id})
    assert r.status_code == 200, r.text
    adj = r.json()["adjustment"]
    assert adj is not None and adj["over_kcal"] == 300
    assert set(adj["exercise"]) == {"running", "walking", "cycling", "swimming"}

    items = _adjust_items(client, uid)
    assert len(items) == 1
    item = items[0]
    assert item["id"] == adj["plan_item_id"] and item["goal_id"] == gid
    assert item["kind"] == "task" and item["done"] is False
    assert "今日热量已超预算 300 kcal" in item["content"] and "运动补偿" in item["content"]

    # 再超（累计 2300，超 800）：同一条目原地更新，不插第二条
    r = client.post("/api/calories", json={"account_id": uid, "total_kcal": 500, "note": "下午茶"})
    assert r.json()["adjustment"]["plan_item_id"] == item["id"]
    assert r.json()["adjustment"]["over_kcal"] == 800
    items = _adjust_items(client, uid)
    assert len(items) == 1
    assert "今日热量已超预算 800 kcal" in items[0]["content"]


def test_calorie_no_goal_no_linkage(client: TestClient) -> None:
    """无减肥目标：手动热量正常入账，但无任何联动；清单带不出预算。"""
    uid = _new_user(client, "无联动圈")
    r = client.post("/api/calories", json={"account_id": uid, "total_kcal": 9000})
    assert r.status_code == 200 and r.json()["adjustment"] is None
    assert _adjust_items(client, uid) == []
    body = client.get("/api/calories", params={"account_id": uid, "date": TODAY}).json()
    assert body["consumed_kcal"] == 9000 and "budget_kcal" not in body


# ---------- 存款月度结算懒触发 ----------

def test_savings_monthly_settlement_lazy(client: TestClient) -> None:
    """游标拨回过去月份 → 读目标触发逐月结算：saved_fen 滚雪球、advice 落库、游标推进到当月。"""
    uid = _new_user(client, "结算圈")
    m2 = _month_add(MONTH, -2)  # 上上月
    m1 = _month_add(MONTH, -1)  # 上月
    r = client.post("/api/goals", json={
        "account_id": uid, "type": "savings", "title": "攒旅行基金",
        "params": {"target_fen": 6000000, "months_left": 6},
        "answers": {"fixed_income_fen": 1000000, "fixed_expense_fen": 200000},
    })
    assert r.status_code == 200, r.text
    gid = r.json()["id"]

    # 上月账目：支出 3000 元 + 额外收入 1000 元（负数）
    client.post("/api/ledger/expenses", json={
        "account_id": uid, "amount_fen": 300000, "category": "餐饮", "spent_at": f"{m1}-15"})
    client.post("/api/ledger/expenses", json={
        "account_id": uid, "amount_fen": -100000, "category": "其他", "spent_at": f"{m1}-16"})

    # 游标拨回上上月 → 触发 m2、m1 两轮结算
    conn = _db()
    conn.execute("UPDATE goals SET last_settled_month = ? WHERE id = ?", (m2, gid))
    conn.commit()
    conn.close()

    d = client.get(f"/api/goals/{gid}", params={"account_id": uid}).json()
    # m2 无账目：实际存入 = 固定收入 10000；m1：10000 + 1000 − 3000 = 8000 → 累计 18000
    assert d["params"]["saved_fen"] == 1800000
    assert d["last_settled_month"] == MONTH  # 游标推进到当月
    st = d["framework"]["settlement"]
    assert st["month"] == m1  # settlement 保留最后一轮
    assert st["actual_saved_fen"] == 800000
    assert st["saved_fen"] == 1800000 and st["remaining_fen"] == 4200000
    assert st["monthly_target_fen"] == -(-4200000 // 6)  # ceil
    assert st["done"] is False
    assert "（fakes 建议）" in st["advice"] and m1 in st["advice"]
    # 存款目标进度按金额口径：18000/60000 = 30%
    assert d["progress"]["percent"] == 30
    assert d["status"] == "active"

    # 幂等：再读不重复结算（saved_fen 不翻倍）
    d2 = client.get(f"/api/goals/{gid}", params={"account_id": uid}).json()
    assert d2["params"]["saved_fen"] == 1800000
    assert d2["framework"]["settlement"]["month"] == m1
