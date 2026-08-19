"""热量「识别与计算拆开」测试：成分表导入清洗、灌库幂等、LIKE/向量两级匹配、
recognize_calorie 查表命中（kcal=表值×克数）与模型值兜底。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_nutrition.py -v
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_nutrition_"), "test.db")
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
from app.db.database import encode_embedding, get_conn, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import nutrition  # noqa: E402

ASSETS = Path(SERVER_DIR) / "scripts" / "assets" / "food_nutrition.json"


def _load_import_script():
    """scripts/ 非包，按文件路径加载导入脚本模块。"""
    spec = importlib.util.spec_from_file_location(
        "import_food_nutrition", Path(SERVER_DIR) / "scripts" / "import_food_nutrition.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _insert_food(name: str, kcal: float, protein=None, fat=None, cho=None) -> None:
    """灌一条受控营养行（embedding 走 fakes 确定性桩，与灌库口径一致）。"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO food_nutrition
           (name, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g, embedding)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, kcal, protein, fat, cho, encode_embedding(ai.embed_text(name))),
    )
    conn.commit()


@pytest.fixture
def controlled_table():
    """清空成分表，测试自行插入受控行（匹配结果完全确定）。"""
    init_db()  # 单测独立运行时也要保证 schema 已建（幂等）
    conn = get_conn()
    conn.execute("DELETE FROM food_nutrition")
    conn.commit()
    yield _insert_food


def _image_url() -> str:
    """造一张站内存量的假图（识别路由只校验文件存在，不解析内容）。"""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stem = uuid.uuid4().hex
    (settings.upload_dir / f"{stem}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return f"/api/uploads/{stem}.jpg"


# ---------- 导入脚本：脏数据清洗 ----------

def test_import_clean_records_drops_dirty_rows() -> None:
    """OCR 脏数据逐类丢弃：空名/空或错位 kcal/超界 kcal/错位宏量/超界宏量/合计超限。"""
    mod = _load_import_script()
    stats: dict = mod.new_stats()
    records = [
        {"foodName": "米饭（蒸）", "energyKCal": "116", "protein": "2.6", "fat": "0.3", "CHO": "25.9"},
        {"foodName": "  ", "energyKCal": "100"},                                  # empty_name
        {"foodName": "A", "energyKCal": ""},                                      # bad_kcal（空）
        {"foodName": "B", "energyKCal": "11 0.3"},                                # bad_kcal（错位双数值）
        {"foodName": "C", "energyKCal": "470197317"},                             # kcal_out_of_range（kJ 串列）
        {"foodName": "D", "energyKCal": "0"},                                     # kcal_out_of_range（0）
        {"foodName": "E", "energyKCal": "100", "protein": "20.2 30.4"},           # bad_macro（错位）
        {"foodName": "F", "energyKCal": "100", "protein": "150"},                 # macro_out_of_range
        {"foodName": "G", "energyKCal": "100", "protein": "60", "fat": "40", "CHO": "30"},  # macro_sum_over
        {"foodName": "H", "energyKCal": "50", "protein": "-", "fat": "", "CHO": None},      # 缺失记 None，保留
    ]
    kept = mod.clean_records(records, stats)
    assert [r["name"] for r in kept] == ["米饭（蒸）", "H"]
    assert kept[0]["kcal_per_100g"] == 116.0 and kept[0]["cho_per_100g"] == 25.9
    assert kept[1]["protein_per_100g"] is None and kept[1]["fat_per_100g"] is None
    assert stats["empty_name"] == 1 and stats["bad_kcal"] == 2
    assert stats["kcal_out_of_range"] == 2 and stats["bad_macro"] == 1
    assert stats["macro_out_of_range"] == 1 and stats["macro_sum_over"] == 1
    assert stats["total"] == len(records)


def test_import_clean_name_strips_whitespace() -> None:
    """foodName 归一：OCR 字间空格去掉，括号备注保留（肥瘦/品牌影响营养值）。"""
    mod = _load_import_script()
    assert mod.clean_name("西域龙 驴奶粉") == "西域龙驴奶粉"
    assert mod.clean_name(" 猪肉(肥瘦) ") == "猪肉(肥瘦)"


def test_import_load_all_dedupes_by_name(tmp_path) -> None:
    """load_all：跨文件按清洗后名称去重（保留先出现的），统计口径正确。"""
    mod = _load_import_script()
    (tmp_path / "a.json").write_text(json.dumps([
        {"foodName": "米饭", "energyKCal": "116"},
        {"foodName": "米饭 ", "energyKCal": "120"},   # 归一后同名 → 去重
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps([
        {"foodName": "米饭", "energyKCal": "130"},    # 跨文件同名 → 去重
        {"foodName": "燕麦", "energyKCal": "338"},
    ], ensure_ascii=False), encoding="utf-8")
    kept, stats = mod.load_all(tmp_path)
    assert [(r["name"], r["kcal_per_100g"]) for r in kept] == [("米饭", 116.0), ("燕麦", 338.0)]
    assert stats["duplicate_name"] == 2 and stats["kept"] == 2 and stats["total"] == 4


def test_vendor_assets_shape() -> None:
    """vendor JSON 已入库：字段齐全、kcal 在物理合理区间、规模在预期范围。"""
    rows = json.loads(ASSETS.read_text(encoding="utf-8"))
    assert 1000 <= len(rows) <= 2000  # 全表 1500 条上下正常
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names))  # name 唯一（灌库 UNIQUE 前提）
    for r in rows:
        assert set(r) == {"name", "kcal_per_100g", "protein_per_100g", "fat_per_100g", "cho_per_100g"}
        assert 0 < r["kcal_per_100g"] <= 1000


# ---------- 灌库：幂等 ----------

def test_seed_food_nutrition_idempotent(client: TestClient) -> None:
    """init 自动灌入 vendor JSON；重复 init 不重复灌；name 向量已算好。"""
    conn = get_conn()
    expected = len(json.loads(ASSETS.read_text(encoding="utf-8")))
    count = conn.execute("SELECT COUNT(*) AS c FROM food_nutrition").fetchone()["c"]
    assert count == expected
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM food_nutrition WHERE embedding IS NULL"
    ).fetchone()["c"] == 0
    init_db()  # 幂等：再跑一遍条数不变
    assert conn.execute("SELECT COUNT(*) AS c FROM food_nutrition").fetchone()["c"] == count


# ---------- 匹配：LIKE 归一 → 向量兜底 ----------

def test_match_like_hit_on_seeded_table(client: TestClient) -> None:
    """真实成分表：「米饭」归一命中「米饭（蒸，代表值）」（去括号备注后相等）。"""
    hit = nutrition.match("米饭")
    assert hit is not None and hit["via"] == "like"
    assert hit["name"] == "米饭（蒸，代表值）" and hit["kcal_per_100g"] == 116.0
    assert hit["protein_per_100g"] == 2.6


def test_match_like_normalized_containment(controlled_table) -> None:
    """LIKE 归一：括号备注/空白不敏感；查询含表名（互含）也命中。"""
    controlled_table("测试燕麦(即食)", 380, 10.0, 1.0, 60.0)
    controlled_table("测试燕麦麸皮", 246)
    # 归一后与「测试燕麦(即食)」完全相等（rank 0 优于互含），确定性命中
    hit = nutrition.match("测试燕麦")
    assert hit is not None and hit["via"] == "like"
    assert hit["name"] == "测试燕麦(即食)" and hit["kcal_per_100g"] == 380
    # 括号备注不敏感：查询自带备注，归一后仍相等
    hit2 = nutrition.match(" 测试燕麦（加糖）")
    assert hit2 is not None and hit2["via"] == "like" and hit2["name"] == "测试燕麦(即食)"
    # 查询包含表名（互含方向）：取重合比例更高的「测试燕麦麸皮」
    hit3 = nutrition.match("来一碗测试燕麦麸皮")
    assert hit3 is not None and hit3["via"] == "like" and hit3["name"] == "测试燕麦麸皮"


def test_match_vector_hit(controlled_table) -> None:
    """向量兜底：LIKE 不中时按 fakes 确定性向量余弦命中（一字之差，cos≈0.81 > 0.75）。"""
    controlled_table("番茄牛肉炒蛋", 90)
    hit = nutrition.match("蒸茄牛肉炒蛋")
    assert hit is not None and hit["via"] == "vector"
    assert hit["name"] == "番茄牛肉炒蛋" and hit["kcal_per_100g"] == 90


def test_match_below_threshold_returns_none(controlled_table) -> None:
    """阈值未命中：与表内任何行都不相似（余弦 < 0.75）→ None（调用方回退模型值）。"""
    controlled_table("番茄炒蛋", 90)
    assert nutrition.match("红烧狮子头") is None
    assert nutrition.match("") is None


# ---------- recognize_calorie：查表计算 + 模型兜底 ----------

def test_recognize_calorie_table_and_model_fallback(
    client: TestClient, controlled_table, monkeypatch
) -> None:
    """命中查表：kcal = 表值 × 克数 / 100，source=table；未命中回退模型 kcal，source=model。"""
    controlled_table("米饭（蒸）", 116, 2.6, 0.3, 25.9)
    uid = client.post("/api/circles", json={"name": "热量查表圈", "nickname": "阿澈"}).json()["account_id"]
    monkeypatch.setattr(ai, "recognize_food", lambda path, hint="": {
        "items": [
            {"name": "米饭", "grams": 200, "kcal": 9999},   # 查表命中：116×2=232，忽略模型值
            {"name": "神秘料理", "grams": 100, "kcal": 500},  # 未命中：回退模型值
            {"name": "米饭", "kcal": 300},                   # 无 grams：回退模型值
        ],
        "note": "伪装识别",
    })
    r = client.post("/api/calories/recognize", json={
        "account_id": uid, "image_url": _image_url(), "hint": "三人份"})
    assert r.status_code == 200, r.text
    entry = r.json()["entry"]
    assert entry["status"] == "pending"
    items = entry["items"]
    assert items[0] == {"name": "米饭", "kcal": 232, "source": "table", "grams": 200}
    assert items[1] == {"name": "神秘料理", "kcal": 500, "source": "model", "grams": 100}
    assert items[2] == {"name": "米饭", "kcal": 300, "source": "model"}
    assert entry["total_kcal"] == 1032
    assert set(entry["exercise_equiv"]) == {"running", "walking", "cycling", "swimming"}
