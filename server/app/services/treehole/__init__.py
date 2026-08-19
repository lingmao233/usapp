"""情绪树洞（私密对话 agent）：LangGraph 六节点图 + 工具集 + 混合检索 + 护栏。

- graph.py：StateGraph 六节点（意图路由→检索→工具→人设生成→护栏→记忆写回），
  会话状态用 SqliteSaver 落同一个 app.db
- service.py：API 侧编排入口（chat/history/clear）
- 分层记忆在 services/memory/（L0 原文 / L1 原子 / L2 场景 / L3 复用圈子蒸馏）

隐私铁律：一切读写以 account_id 隔离，树洞只服务账号本人。
"""
from .persona import get_persona, put_persona
from .service import clear_history, history, send_message

__all__ = ["send_message", "history", "clear_history", "get_persona", "put_persona"]
