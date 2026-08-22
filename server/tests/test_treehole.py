"""情绪树洞测试（fakes 确定性桩）：意图路由分支、工具真实数据、人设卡注入、
护栏触发/不误伤、记忆写回 L1、隐私边界、历史清空、滚动压缩。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_treehole.py -v
"""
import json
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
os.environ["TREEHOLE_API_KEY"] = ""
os.environ["TREEHOLE_BASE_URL"] = ""
os.environ["TREEHOLE_MODEL"] = ""
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import fakes  # noqa: E402
from app import ai  # noqa: E402
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


def test_persona_custom_prompt_priority(client: TestClient, monkeypatch) -> None:
    """整段人设优先：生成时 system 只带整段原文+名字，模板字段不再注入；
    顺手锁定树洞门面走 TREEHOLE_* 配置（Kimi 默认值 + $web_search 联网工具）。"""
    acc = _new_account(client)
    r = client.put("/api/treehole/persona", json={
        "account_id": acc["account_id"], "name": "阿青",
        "personality": "模板性格不该出现", "custom_prompt": "你是阿青，说话像深夜电台。"})
    assert r.status_code == 200, r.text
    saved = client.get("/api/treehole/persona", params={"account_id": acc["account_id"]}).json()
    assert saved["default"] is False and saved["custom_prompt"] == "你是阿青，说话像深夜电台。"

    captured: dict = {}

    def fake_chat_messages(messages, cfg=None, tools=None, timeout=120.0,
                           max_tool_rounds=3, on_delta=None, reasoning=""):
        captured.update(messages=messages, tools=tools, cfg=cfg, reasoning=reasoning)
        return "收到"

    monkeypatch.setattr(settings, "TREEHOLE_API_KEY", "test-key")
    monkeypatch.setattr(settings, "TREEHOLE_WEB_SEARCH", "on")
    monkeypatch.setattr(ai.llm, "chat_messages", fake_chat_messages)
    monkeypatch.setattr(ai, "treehole_reply", fakes.REAL_IMPLS["treehole_reply"])  # 脱桩走真身
    _chat(client, acc["account_id"], "今晚睡不着")

    assert captured["cfg"] == ("test-key", "https://api.moonshot.cn/v1", "kimi-k2.6")
    assert captured["tools"] == [{"type": "builtin_function", "function": {"name": "$web_search"}}]
    system = captured["messages"][0]
    assert system["role"] == "system"
    assert "你是阿青，说话像深夜电台。" in system["content"]
    assert "名字：阿青" in system["content"]
    assert "模板性格不该出现" not in system["content"]
    last = captured["messages"][-1]
    assert last["role"] == "user" and last["content"] == "今晚睡不着"


# ---------- 图片消息 ----------

def _plant_upload(name: str) -> str:
    """造一个合法命名的上传文件（caption 走桩，不校验图片内容），返回 image_url。"""
    up = settings.upload_dir
    up.mkdir(parents=True, exist_ok=True)
    (up / name).write_bytes(b"fake-jpeg")
    return f"/api/uploads/{name}"


def test_chat_with_image(client: TestClient) -> None:
    """图片消息：caption 写进 L0 原文（L1 抽取/检索的输入），image_url 落库并随 history 返回。"""
    acc = _new_account(client)
    url = _plant_upload("ab" * 16 + ".jpg")
    r = client.post("/api/treehole/chat", json={
        "account_id": acc["account_id"], "message": "这是我", "image_url": url})
    assert r.status_code == 200, r.text
    items = client.get("/api/treehole/history",
                       params={"account_id": acc["account_id"]}).json()["items"]
    user_msg = [m for m in items if m["role"] == "user"][-1]
    assert user_msg["image_url"] == url
    assert "这是我" in user_msg["content"]
    assert "[图片：用户发来的jpeg图片（随图说：这是我）]" in user_msg["content"]


def test_chat_image_only_and_bad_url(client: TestClient) -> None:
    """纯图消息（空文本）不 400；非法/不存在的 image_url 降级为纯文本轮。"""
    acc = _new_account(client)
    url = _plant_upload("cd" * 16 + ".png")
    r = client.post("/api/treehole/chat", json={
        "account_id": acc["account_id"], "message": "", "image_url": url})
    assert r.status_code == 200, r.text
    items = client.get("/api/treehole/history",
                       params={"account_id": acc["account_id"]}).json()["items"]
    last_user = [m for m in items if m["role"] == "user"][-1]
    assert last_user["content"].startswith("[图片：用户发来的png图片")

    r = client.post("/api/treehole/chat", json={
        "account_id": acc["account_id"], "message": "", "image_url": "/api/uploads/not-a-file"})
    assert r.status_code == 200, r.text
    items = client.get("/api/treehole/history",
                       params={"account_id": acc["account_id"]}).json()["items"]
    last_user = [m for m in items if m["role"] == "user"][-1]
    assert last_user["content"] == "（发来一张图片）" and not last_user["image_url"]


def test_web_search_echo_protocol(monkeypatch) -> None:
    """$web_search 回声协议：finish_reason=tool_calls → 原样回显 arguments(role=tool) → stop。"""
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)

        class R:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                if len(calls) == 1:
                    return {"choices": [{"finish_reason": "tool_calls", "message": {
                        "role": "assistant", "content": "",
                        "tool_calls": [{"id": "tc1", "type": "builtin_function", "function": {
                            "name": "$web_search", "arguments": '{"q":"今天天气"}'}}]}}]}
                return {"choices": [{"finish_reason": "stop",
                                     "message": {"role": "assistant", "content": "今天晴"}}]}

        return R()

    monkeypatch.setattr(ai.llm.httpx, "post", fake_post)
    out = ai.llm.chat_messages(
        [{"role": "user", "content": "今天天气怎么样"}],
        cfg=("k", "https://api.moonshot.cn/v1", "kimi-k2.6"),
        tools=[{"type": "builtin_function", "function": {"name": "$web_search"}}])
    assert out == "今天晴"
    assert len(calls) == 2
    second = calls[1]["messages"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "tc1"
    # 回显时 type 归一为 OpenAI 线格式 "function"（builtin_function 会被 kimi.com/coding 网关 400）
    assert second[-2]["tool_calls"][0]["type"] == "function"
    assert second[-1]["role"] == "tool" and second[-1]["content"] == '{"q":"今天天气"}'


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


# ---------- 2026-08-22 提速/降级/护栏前置/流式改造（优化实录见 docs） ----------


def test_vent_skips_tools_node(client: TestClient, monkeypatch) -> None:
    """倾诉轮结构性跳过工具节点：tool-plan LLM 一次都不调（原实现 vent 也逃不掉首轮）。"""
    acc = _new_account(client, "提速圈")
    calls: list[str] = []
    orig = ai.treehole_tool_plan

    def spy(message, intent, tools_desc, results):
        calls.append(message)
        return orig(message, intent, tools_desc, results)

    monkeypatch.setattr(ai, "treehole_tool_plan", spy)
    result = _chat(client, acc["account_id"], "今天被领导当众训了，真的很难受")
    assert result["intent"] == "vent" and calls == []  # 倾诉：工具决策零调用
    result2 = _chat(client, acc["account_id"], "我这个月花了多少钱")
    assert result2["intent"] == "data" and len(calls) >= 1  # 查数据：照常决策


def test_route_failure_degrades_to_vent(client: TestClient, monkeypatch) -> None:
    """路由 LLM 挂了：降级为倾诉轮（检索用原句），整轮 200 不 500。"""
    acc = _new_account(client, "降级圈")

    def boom(message):
        raise RuntimeError("kimi 超时")

    monkeypatch.setattr(ai, "treehole_route", boom)
    result = _chat(client, acc["account_id"], "最近换工作好纠结，你说我该怎么选")
    assert result["intent"] == "vent" and result["reply"]


def test_guardrail_short_circuits_generation(client: TestClient, monkeypatch) -> None:
    """护栏前置：强烈自伤意愿在路由节点短路，生成 LLM 一次都不调（原实现生成后检，
    命中时 120s 的回复已经花掉再整条丢弃）。"""
    acc = _new_account(client, "前置圈")
    replies: list[dict] = []
    monkeypatch.setattr(ai, "treehole_reply",
                        lambda payload, on_delta=None: replies.append(payload) or "不该出现")
    result = _chat(client, acc["account_id"], "活着没意思，我不想活了")
    assert result["guardrail"] is True and "400-161-9995" in result["reply"]
    assert replies == []  # 生成被短路


def test_guardrail_extended_signals() -> None:
    """词表扩充：否定式存在表达触发；新夸张缓冲不误伤。"""
    from app.services.treehole import guardrail
    assert guardrail.is_strong_self_harm("我不想存在于这个世界")
    assert guardrail.is_strong_self_harm("这样的日子什么时候是个头，真想解脱")
    assert not guardrail.is_strong_self_harm("困得想死，明天还要早起")
    assert not guardrail.is_strong_self_harm("无聊得想死，这电影也太长了")


def test_chat_records_task_runs(client: TestClient) -> None:
    """可观测性：每轮对话落 task_runs（treehole_chat），状态/起止时间可查。"""
    acc = _new_account(client, "观测圈")
    before = _db().execute(
        "SELECT COUNT(*) AS c FROM task_runs WHERE task_name = 'treehole_chat'"
    ).fetchone()["c"]
    _chat(client, acc["account_id"], "随便说点什么")
    row = _db().execute(
        """SELECT status, started_at, finished_at FROM task_runs
           WHERE task_name = 'treehole_chat' ORDER BY rowid DESC LIMIT 1"""
    ).fetchone()
    assert row["status"] in ("success", "degraded") and row["finished_at"]
    after = _db().execute(
        "SELECT COUNT(*) AS c FROM task_runs WHERE task_name = 'treehole_chat'"
    ).fetchone()["c"]
    assert after == before + 1


def test_chat_stream_endpoint(client: TestClient) -> None:
    """流式端点：SSE 事件流按 delta…done 顺序到达，done 带权威整包；
    护栏轮没有 delta（命中即直达干预话术）。TestClient 会缓冲整包后返回，顺序仍可断言。"""
    acc = _new_account(client, "流式圈")
    with client.stream("POST", "/api/treehole/chat/stream",
                       json={"account_id": acc["account_id"], "message": "今天有点累，想聊聊"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[len("data:"):].strip())
                  for line in resp.iter_lines() if line.startswith("data:")]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "delta" and kinds[-1] == "done" and "error" not in kinds
    assert "".join(e["text"] for e in events if e["type"] == "delta") == events[-1]["result"]["reply"]
    # 护栏轮：无 delta，done 毫秒级直达
    with client.stream("POST", "/api/treehole/chat/stream",
                       json={"account_id": acc["account_id"], "message": "我不想活了"}) as resp:
        events = [json.loads(line[len("data:"):].strip())
                  for line in resp.iter_lines() if line.startswith("data:")]
    assert [e["type"] for e in events] == ["done"]
    assert events[0]["result"]["guardrail"] is True


def test_tool_plan_failure_degrades(client: TestClient, monkeypatch) -> None:
    """工具决策 LLM 挂了：当轮无工具继续生成，200 不 500。"""
    acc = _new_account(client, "工具降级圈")

    def boom(message, intent, tools_desc, results):
        raise RuntimeError("kimi 5xx")

    monkeypatch.setattr(ai, "treehole_tool_plan", boom)
    result = _chat(client, acc["account_id"], "我这个月花了多少钱")
    assert result["tools_used"] == [] and result["reply"]


# ---------- 2026-08-22 追加：query_calories 菜名明细 + 思考程度参数 ----------


def test_query_calories_returns_food_names(client: TestClient) -> None:
    """「我吃了什么」要答得上来：summary/data 带每餐菜名明细（去重、上限截断），
    支持指定 day；无明细的纯数字记录回退 note。"""
    from datetime import date
    from app.services.treehole import tools as treehole_tools

    acc = _new_account(client, "吃什么圈")
    conn = _db()
    today = date.today().isoformat()
    rows = [
        ("c1", 500.0, json.dumps([{"name": "米饭", "kcal": 232},
                                  {"name": "番茄炒蛋", "kcal": 170},
                                  {"name": "米饭", "kcal": 98}], ensure_ascii=False), "午饭"),
        ("c2", 300.0, json.dumps([{"name": "面条", "kcal": 300}], ensure_ascii=False), "晚饭"),
        ("c3", 150.0, "[]", "加餐"),  # 无明细：回退 note
    ]
    for rid, kcal, items, note in rows:
        conn.execute(
            """INSERT INTO calorie_entries (id, account_id, total_kcal, items, note,
               source, image_url, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'manual', '', 'confirmed', ?)""",
            (rid, acc["account_id"], kcal, items, note, f"{today}T12:00:00"))
    conn.commit()
    out = treehole_tools.query_calories(acc["account_id"], {})
    assert "吃了" in out["summary"] and "米饭" in out["summary"]
    assert "番茄炒蛋" in out["summary"] and "面条" in out["summary"]
    assert out["summary"].count("米饭") == 1  # 跨餐去重
    details = out["data"]["meal_details"]
    assert details[0]["foods"] == "米饭、番茄炒蛋"
    assert details[2]["foods"] == "加餐"  # 空明细回退 note
    # 指定 day
    out2 = treehole_tools.query_calories(acc["account_id"], {"day": "2020-01-01"})
    assert "还没有热量记录" in out2["summary"]


def test_thinking_level_flows_to_llm(client: TestClient, monkeypatch) -> None:
    """思考程度随人设卡存储并流到 LLM：deep → reasoning=high；默认 balanced → 不传参；
    非法档位 400。"""
    acc = _new_account(client, "思考圈")
    captured: dict = {}
    monkeypatch.setattr(settings, "TREEHOLE_API_KEY", "test-key")

    def fake_chat_messages(messages, cfg=None, tools=None, timeout=120.0,
                           max_tool_rounds=3, on_delta=None, reasoning=""):
        captured["reasoning"] = reasoning
        return "收到"

    monkeypatch.setattr(ai.llm, "chat_messages", fake_chat_messages)
    monkeypatch.setattr(ai, "treehole_reply", fakes.REAL_IMPLS["treehole_reply"])

    _chat(client, acc["account_id"], "随便聊聊")
    assert captured["reasoning"] == ""  # 默认：模型默认档（不传参）

    r = client.put("/api/treehole/persona", json={
        "account_id": acc["account_id"], "name": "阿青", "thinking": "deep"})
    assert r.status_code == 200 and r.json()["thinking"] == "deep"
    _chat(client, acc["account_id"], "再聊聊")
    assert captured["reasoning"] == "high"  # 深思 → high

    client.put("/api/treehole/persona", json={
        "account_id": acc["account_id"], "name": "阿青", "thinking": "fast"})
    _chat(client, acc["account_id"], "还聊聊")
    assert captured["reasoning"] == "off"  # 快 → 关思考

    r = client.put("/api/treehole/persona", json={
        "account_id": acc["account_id"], "thinking": "ultra"})
    assert r.status_code == 400  # 非法档位白名单拒收


def test_llm_reasoning_400_strips_and_retries(monkeypatch) -> None:
    """厂商拒思考参数（400 提到 thinking/reasoning）→ 剥掉重试一次，调用成功。"""
    import httpx as _hx

    from app.ai import llm as llm_mod

    calls: list[dict] = []
    req = _hx.Request("POST", "http://x/chat/completions")
    resp400 = _hx.Response(
        400, text='{"error":"The enable_thinking parameter is not supported"}', request=req)
    resp200 = _hx.Response(
        200, json={"choices": [{"message": {"content": "好的"}, "finish_reason": "stop"}]},
        request=req)

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return resp400 if len(calls) == 1 else resp200

    monkeypatch.setattr(llm_mod.httpx, "post", fake_post)
    out = llm_mod.chat_messages([{"role": "user", "content": "hi"}],
                                cfg=("k", "http://x", "m"), reasoning="high")
    assert out == "好的"
    assert len(calls) == 2
    assert calls[0]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in calls[1]  # 重试已剥掉思考参数


def test_langmem_temperature_shares_llm_resolver(monkeypatch) -> None:
    """langmem 的 ChatOpenAI 温度必须与 llm 层同一份解析（LLM_TEMPERATURE）——
    k3 只接受 1，写死 0 会让 L1 记忆抽取全量 400、一次都写不进（BUG-024）。
    注：test_config 会 reload(config)，模块里可能同时存活新旧两个 settings 实例
    （llm.py 持旧、config.settings 是新）——两个都得 patch，单点 patch 在全量顺序下失灵。"""
    import app.config as config

    from app.ai import langmem_ext
    from app.ai import llm as llm_mod

    live = {id(s): s for s in (config.settings, llm_mod.settings)}
    for s in live.values():
        monkeypatch.setattr(s, "TREEHOLE_API_KEY", "test-key")  # 构造 ChatOpenAI 需要凭证
        monkeypatch.setattr(s, "LLM_TEMPERATURE", "1")
    monkeypatch.setattr(langmem_ext, "_manager", None)  # 清懒建缓存
    llm_client = langmem_ext._build_llm()
    assert llm_client.temperature == 1.0

    for s in live.values():
        monkeypatch.setattr(s, "LLM_TEMPERATURE", "")
    monkeypatch.setattr(langmem_ext, "_manager", None)
    assert langmem_ext._build_llm().temperature == 0.7  # 未配置回退默认，两侧行为一致


# ---------- 2026-08-23 追加：history 分页 / citations·tools 持久化 / L1 去重 ----------


def _insert_msg(conn: sqlite3.Connection, account_id: str, msg_id: str,
                content: str, created_at: str) -> None:
    """直插 L0 消息（不经 chat 管线）：分页测试需要可控且互不相同的 created_at
    （_now 只有秒精度，同秒行会撞 before_created 的严格小于游标）。"""
    conn.execute(
        "INSERT INTO treehole_messages (id, account_id, role, content, image_url,"
        " citations, tools, created_at) VALUES (?, ?, 'user', ?, '', '[]', '[]', ?)",
        (msg_id, account_id, content, created_at))


def test_history_pagination(client: TestClient) -> None:
    """分页：limit 控制页大小（服务端多取 1 条判 has_more），before_created 游标翻更早，
    两页拼接与全量一致（正序）。"""
    acc = _new_account(client, "分页圈")
    conn = _db()
    for i in range(5):
        _insert_msg(conn, acc["account_id"], f"pg{i}", f"第 {i} 条",
                    f"2026-01-01T12:00:0{i}")
    conn.commit()

    page1 = client.get("/api/treehole/history",
                       params={"account_id": acc["account_id"], "limit": 3}).json()
    assert [m["id"] for m in page1["items"]] == ["pg2", "pg3", "pg4"]
    assert page1["has_more"] is True

    page2 = client.get(
        "/api/treehole/history",
        params={"account_id": acc["account_id"], "limit": 3,
                "before_created": page1["items"][0]["created_at"]}).json()
    assert [m["id"] for m in page2["items"]] == ["pg0", "pg1"]
    assert page2["has_more"] is False


def test_history_default_page_size(client: TestClient) -> None:
    """默认页大小 200（上限 500）：超过截到 200 并标 has_more（前端首屏依赖）。"""
    acc = _new_account(client, "默认页圈")
    conn = _db()
    for i in range(205):
        _insert_msg(conn, acc["account_id"], f"big{i}", f"第 {i} 条",
                    f"2026-01-02T12:{i // 60:02d}:{i % 60:02d}")
    conn.commit()
    page = client.get("/api/treehole/history",
                      params={"account_id": acc["account_id"]}).json()
    assert len(page["items"]) == 200 and page["has_more"] is True


def test_history_persists_citations_tools(client: TestClient) -> None:
    """citations/tools 随 L0 持久化：chat 后从 history 读回（刷新页面不再丢依据）。"""
    acc = _new_account(client, "依据圈")
    frag = client.post("/api/fragments", json={
        "circle_id": acc["circle_id"], "user_id": acc["user_id"],
        "content": "想去冰岛看极光", "visibility": "private"})
    assert frag.status_code == 200, frag.text
    frag_id = frag.json()["id"]

    reply = _chat(client, acc["account_id"], "我最近想去冰岛")
    assert any(c["id"] == frag_id for c in reply["citations"])  # 当轮召回自己的碎片

    r = client.post("/api/ledger/expenses", json={
        "account_id": acc["account_id"], "amount_fen": 3550,
        "category": "餐饮", "merchant": "麦当劳"})
    assert r.status_code == 200, r.text
    assert "query_ledger" in _chat(
        client, acc["account_id"], "我这个月花了多少钱？")["tools_used"]

    items = client.get("/api/treehole/history",
                       params={"account_id": acc["account_id"]}).json()["items"]
    assistants = [m for m in items if m["role"] == "assistant"]
    assert any(c["id"] == frag_id for m in assistants for c in m["citations"])
    assert any("query_ledger" in m["tools"] for m in assistants)


def test_l1_dedup_repeat_vent(client: TestClient) -> None:
    """L1 去重：同一句倾诉发两遍，同文本原子只落一条（insert_atoms 精确文本 +
    余弦 ≥0.9 跳过；fakes 桩确定性保证两轮文本/嵌入一致）。"""
    acc = _new_account(client, "去重圈")
    msg = "我喜欢吃辣的火锅"
    _chat(client, acc["account_id"], msg)
    _chat(client, acc["account_id"], msg)

    contents = [a["content"] for a in layers.list_atoms(acc["account_id"])]
    assert contents, "第一轮就该抽出原子"
    assert len(contents) == len(set(contents)), f"重复倾诉不该落重复原子：{contents}"
