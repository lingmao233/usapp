from fastapi import APIRouter
from pydantic import BaseModel

from ..services import chat as svc

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatIn(BaseModel):
    user_id: str
    message: str


@router.get("/plan/{wish_id}")
def list_plan_chat(wish_id: str, user_id: str):
    """方案追问记录（无则空列表）。"""
    return svc.list_plan_chat(wish_id, user_id)


@router.post("/plan/{wish_id}")
def send_plan_chat(wish_id: str, body: ChatIn):
    """追加追问并生成助手回复，返回全量对话。"""
    return svc.send_plan_chat(wish_id, body.user_id, body.message)
