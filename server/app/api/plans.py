from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services import nudges as nudges_svc
from ..services import plans as svc

router = APIRouter(prefix="/api/plans", tags=["plans"])


@router.get("/today")
def today(account_id: str, background_tasks: BackgroundTasks):
    """今日清单：无 AI 条目且有 active 目标时懒触发生成（generating 语义照抄周报，前端轮询收敛）。"""
    result = svc.today(account_id)
    if result["generating"] == "trigger":
        background_tasks.add_task(svc.generate_today, account_id)
        result["generating"] = True
    return result


class AddItemIn(BaseModel):
    account_id: str
    content: str
    date: str | None = None  # YYYY-MM-DD，空=今天
    goal_id: str | None = None  # 空=自定义条目（无目标也能用）
    kind: str = "task"


@router.post("/items")
def add_item(body: AddItemIn):
    return svc.add_item(body.account_id, body.content, body.date, body.goal_id, body.kind)


class UpdateItemIn(BaseModel):
    account_id: str
    content: str | None = None
    done: bool | None = None


@router.put("/items/{item_id}")
def update_item(item_id: str, body: UpdateItemIn):
    return svc.update_item(item_id, body.account_id, body.content, body.done)


@router.delete("/items/{item_id}")
def delete_item(item_id: str, account_id: str):
    return svc.delete_item(item_id, account_id)


class PlanNudgeIn(BaseModel):
    account_id: str  # 鞭策发起者（圈友账号）
    to_account_id: str
    circle_id: str
    message: str = ""


@router.post("/nudge")
def send_plan_nudge(body: PlanNudgeIn):
    """今日计划鞭策：校验链在 service 层（成员/共享/屏蔽/合并限频）。"""
    return nudges_svc.send_plan_nudge(body.account_id, body.to_account_id, body.circle_id, body.message)


@router.get("/nudges")
def list_plan_nudges(account_id: str, date: str | None = None):
    """我某天收到的计划鞭策留言（结构上只查本人收件箱，他人数据不可见）。"""
    return nudges_svc.list_plan_nudges(account_id, date)
