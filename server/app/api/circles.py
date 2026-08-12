from fastapi import APIRouter
from pydantic import BaseModel

from ..services import circles as svc
from ..services import memory

router = APIRouter(prefix="/api/circles", tags=["circles"])


class CreateCircleIn(BaseModel):
    name: str
    account_id: str | None = None
    nickname: str | None = None
    persona_preset: str | None = None
    persona_custom: str | None = None


class JoinCircleIn(BaseModel):
    invite_code: str
    nickname: str | None = None
    account_id: str | None = None


class UpdatePersonaIn(BaseModel):
    user_id: str
    persona_preset: str = "observer"
    persona_custom: str = ""


@router.post("")
def create_circle(body: CreateCircleIn):
    return svc.create_circle(
        body.name, body.account_id, body.nickname, body.persona_preset, body.persona_custom
    )


@router.post("/join")
def join_circle(body: JoinCircleIn):
    return svc.join_circle(body.invite_code, body.nickname, body.account_id)


@router.put("/{circle_id}/persona")
def update_persona(circle_id: str, body: UpdatePersonaIn):
    """圈子人格：任何成员可换，圈与圈互不影响。"""
    return svc.update_persona(circle_id, body.user_id, body.persona_preset, body.persona_custom)


@router.get("/{circle_id}")
def get_circle(circle_id: str):
    return svc.get_circle(circle_id)


@router.get("/{circle_id}/members")
def list_members(circle_id: str):
    return {"members": svc.list_members(circle_id)}


@router.get("/{circle_id}/graph")
def pair_graph(circle_id: str, user_id: str):
    """观看者（user_id）视角的关系图：节点 + 按身份过滤后的边（设计文档 §5/§6）。"""
    svc.get_circle(circle_id)  # 圈子不存在 → 404
    return memory.build_pair_graph(circle_id, user_id)
