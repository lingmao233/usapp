from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services import plans as svc

router = APIRouter(prefix="/api/plans", tags=["plans"])


@router.get("/today")
def today(user_id: str, background_tasks: BackgroundTasks):
    """今日清单：无 AI 条目且有 active 目标时懒触发生成（generating 语义照抄周报，前端轮询收敛）。"""
    result = svc.today(user_id)
    if result["generating"] == "trigger":
        background_tasks.add_task(svc.generate_today, user_id)
        result["generating"] = True
    return result


class AddItemIn(BaseModel):
    user_id: str
    content: str
    date: str | None = None  # YYYY-MM-DD，空=今天
    goal_id: str | None = None  # 空=自定义条目（无目标也能用）
    kind: str = "task"


@router.post("/items")
def add_item(body: AddItemIn):
    return svc.add_item(body.user_id, body.content, body.date, body.goal_id, body.kind)


class UpdateItemIn(BaseModel):
    user_id: str
    content: str | None = None
    done: bool | None = None


@router.put("/items/{item_id}")
def update_item(item_id: str, body: UpdateItemIn):
    return svc.update_item(item_id, body.user_id, body.content, body.done)


@router.delete("/items/{item_id}")
def delete_item(item_id: str, user_id: str):
    return svc.delete_item(item_id, user_id)
