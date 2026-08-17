from fastapi import APIRouter
from pydantic import BaseModel

from ..services import selfshare as svc

router = APIRouter(prefix="/api/self", tags=["self"])


@router.get("/sharing")
def list_sharing(account_id: str):
    """我的共享开关列表（全部圈子 × 类别）。"""
    return svc.list_sharing(account_id)


class SharingIn(BaseModel):
    account_id: str
    circle_id: str
    category: str  # goal/plan/ledger/calorie
    level: str | None = None  # 仅 goal/plan：progress/detail，缺省 progress


@router.put("/sharing")
def upsert_sharing(body: SharingIn):
    """开/调共享（UPSERT）：goal/plan 带档位；ledger/calorie 只有开关。"""
    return svc.upsert_sharing(body.account_id, body.circle_id, body.category, body.level)


@router.delete("/sharing")
def delete_sharing(account_id: str, circle_id: str, category: str):
    """关闭共享（删行，幂等）。"""
    return svc.delete_sharing(account_id, circle_id, category)
