"""情绪树洞测试（fakes 确定性桩）：意图路由分支、工具真实数据、人设卡注入、
护栏触发/不误伤、记忆写回 L1、隐私边界、历史清空、滚动压缩。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_treehole.py -v
"""
import os
import sqlite3
import sys
import tempfile

# 独立测试数据库 + 清空厂商 key（挡住 .env 回填；AI 由 conftest 装 tests/fakes 确定性桩），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_treehole_"), "test.db")
os.environ["LLM_API_KEY"] = ""
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["VISION_API_KEY"] = ""
os.environ["VISION_MODEL"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.memory import layers, scenarios  # noqa: E402
from app.services.treehole import graph as graph_mod  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _new_account(client: TestClient, nickname: str = "阿澈") -> dict:
    """建圈即建账号：返回 {account_id, user_id, circle_id}。"""
    r = client.post("/api/circles", json={"name": "树洞测试圈", "nickname": nickname})
    assert r.status_code == 200, r.text
    body = r.json()
    return {"account_id": body["account_id"], "user_id": body["user_id"], "circle_id": body["id"]}


def _chat(client: TestClient, account_id: str, message: str) -> dict:
    r = client.post("/api/treehole/chat", json={"account_id": account_id, "message": message})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 意图路由分支 ----------

def test_intent_routing(client: TestClient) -> None:
    """vent/question/data 三分支；data 优先于疑问词（"花了多少钱？"是查数据不是提问）。"""
    acc = _new_account(client)
    assert _chat(client, acc["account_id"], "今天上班好累，什么都不想干")["intent"] == "vent"
    assert _chat(client, acc["account_id"], "你觉得我该不该换工作？")["intent"] == "question"
    assert _chat(client, acc["account_id"], "我这个月花了多少钱啊")["intent"] == "data"


# ---------- 工具节点：查账工具返回真实记账数据 ----------

def test_tool_call_returns_real_ledger_data(client: TestClient) -> None:
    acc = _new_account(client)
    r = client.post("/api/ledger/expenses", json={
        "account_id": acc["account_id"], "amount_fen": 3550,
        "category": "餐饮", "merchant": "麦当劳"})
    assert r.status_code == 200, r.text

    result = _chat(client, acc["account_id"], "我这个月花了多少钱？")
    assert result["intent"] == "data"
    assert "query_ledger" in result["tools_used"]
    assert "35.50 元" in result["reply"]  # 真实账本数字进了回复
    assert "餐饮" in result["reply"]


def test_vent_does_not_call_tools(client: TestClient) -> None:
    """纯倾诉不调工具（模型决定调不调；桩口径：无数据关键词不调）。"""
    acc = _new_account(client)
    result = _chat(client, acc["account_id"], "今天加班到十点，真的好累")
    assert result["intent"] == "vent"
    assert result["tools_used"] == []


# ---------- 人设卡 ----------

def test_persona_card(client: TestClient) -> None:
    acc = _new_account(client)
    # 未设立：默认倾听者人设
    default = client.get("/api/treehole/persona", params={"account_id": acc["account_id"]}).json()
    assert default["default"] is True and default["name"] == "树洞"
    assert "【树洞】" in _chat(client, acc["account_id"], "随便聊聊")["reply"]

    # 设立后每轮按卡扮演（桩回复带头牌人设名）
    r = client.put("/api/treehole/persona", json={
        "account_id": acc["account_id"], "name": "阿暖",
        "personality": "毒舌但心软", "speaking_style": "短句、爱用反问",
        "relationship": "损友", "background": "认识十年的老同学"})
    assert r.status_code == 200, r.text
    saved = client.get("/api/treehole/persona", params={"account_id": acc["account_id"]}).json()
    assert saved["default"] is False and saved["name"] == "阿暖"
    assert saved["personality"] == "毒舌但心软" and saved["relationship"] == "损友"
    assert "【阿暖】" in _chat(client, acc["account_id"], "今天心情不太好")["reply"]


# ---------- 护栏：强烈自伤触发 / 普通情绪不误伤 ----------

def test_guardrail_triggers_on_strong_self_harm(client: TestClient) -> None:
    acc = _new_account(client)
    result = _chat(client, acc["account_id"], "活着没意思，我不想活了")
    assert result["guardrail"] is True
    assert "400-161-9995" in result["reply"]
    assert result["citations"] == []  # 干预话术不带引用
    # 护栏轮次不做 L1 抽取（危机倾诉不沉淀为记忆条目）
    conn = _db()
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM memory_atoms WHERE account_id = ?", (acc["account_id"],)
    ).fetchone()["c"] == 0


def test_guardrail_not_triggered_by_normal_venting(client: TestClient) -> None:
    acc = _new_account(client)
    for msg in ("今天上班好累", "烦死了，又是那种破事", "累得想死，先睡为敬"):
        result = _chat(client, acc["account_id"], msg)
        assert result["guardrail"] is False, msg
        assert "400-161-9995" not in result["reply"]


# ---------- 记忆写回：L0 落库 + L1 原子抽取 ----------

def test_memory_writeback_produces_atoms(client: TestClient) -> None:
    acc = _new_account(client)
    _chat(client, acc["account_id"], "我喜欢吃辣的，我打算明年去冰岛。")

    atoms = layers.list_atoms(acc["account_id"])
    by_kind = {a["kind"]: a for a in atoms}
    assert "preference" in by_kind and "辣" in by_kind["preference"]["content"]
    assert "commitment" in by_kind and "冰岛" in by_kind["commitment"]["content"]

    # 来源消息 id 可回溯到 L0 原文
    conn = _db()
    for atom in atoms:
        for msg_id in atom["source_msg_ids"]:
            row = conn.execute(
                "SELECT 1 FROM treehole_messages WHERE id = ? AND account_id = ?",
                (msg_id, acc["account_id"]),
            ).fetchone()
            assert row is not None

    # L0：一轮 = user + assistant 两条原文
    msgs = layers.list_messages(acc["account_id"])
    assert len(msgs) == 2 and msgs[0]["role"] == "user" and msgs[1]["role"] == "assistant"


def test_l2_scenarios_cluster(client: TestClient) -> None:
    """同主题 L1 原子聚成 L2 场景（后台聚类，测试直接触发刷新）。"""
    acc = _new_account(client)
    _chat(client, acc["account_id"], "我喜欢吃辣的火锅")
    _chat(client, acc["account_id"], "我喜欢吃辣的烧烤")
    scenarios.refresh_scenarios(acc["account_id"])
    items = scenarios.list_scenarios(acc["account_id"])
    assert items, "两条共享「辣的」的偏好原子应聚成一个场景"
    assert any("辣" in s["topic"] for s in items)
    covered = {aid for s in items for aid in s["atom_ids"]}
    assert len(covered) == 2
    assert sum(s["pinned"] for s in items) >= 1


# ---------- 隐私边界：A 的消息/记忆 B 读不到 ----------

def test_privacy_boundary(client: TestClient) -> None:
    a = _new_account(client, "阿澈")
    b = _new_account(client, "丫丫")

    # A 留一条私密碎片 + 一轮树洞对话（产出 L0/L1）
    r = client.post("/api/fragments", json={
        "circle_id": a["circle_id"], "user_id": a["user_id"],
        "content": "想去冰岛看极光", "visibility": "private"})
    assert r.status_code == 200, r.text
    reply_a = _chat(client, a["account_id"], "我最近想去冰岛")
    assert "极光" in reply_a["reply"]  # A 的碎片被召回并引用（带 excerpt）

    # B 没聊过：历史/原子/场景全空（A 的数据一点不渗过来）
    assert client.get(
        "/api/treehole/history", params={"account_id": b["account_id"]}).json()["items"] == []
    assert layers.list_atoms(b["account_id"]) == []
    assert scenarios.list_scenarios(b["account_id"]) == []

    # B 聊同样的话题：召不回 A 的碎片，回复绝不出现 A 的私密内容
    reply_b = _chat(client, b["account_id"], "我也想去冰岛旅行")
    assert "极光" not in reply_b["reply"]
    assert all(c["id"] != r.json()["id"] for c in reply_b["citations"])


# ---------- 历史与清空 ----------

def test_history_and_clear(client: TestClient) -> None:
    acc = _new_account(client)
    _chat(client, acc["account_id"], "我喜欢吃辣的")
    _chat(client, acc["account_id"], "今天有点累")

    items = client.get(
        "/api/treehole/history", params={"account_id": acc["account_id"]}).json()["items"]
    assert len(items) == 4  # 两轮 × (user + assistant)
    assert [m["role"] for m in items] == ["user", "assistant", "user", "assistant"]

    r = client.delete("/api/treehole/history", params={"account_id": acc["account_id"]})
    assert r.status_code == 200 and r.json()["status"] == "cleared"
    assert client.get(
        "/api/treehole/history", params={"account_id": acc["account_id"]}).json()["items"] == []
    # 清空对话不清记忆（L1 保留），人设卡也不动
    assert layers.list_atoms(acc["account_id"]) != []
    # 会话状态已清：新一轮正常跑、历史从零开始
    _chat(client, acc["account_id"], "我回来了")
    items = client.get(
        "/api/treehole/history", params={"account_id": acc["account_id"]}).json()["items"]
    assert len(items) == 2


# ---------- 上下文压缩：滚动摘要 ----------

def test_rolling_summary_compression(client: TestClient) -> None:
    """最近 10 轮（20 条）原文不压缩；更早历史攒满 10 条并入填槽式摘要（带源消息 id）。"""
    acc = _new_account(client)
    for i in range(15):  # 15 轮 = 30 条；第 15 轮触发首次压缩（30-20=10 条 backlog）
        _chat(client, acc["account_id"], f"今天发生了第 {i} 件事，记下来")

    graph = graph_mod.get_graph()
    snap = graph.get_state(
        {"configurable": {"thread_id": graph_mod.thread_id_of(acc["account_id"])}})
    summary = snap.values.get("summary") or {}
    assert snap.values.get("summary_upto") == 10  # 30 条 - 20 条原文窗口
    assert summary.get("facts"), "压缩后应产出关键事实槽"
    assert all(f.get("msg_ids") for f in summary["facts"])  # 条目带源消息 id
    assert summary.get("emotion_trail")

    # L0 原文一条没少（摘要不替代原文，可回溯）
    assert layers.count_messages(acc["account_id"]) == 30
