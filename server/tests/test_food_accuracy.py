"""食物识别精度优化回归测试（优化清单 2026-08-22 第 ①②③ 项）：

- ② prompt 改造：克数两步推理（容器 × 密度）与 confidence 字段进 FOOD_PROMPT；
  用户级克数偏置（_gram_bias：中位比值、样本门槛、离谱钳制）注入 calibration 块；
  recognize_calorie 把模型 confidence 落到 item
- ③ 别名与护栏：成分表方括号别名（马铃薯[土豆、洋芋]）参与 LIKE 匹配；
  单字护栏双向化（查询侧防「茶→茶肠」，键侧防「小西红柿→柿」）；
  做法词表补 拍/蒜蓉/清炖（拍黄瓜→黄瓜）

匹配屧行为的数字验收在 scripts/eval_food.py（改完必跑对比），这里钉单元级行为。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_food_accuracy.py -v
"""
import os
import sys
import tempfile
import uuid

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_acc_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import ai  # noqa: E402
from app.ai.prompts import FOOD_PROMPT  # noqa: E402
from app.config import settings  # noqa: E402
from app.db.database import get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ledger as ledger_svc  # noqa: E402
from app.services import nutrition  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def env(client: TestClient):
    """清匹配相关表并种入本文件专用的成分表行（别名/护栏用例的数据底座）。"""
    conn = get_conn()
    for table in ("food_staging_approvals", "food_nutrition_staging", "food_nutrition"):
        conn.execute(f"DELETE FROM {table}")
    rows = [
        ("马铃薯[土豆、洋芋]", 81.0),   # 方括号别名（正规括号，normalize 会剥掉）
        ("樱桃番茄[小西红柿]", 25.0),
        ("柿", 74.0),                   # 单字行：护栏用（防小西红柿互含吸中）
        ("秋黄瓜[旱黄瓜]", 14.0),
        ("番茄[西红柿]", 15.0),
        ("茶肠", 329.0),                # 单字查询护栏用（防 茶→茶肠）
        ("鸡蛋(代表值)", 139.0),
        ("粳米饭(蒸)", 118.0),
    ]
    for name, kcal in rows:
        conn.execute(
            "INSERT INTO food_nutrition (name, brand, kcal_per_100g) VALUES (?, '', ?)",
            (name, kcal))
    conn.commit()
    yield client
    for table in ("food_staging_approvals", "food_nutrition_staging", "food_nutrition"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    init_db()  # 补灌 vendor 数据，不给下游文件留空表


# ---------- ② prompt：两步推理与 confidence ----------


def test_food_prompt_has_volume_density_confidence() -> None:
    """prompt 防呆：容器参照/密度表/两步推理指令和 confidence 字段不能被误删。"""
    for marker in ("第一步", "第二步", "0.6 g/ml", "confidence", "≥0.8", "≤0.5"):
        assert marker in FOOD_PROMPT, f"FOOD_PROMPT 缺少关键块：{marker}"


def test_recognize_food_injects_bias_and_calibration(monkeypatch) -> None:
    """calibration 样例行 + 用户级偏置行都进 prompt；偏置 <5% 视为噪声不注入。"""
    monkeypatch.setattr(settings, "VISION_API_KEY", "test-key")
    monkeypatch.setattr(settings, "VISION_MODEL", "test-model")
    captured: dict = {}

    def fake_vision_json(image_path, prompt, reasoning="", timeout=60.0):
        captured["prompt"] = prompt
        return {"items": [{"name": "米饭", "grams": 200, "kcal": 232}], "note": ""}

    monkeypatch.setattr(ai.vision, "vision_json", fake_vision_json)
    out = ai.recognize_food(
        "/tmp/whatever.jpg",
        calibration=[{"name": "米饭", "brand": "", "ai_grams": 200, "user_grams": 300}],
        bias=1.25,
    )
    assert out is not None
    assert "你上次估 200g，用户实际是 300g" in captured["prompt"]
    assert "平均偏低约 25%" in captured["prompt"] and "×1.25" in captured["prompt"]
    # 偏置接近 1（<5%）不注入
    ai.recognize_food("/tmp/whatever.jpg", bias=1.03)
    assert "×1.03" not in captured["prompt"]


def test_gram_bias_median_gating_and_cap(env) -> None:
    """偏置计算：中位比值；样本不足 None；中位本身离谱（>cap）None（纠正数据不可信）。"""
    conn = get_conn()
    acct = "bias_test_" + uuid.uuid4().hex[:6]
    acct2 = "bias_test_" + uuid.uuid4().hex[:6]
    for a in (acct, acct2):  # 纠正表 account_id 有外键，先落账号行
        conn.execute(
            "INSERT INTO accounts (id, nickname, created_at) VALUES (?, '偏置测试', '2026-08-22T00:00:00')",
            (a,))

    def _add(account: str, ai_g: float, user_g: float) -> None:
        conn.execute(
            """INSERT INTO calorie_gram_corrections
               (id, account_id, name, brand, ai_grams, user_grams, entry_id, created_at)
               VALUES (?, ?, '测试食物', '', ?, ?, '', '2026-08-22T00:00:00')""",
            (uuid.uuid4().hex[:12], account, ai_g, user_g))

    assert ledger_svc._gram_bias(conn, acct) is None  # 无样本
    _add(acct, 100, 120)
    assert ledger_svc._gram_bias(conn, acct) is None  # 样本不足（<3）
    _add(acct, 100, 130)
    _add(acct, 100, 140)
    assert ledger_svc._gram_bias(conn, acct) == 1.3  # median(1.2,1.3,1.4)
    _add(acct, 100, 80)
    assert ledger_svc._gram_bias(conn, acct) == 1.25  # median(1.2,1.3,1.4,0.8)
    for _ in range(4):
        _add(acct2, 100, 300)
    assert ledger_svc._gram_bias(conn, acct2) is None  # 中位 3.0 > cap=1.5：数据不可信


def test_recognize_calorie_stores_confidence(client, env, monkeypatch) -> None:
    """模型自报 confidence 落到 item（截断到 [0,1]，缺失容忍）。"""
    uid = client.post("/api/circles", json={"name": "置信度圈", "nickname": "阿澈"}).json()["account_id"]
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    (settings.upload_dir / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + uuid.uuid4().bytes)
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="", **_: {
        "items": [
            {"name": "鸡蛋", "grams": 50, "kcal": 70, "confidence": 0.92},
            {"name": "粳米饭", "grams": 200, "kcal": 240, "confidence": 7},   # 越界 → 钳到 1
            {"name": "秋黄瓜", "grams": 80, "kcal": 11},                     # 缺失 → 不落字段
        ], "note": ""})
    entry = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": f"/api/uploads/{stem}.jpg"}).json()["entry"]
    items = {i["name"]: i for i in entry["items"]}
    assert items["鸡蛋"]["confidence"] == 0.92
    assert items["粳米饭"]["confidence"] == 1.0
    assert "confidence" not in items["秋黄瓜"]


# ---------- ③ 别名与护栏 ----------


def test_alias_in_brackets_matches(env) -> None:
    """方括号别名参与 LIKE：土豆/洋芋 精确命中 马铃薯[土豆、洋芋]（normalize 剥括号也能中主名）。"""
    for q in ("土豆", "洋芋", "马铃薯"):
        hit = nutrition.match(q)
        assert hit is not None, f"{q} 应命中"
        assert hit["name"] == "马铃薯[土豆、洋芋]" and hit["kcal_per_100g"] == 81.0


def test_alias_survives_broken_brackets(env) -> None:
    """破损括号（[别名】半角开全角关）不再靠运气：别名提取一并覆盖
    （回归：西红柿曾靠厂商括号写错、normalize 剥不掉才碰巧命中）。用独占名避免与种子行平手。"""
    conn = get_conn()
    conn.execute(
        "INSERT INTO food_nutrition (name, brand, kcal_per_100g) VALUES ('佛手瓜[洋瓜】', '', 47.0)")
    conn.commit()
    hit = nutrition.match("洋瓜")
    assert hit is not None and hit["name"] == "佛手瓜[洋瓜】" and hit["kcal_per_100g"] == 47.0


def test_single_char_key_guard(env) -> None:
    """键侧单字护栏：「小西红柿」不得互含吸中单字行「柿」（74 vs 25，实测 196% 误差），
    应命中别名行 樱桃番茄[小西红柿]。"""
    hit = nutrition.match("小西红柿")
    assert hit is not None
    assert hit["name"] == "樱桃番茄[小西红柿]" and hit["kcal_per_100g"] == 25.0


def test_single_char_query_guard_still_holds(env) -> None:
    """查询侧单字护栏回归：「茶」只收完全一致，不互含吸「茶肠」。"""
    assert nutrition.match("茶") is None


def test_cooking_prefix_extended(env) -> None:
    """做法词表扩展：拍黄瓜 → 剥「拍」→ 黄瓜 命中秋黄瓜行。"""
    hit = nutrition.match("拍黄瓜")
    assert hit is not None and hit["kcal_per_100g"] == 14.0


def test_no_false_alias_hits(env) -> None:
    """表里真没有的（饺子）保持 miss——别名扩展不得制造误吸。"""
    assert nutrition.match("饺子") is None


# ---------- 联网搜索可靠性（源头闸门，BUG-022 复盘：红茶搜回干茶叶 294） ----------


def test_web_search_prompt_guardrails() -> None:
    """prompt 防呆：形态口径/单位换算/交叉自检/basis 字段不能被误删。"""
    from app.ai.prompts import WEB_SEARCH_FOOD_PROMPT
    for marker in ("形态必须一致", "4.184", "交叉自检", "basis", "干茶叶"):
        assert marker in WEB_SEARCH_FOOD_PROMPT, f"WEB_SEARCH_FOOD_PROMPT 缺少关键块：{marker}"


def test_web_search_food_injects_form_and_ref_hints(monkeypatch) -> None:
    """饮品名注入「即饮口径」提示；带视觉估值时注入交叉自检锚点，无估值时给常识兜底话术。
    走 Kimi $web_search 真身路径（脱桩），chat_messages 截获 prompt 断言。"""
    import fakes
    from app.ai.prompts import WEB_SEARCH_FOOD_PROMPT  # noqa: F401 —— 确认已可导入
    captured: dict = {}

    def fake_cm(messages, cfg=None, tools=None, timeout=120.0, max_tool_rounds=3):
        captured["prompt"] = messages[0]["content"]
        return '{"kcal_per_100g": 2, "basis": "冲泡后液体每100ml"}'

    monkeypatch.setattr(settings, "LLM_WEB_SEARCH", "on")
    monkeypatch.setattr(settings, "TREEHOLE_API_KEY", "test-key")
    monkeypatch.setattr(ai.llm, "chat_messages", fake_cm)
    monkeypatch.setattr(ai, "web_search_food", fakes.REAL_IMPLS["web_search_food"])

    out = ai.web_search_food("红茶", model_per_100g=4.0)
    assert out is not None and out["kcal_per_100g"] == 2.0 and out["basis"] == "冲泡后液体每100ml"
    p = captured["prompt"]
    assert "这是用户喝的饮品" in p and "即饮" in p          # form_hint
    assert "估算约为 4" in p and "差 10 倍" in p            # 交叉自检锚点

    ai.web_search_food("饼干", model_per_100g=None)          # 非饮品 + 无估值
    p2 = captured["prompt"]
    assert "这是用户喝的饮品" not in p2
    assert "常识量级" in p2 and "估算约为" not in p2
