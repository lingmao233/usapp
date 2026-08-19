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
    """造一张站内存量的假图（识别路由只校验文件存在，不解析内容）。"""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    (settings.upload_dir / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
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
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="": {
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
