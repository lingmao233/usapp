from fastapi import APIRouter
from pydantic import BaseModel

from ..services import knowledge as svc

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class SearchIn(BaseModel):
    query: str
    circle_id: str
    top_k: int = 5


@router.get("")
def list_items(circle_id: str, tag: str | None = None, limit: int = 50):
    return svc.list_items(circle_id, tag, limit)


@router.post("/search")
def search(body: SearchIn):
    return svc.search(body.query, body.circle_id, body.top_k)
