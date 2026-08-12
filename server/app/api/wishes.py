from fastapi import APIRouter
from pydantic import BaseModel

from ..services import memory
from ..services import wishes as svc

router = APIRouter(prefix="/api/wishes", tags=["wishes"])


class AddWishIn(BaseModel):
    circle_id: str
    user_id: str
    content: str = ""  # 配图愿望允许纯图片，content 可空（服务端校验二者至少其一）
    visibility: str = "public"
    image_url: str | None = None


@router.get("")
def list_wishes(circle_id: str, user_id: str | None = None, status: str | None = None):
    return svc.list_wishes(circle_id, user_id, status)


@router.post("")
def add_wish(body: AddWishIn):
    result = svc.add_wish(
        body.circle_id, body.user_id, body.content, body.visibility, body.image_url
    )
    # 写路径打点：画像与相关用户对标记 dirty，等每晚蒸馏重算
    memory.mark_dirty(body.circle_id, body.user_id)
    return result


@router.get("/common")
def common_wishes(circle_id: str):
    return svc.common_wishes(circle_id)


@router.delete("/{wish_id}")
def delete_wish(wish_id: str, user_id: str):
    result = svc.delete_wish(wish_id, user_id)
    # 共同愿望分量留给 nightly 重算（service 层不引 memory，避免与 wishes 循环导入）
    memory.mark_dirty(result["circle_id"], user_id)
    return {"id": result["id"], "status": result["status"]}


@router.post("/{wish_id}/plan")
def generate_plan(wish_id: str):
    return svc.generate_plan(wish_id)
