"""情绪树洞 API：chat / chat 流式 / history / persona。全部 account_id 隔离（隐私铁律）。

鉴权（优化清单第 4 项）：树洞是隐私最敏感模块，全路由挂设备令牌校验（Bearer 头，
services/tokens.py）--过渡期缺令牌放行+告警，TOKEN_ENFORCE=on 收紧；带错令牌一律 401。
其余模块待前端带 token 验证后逐步接入。
"""
import json
import queue
import threading

from fastapi import APIRouter, BackgroundTasks, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..services import tasks, tokens, treehole as svc
from ..services.memory import scenarios

router = APIRouter(prefix="/api/treehole", tags=["treehole"])


def _authorized(account_id: str, authorization: str | None) -> None:
    tokens.require_authorized(account_id, authorization)


class ChatIn(BaseModel):
    account_id: str
    message: str = ""          # 纯图消息可空（服务端兜底"（发来一张图片）"）
    image_url: str | None = None  # 先走 /api/uploads 上传，这里只带 URL


def _refresh_l2(account_id: str) -> None:
    """L2 场景聚类后台刷新（经任务层落 task_runs：失败/降级可查，重试 1 次）。"""
    tasks.run_task("treehole_l2_refresh", account_id,
                   lambda: scenarios.refresh_scenarios(account_id), retries=1)


@router.post("/chat")
def chat(body: ChatIn, background_tasks: BackgroundTasks, authorization: str | None = Header(None)):
    """整包响应：{reply, citations, tools_used, intent, guardrail}。

    L2 场景聚类在响应后后台异步刷新（hot path 只做 L0 落库 + L1 抽取）。
    """
    _authorized(body.account_id, authorization)
    result = svc.send_message(body.account_id, body.message, image_url=body.image_url)
    background_tasks.add_task(_refresh_l2, body.account_id)
    return result


@router.post("/chat/stream")
def chat_stream(body: ChatIn, background_tasks: BackgroundTasks, authorization: str | None = Header(None)):
    """SSE 流式：delta 事件逐段推送生成内容，done 事件带最终整包
    （reply/citations/tools_used/intent/guardrail——done 的 reply 是权威全文，
    前端以此对齐展示与落库口径）；error 事件带原因。

    图跑在 worker 线程，delta 经队列送到响应生成器（同步生成器由 Starlette
    在线程池迭代）。护栏轮没有 delta——命中即直达干预话术，done 毫秒级到达。
    """
    background_tasks.add_task(_refresh_l2, body.account_id)  # 响应（流）结束后刷新 L2
    q: queue.Queue = queue.Queue()

    def _worker() -> None:
        try:
            result = svc.send_message(body.account_id, body.message,
                                      image_url=body.image_url,
                                      on_delta=lambda t: q.put(("delta", t)))
            q.put(("done", result))
        except Exception as exc:  # noqa: BLE001 —— HTTPException/detail 一律转 error 事件
            detail = getattr(exc, "detail", None) or "树洞暂时不在，稍后再来"
            q.put(("error", str(detail)))
        finally:
            q.put(None)

    threading.Thread(target=_worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            kind, data = item
            if kind == "delta":
                event = {"type": "delta", "text": data}
            elif kind == "done":
                event = {"type": "done", "result": data}
            else:
                event = {"type": "error", "error": data}
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/history")
def get_history(account_id: str, authorization: str | None = Header(None)):
    """分页：limit 默认 200，before_created（取当前最早一条的 created_at）翻更早；
    has_more 标记还有更早。citations/tools 已随行带出。"""
    _authorized(account_id, authorization)
    return svc.history(account_id)


@router.delete("/history")
def delete_history(account_id: str, authorization: str | None = Header(None)):
    _authorized(account_id, authorization)
    return svc.clear_history(account_id)


class PersonaIn(BaseModel):
    account_id: str
    name: str = ""
    personality: str = ""
    speaking_style: str = ""
    relationship: str = ""
    background: str = ""
    custom_prompt: str = ""  # 整段人设粘贴：非空时生成优先于模板字段
    thinking: str = ""       # 思考程度：fast/balanced/deep（空 = balanced 模型默认）


@router.get("/persona")
def get_persona(account_id: str, authorization: str | None = Header(None)):
    _authorized(account_id, authorization)
    return svc.get_persona(account_id)


@router.put("/persona")
def put_persona(body: PersonaIn, authorization: str | None = Header(None)):
    _authorized(body.account_id, authorization)
    return svc.put_persona(body.account_id, body.model_dump(exclude={"account_id"}))
