"""树洞服务编排：API 侧入口。图跑一轮 → 整包响应；历史/清空直读 L0 与 checkpoint。"""
from fastapi import HTTPException

from ...db.database import get_conn
from .. import selfshare
from ..memory import layers
from . import graph as graph_mod


def _require_account(account_id: str) -> None:
    selfshare.require_account(get_conn(), account_id)


def send_message(account_id: str, message: str) -> dict:
    """跑一轮树洞图，返回 {reply, citations, tools_used, intent}（整包响应）。"""
    _require_account(account_id)
    message = (message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空")
    message = message[:2000]
    final = graph_mod.get_graph().invoke(
        {"account_id": account_id, "message": message},
        config={"configurable": {"thread_id": graph_mod.thread_id_of(account_id)}},
    )
    return {
        "reply": final.get("reply") or "",
        "citations": final.get("citations") or [],
        "tools_used": final.get("tools_used") or [],
        "intent": final.get("intent") or "vent",
        "guardrail": bool(final.get("guardrail")),
    }


def history(account_id: str) -> dict:
    """对话原文（L0 正序全量）。"""
    _require_account(account_id)
    return {"items": layers.list_messages(account_id)}


def clear_history(account_id: str) -> dict:
    """清空 L0 原文 + LangGraph 会话状态（滚动摘要随 checkpoint 一并消失）。

    L1/L2 记忆保留——清空的是对话，不是记忆（与人设卡解耦，人设卡也不动）。
    """
    _require_account(account_id)
    layers.clear_messages(account_id)
    graph_mod.clear_session(account_id)
    return {"status": "cleared"}
