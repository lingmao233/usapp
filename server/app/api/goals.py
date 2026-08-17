from fastapi import APIRouter
from pydantic import BaseModel

from ..services import goals as svc
from ..services import nudges as nudges_svc

router = APIRouter(prefix="/api/goals", tags=["goals"])
blocks_router = APIRouter(prefix="/api/nudge-blocks", tags=["nudges"])


class CreateGoalIn(BaseModel):
    account_id: str
    type: str
    title: str
    params: dict = {}
    answers: dict = {}


@router.post("")
def create_goal(body: CreateGoalIn):
    """建目标（账号级）；共享走 /api/self/sharing（类别 × 圈子开关）。"""
    return svc.create_goal(body.account_id, body.type, body.title, body.params, body.answers)


@router.get("")
def list_goals(account_id: str):
    return svc.list_goals(account_id)


@router.get("/circle/{circle_id}")
def circle_goals(circle_id: str, account_id: str):
    """本圈内共享出来的目标列表（按 self_sharing 档位裁剪）。"""
    return svc.list_circle_goals(circle_id, account_id)


@router.get("/{goal_id}")
def get_goal(goal_id: str, account_id: str | None = None):
    """目标详情：服务端按 viewer 过滤（owner 全量 / 圈友按档位 / 其余 404）。"""
    return svc.get_goal(goal_id, account_id)


class NudgeToggleIn(BaseModel):
    account_id: str
    enabled: bool


@router.post("/{goal_id}/nudge-toggle")
def nudge_toggle(goal_id: str, body: NudgeToggleIn):
    return svc.set_nudge_enabled(goal_id, body.account_id, body.enabled)


class SendNudgeIn(BaseModel):
    account_id: str  # 鞭策发起者（圈友账号）
    message: str = ""


@router.post("/{goal_id}/nudges")
def send_nudge(goal_id: str, body: SendNudgeIn):
    return nudges_svc.send_nudge(goal_id, body.account_id, body.message)


@router.get("/{goal_id}/nudges")
def list_nudges(goal_id: str, account_id: str):
    return nudges_svc.list_nudges(goal_id, account_id)


class BlockIn(BaseModel):
    account_id: str
    blocked_account_id: str


@blocks_router.post("")
def block_user(body: BlockIn):
    return svc.block_user(body.account_id, body.blocked_account_id)
