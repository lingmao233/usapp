from fastapi import APIRouter, BackgroundTasks
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
def add_wish(body: AddWishIn, background_tasks: BackgroundTasks):
    result = svc.add_wish(
        body.circle_id, body.user_id, body.content, body.visibility, body.image_url
    )
    # 分类 + 向量化异步执行（与碎片管线同模式），提交秒回
    background_tasks.add_task(svc.process_wish, result["id"])
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


class SetDoneIn(BaseModel):
    user_id: str
    done: bool


@router.put("/{wish_id}/done")
def set_wish_done(wish_id: str, body: SetDoneIn):
    """勾选完成 / 取消完成：完成的愿望移出共同愿望匹配池，可逆。"""
    result = svc.set_wish_done(wish_id, body.user_id, body.done)
    memory.mark_dirty(result["circle_id"], body.user_id)
    return {"id": result["id"], "status": result["status"]}


class PlanIn(BaseModel):
    user_id: str | None = None  # 用于生成完成后的 Web Push 通知；不传则不推


@router.post("/{wish_id}/plan")
def wish_plan(wish_id: str, background_tasks: BackgroundTasks, body: PlanIn | None = None):
    """方案：有缓存直接返回；没缓存转后台异步生成，完成后推送通知（前端轮询兜底）。"""
    cached = svc.get_cached_plan(wish_id)
    if cached is not None:
        return cached
    background_tasks.add_task(svc.generate_plan_and_notify, wish_id, body.user_id if body else None)
    return {"status": "generating"}
