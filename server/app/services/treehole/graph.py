"""树洞 LangGraph 状态图 + SqliteSaver 会话状态（落同一个 app.db）。

接线（与交接文档§二对应，2026-08-22 提速改造后）：

    START → route_intent（① 意图路由+查询改写一次调用；护栏前置——强烈自伤意愿
            直接产出干预话术，条件边跳 writeback，省掉检索/工具/生成整段）
          → retrieve（② 混合检索 + L1/L3 注入；query 来自 ①）
          → tools（③ tool calling 循环；intent=vent 直接跳过——倾诉不查数据，
            省掉 tool-plan 的 LLM 空转）
          → generate（④ 人设卡 + 偏好注入 + 引用落地；挂流式回调时逐段外发）
          → writeback（⑥ L0 落库 + L1 抽取 + 滚动摘要压缩）
          → END

降级链（Kimi 抖动只降级不 500）：路由挂了默认 vent；工具决策挂了跳过工具；
生成挂了才如实 502（service 层转换）。降级统一记 ai.mark_degraded，由任务层
（send_message 的 run_task）落 task_runs。
上下文压缩：人设卡/画像/最近 10 轮（20 条）原文永不压缩；更早历史每攒 10 条
增量并入填槽式滚动摘要（facts/emotion_trail/followups/time_anchors，带源消息 id），
摘要随 checkpoint 持久化（summary + summary_upto 游标）。
"""
import logging
import sqlite3
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from ... import ai
from ...config import settings
from ..memory import layers
from . import guardrail, retrieve, tools
from .persona import get_persona
from . import streaming

logger = logging.getLogger("us.treehole")

VERBATIM_MESSAGES = 20   # 最近 10 轮（20 条）原文永不压缩
SUMMARY_BATCH = 10       # 更早历史每攒 10 条增量并入滚动摘要


class TreeholeState(TypedDict, total=False):
    account_id: str
    message: str
    image: str              # 当前消息的图片 data URL（空=纯文本轮）
    image_url: str          # 图片的上传 URL（L0 落库 / 前端展示用）
    intent: str
    rewritten_query: str
    hits: list[dict]
    profile: dict
    tool_results: list[dict]
    tools_used: list[str]
    citations: list[dict]
    reply: str
    guardrail: bool
    summary: dict           # 滚动摘要（随 checkpoint 持久化）
    summary_upto: int       # 摘要已覆盖的 L0 消息数游标


# ---------- 节点 ----------

def node_route(state: TreeholeState) -> dict:
    """① 意图路由 + 查询改写（一次 LLM 调用合并出 intent 与检索 query）。

    护栏前置（原⑤生成后检的改造）：检查对象本来就是用户消息，没必要等 120s 生成完
    再替换——命中强烈自伤意愿直接产出干预话术，条件边跳 writeback，检索/工具/生成
    整段省掉。路由 LLM 挂了默认 vent + 原句检索（倾诉是最安全口径），不炸整轮。
    """
    if guardrail.is_strong_self_harm(state["message"]):
        return {"intent": "vent", "query": "", "guardrail": True,
                "reply": guardrail.INTERVENTION_TEXT, "citations": [],
                "tool_results": [], "tools_used": []}
    try:
        routed = ai.treehole_route(state["message"])
        return {"intent": routed["intent"], "query": routed.get("query") or "",
                "guardrail": False}
    except Exception as exc:  # noqa: BLE001 —— 路由失败降级为倾诉轮，检索用原句
        ai.mark_degraded()
        logger.warning("意图路由失败，降级为倾诉轮：%s", exc)
        return {"intent": "vent", "query": "", "guardrail": False}


def node_retrieve(state: TreeholeState) -> dict:
    """② 混合检索 + 画像注入。query 来自①（合并调用带出）；vent 轮用原句做共鸣检索。"""
    query = state.get("query") or state["message"]
    return {
        "rewritten_query": query,
        "hits": retrieve.recall(state["account_id"], query),
        "profile": layers.account_profile(state["account_id"]),
    }


def node_tools(state: TreeholeState) -> dict:
    """③ tool calling 循环：模型每轮看已拿到的结果决定继不继续，最多 3 轮防死循环。

    倾诉（vent）直接跳过——不查账本不查计划，省掉 tool-plan 的 LLM 空转；决策 LLM
    挂了当轮不带工具（如实按已有上下文回答），不炸整轮。
    """
    if state.get("intent") == "vent":
        return {"tool_results": [], "tools_used": []}
    results: list[dict] = []
    for _ in range(3):
        try:
            plan = ai.treehole_tool_plan(
                state["message"], state.get("intent", "vent"), tools.specs_text(), results
            )
        except Exception as exc:  # noqa: BLE001 —— 工具决策失败：当轮无工具，继续生成
            ai.mark_degraded()
            logger.warning("工具决策失败，当轮跳过工具：%s", exc)
            break
        calls = plan.get("calls") or []
        if not calls:
            break
        for call in calls:
            result = tools.execute(state["account_id"], call["name"], call.get("args") or {})
            if result.get("summary"):
                results.append(result)
    return {"tool_results": results, "tools_used": [r["name"] for r in results]}


def node_generate(state: TreeholeState) -> dict:
    account_id = state["account_id"]
    citations = [
        {"kind": h["kind"], "id": h["id"], "excerpt": h["excerpt"]}
        for h in (state.get("hits") or [])[:3]
    ]
    profile = dict(state.get("profile") or {})
    if profile:
        from ..memory import scenarios  # 延迟导入，避免包初始化成环

        pinned = [s["topic"] for s in scenarios.list_scenarios(account_id) if s["pinned"]]
        if pinned:
            profile["scenarios"] = pinned  # L2 置顶主题常驻注入做底
    payload = {
        "persona": get_persona(account_id),
        "profile": profile,
        "atoms": layers.list_atoms(account_id, limit=5),
        "hits": state.get("hits") or [],
        "summary": state.get("summary") or {},
        "tool_results": state.get("tool_results") or [],
        "history": layers.list_messages(account_id, limit=VERBATIM_MESSAGES),
        "message": state["message"],
        "image": state.get("image") or "",
        "intent": state.get("intent", "vent"),
        "citations": citations,
    }
    # 流式轮（/chat/stream）：on_delta 由 service 挂在线程本地，生成内容逐段外发
    reply = ai.treehole_reply(payload, on_delta=streaming.get_delta_cb())
    return {"reply": reply, "citations": citations}


def node_writeback(state: TreeholeState) -> dict:
    """L0 落库（user+assistant 两条）+ L1 抽取 + 滚动摘要推进。

    护栏命中的轮次不做 L1 抽取：危机倾诉是求助信号，不作为「记忆条目」沉淀。
    assistant 轮随行持久化 citations/tools--历史接口带出，刷新页面「依据/刚刚查了」不丢。
    """
    account_id = state["account_id"]
    user_id = layers.append_message(
        account_id, "user", state["message"], image_url=state.get("image_url") or "")
    reply_id = layers.append_message(
        account_id, "assistant", state["reply"],
        citations=state.get("citations") or [], tools=state.get("tools_used") or [])
    if not state.get("guardrail"):
        atoms = ai.extract_memory_atoms(state["message"], state["reply"])
        layers.insert_atoms(account_id, atoms, source_msg_ids=[user_id, reply_id])

    # 压缩：最近 20 条原文不动，之外每攒 10 条增量并入滚动摘要
    total = layers.count_messages(account_id)
    upto = state.get("summary_upto") or 0
    if total - upto - VERBATIM_MESSAGES >= SUMMARY_BATCH:
        backlog = layers.list_messages(account_id)[upto : total - VERBATIM_MESSAGES]
        summary = ai.treehole_compress(state.get("summary") or {}, backlog)
        return {"summary": summary, "summary_upto": total - VERBATIM_MESSAGES}
    return {}


# ---------- 图装配与会话状态 ----------

_graphs: dict[str, object] = {}


def _checkpointer() -> SqliteSaver:
    """独立连接给 checkpoint 表（与业务连接分离，避免 row_factory/事务互相干扰）。"""
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def get_graph():
    """按 DB_PATH 懒建并缓存编译好的图（测试库与生产库各自独立）。"""
    key = settings.DB_PATH
    if key not in _graphs:
        g = StateGraph(TreeholeState)
        g.add_node("route_intent", node_route)
        g.add_node("retrieve", node_retrieve)
        g.add_node("tools", node_tools)
        g.add_node("generate", node_generate)
        g.add_node("writeback", node_writeback)
        g.add_edge(START, "route_intent")
        # 护栏前置：命中强烈自伤意愿 → 干预话术直达写回，跳过检索/工具/生成
        g.add_conditional_edges(
            "route_intent",
            lambda s: "writeback" if s.get("guardrail") else "retrieve",
            {"retrieve": "retrieve", "writeback": "writeback"},
        )
        g.add_edge("retrieve", "tools")
        g.add_edge("tools", "generate")
        g.add_edge("generate", "writeback")
        g.add_edge("writeback", END)
        _graphs[key] = g.compile(checkpointer=_checkpointer())
    return _graphs[key]


def thread_id_of(account_id: str) -> str:
    return f"treehole:{account_id}"


def clear_session(account_id: str) -> None:
    """清空 LangGraph 会话状态（checkpoint 表按 thread_id 删行，表结构不动）。"""
    conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
    try:
        tid = thread_id_of(account_id)
        existing = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        # langgraph-checkpoint-sqlite 3.x 用 checkpoints/writes 两表；防御性遍历候选名
        for table in ("checkpoints", "writes", "checkpoint_writes", "checkpoint_blobs"):
            if table in existing:
                conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()
