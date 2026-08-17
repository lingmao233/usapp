from fastapi import APIRouter
from pydantic import BaseModel

from ..services import circles as svc

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class SetCodeIn(BaseModel):
    code: str


@router.get("/{account_id}")
def get_account(account_id: str):
    return svc.get_account(account_id)


@router.post("/{account_id}/recovery_code/reset")
def reset_recovery_code(account_id: str):
    return svc.reset_recovery_code(account_id)


@router.put("/{account_id}/recovery_code")
def set_recovery_code(account_id: str, body: SetCodeIn):
    return svc.set_recovery_code(account_id, body.code)


@router.get("/{account_id}/circles")
def list_account_circles(account_id: str):
    return svc.list_account_circles(account_id)
