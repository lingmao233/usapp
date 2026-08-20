"""情绪树洞 API：chat / history / persona。全部 account_id 隔离（隐私铁律）。"""
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services import treehole as svc
from ..services.memory import scenarios

router = APIRouter(prefix="/api/treehole", tags=["treehole"])


class ChatIn(BaseModel):
    account_id: str
    message: str = ""          # 纯图消息可空（服务端兜底"（发来一张图片）"）
    image_url: str | None = None  # 先走 /api/uploads 上传，这里只带 URL


@router.post("/chat")
def chat(body: ChatIn, background_tasks: BackgroundTasks):
    """整包响应：{reply, citations, tools_used, intent, guardrail}。

    L2 场景聚类在响应后后台异步刷新（hot path 只做 L0 落库 + L1 抽取）。
    """
    result = svc.send_message(body.account_id, body.message, image_url=body.image_url)
    background_tasks.add_task(scenarios.refresh_scenarios, body.account_id)
    return result


@router.get("/history")
def get_history(account_id: str):
    return svc.history(account_id)


@router.delete("/history")
def delete_history(account_id: str):
    return svc.clear_history(account_id)


class PersonaIn(BaseModel):
    account_id: str
    name: str = ""
    personality: str = ""
    speaking_style: str = ""
    relationship: str = ""
    background: str = ""
    custom_prompt: str = ""  # 整段人设粘贴：非空时生成优先于模板字段


@router.get("/persona")
def get_persona(account_id: str):
    return svc.get_persona(account_id)


@router.put("/persona")
def put_persona(body: PersonaIn):
    return svc.put_persona(body.account_id, body.model_dump(exclude={"account_id"}))
