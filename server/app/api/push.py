"""Web Push 订阅管理（第 5 期）：订阅/退订/VAPID 公钥下发。"""
from fastapi import APIRouter
from pydantic import BaseModel

from ..services import push as svc

router = APIRouter(prefix="/api/push", tags=["push"])


class PushKeys(BaseModel):
    """PushSubscription.toJSON().keys：p256dh 公钥 + auth 密钥。"""

    p256dh: str
    auth: str


class SubscribeIn(BaseModel):
    user_id: str
    endpoint: str
    keys: PushKeys


class UnsubscribeIn(BaseModel):
    endpoint: str


@router.get("/vapid-key")
def vapid_key() -> dict:
    """VAPID 公钥，前端 PushManager.subscribe 的 applicationServerKey。"""
    return {"public_key": svc.public_key()}


@router.post("/subscribe")
def subscribe(body: SubscribeIn) -> dict:
    svc.subscribe(body.user_id, body.endpoint, body.keys.model_dump())
    return {"status": "subscribed"}


@router.post("/unsubscribe")
def unsubscribe(body: UnsubscribeIn) -> dict:
    svc.unsubscribe(body.endpoint)
    return {"status": "unsubscribed"}
