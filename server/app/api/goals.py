from fastapi import APIRouter
from pydantic import BaseModel

from ..services import goals as svc
from ..services import nudges as nudges_svc

router = APIRouter(prefix="/api/goals", tags=["goals"])
blocks_router = APIRouter(prefix="/api/nudge-blocks", tags=["nudges"])


class CreateGoalIn(BaseModel):
    user_id: str
    type: str
    title: str
    params: dict = {}
    answers: dict = {}
    visible_circle_ids: list[str] = []
    detail_level: str = "summary"


@router.post("")
def create_goal(body: CreateGoalIn):
    return svc.create_goal(
        body.user_id, body.type, body.title, body.params, body.answers,
        body.visible_circle_ids, body.detail_level,
    )


@router.get("")
def list_goals(user_id: str):
    return svc.list_goals(user_id)


@router.get("/circle/{circle_id}")
def circle_goals(circle_id: str, viewer_id: str):
    """Wall「伙伴目标」：本圈内公开的目标列表（summary 粒度）。"""
    return svc.list_circle_goals(circle_id, viewer_id)


@router.get("/{goal_id}")
def get_goal(goal_id: str, viewer_id: str | None = None):
    """目标详情：服务端按 viewer 过滤（owner 全量 / 圈友按粒度 / 其余 404）。"""
    return svc.get_goal(goal_id, viewer_id)


class SharingIn(BaseModel):
    user_id: str
    visible_circle_ids: list[str] = []
    detail_level: str = "summary"


@router.put("/{goal_id}/sharing")
def update_sharing(goal_id: str, body: SharingIn):
    return svc.update_sharing(goal_id, body.user_id, body.visible_circle_ids, body.detail_level)


class NudgeToggleIn(BaseModel):
    user_id: str
    enabled: bool


@router.post("/{goal_id}/nudge-toggle")
def nudge_toggle(goal_id: str, body: NudgeToggleIn):
    return svc.set_nudge_enabled(goal_id, body.user_id, body.enabled)


class SendNudgeIn(BaseModel):
    user_id: str  # 鞭策发起者（圈友）
    message: str = ""


@router.post("/{goal_id}/nudges")
def send_nudge(goal_id: str, body: SendNudgeIn):
    return nudges_svc.send_nudge(goal_id, body.user_id, body.message)


@router.get("/{goal_id}/nudges")
def list_nudges(goal_id: str, user_id: str):
    return nudges_svc.list_nudges(goal_id, user_id)


class BlockIn(BaseModel):
    user_id: str
    blocked_user_id: str


@blocks_router.post("")
def block_user(body: BlockIn):
    return svc.block_user(body.user_id, body.blocked_user_id)
