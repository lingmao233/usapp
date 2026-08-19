"""营养共建：用户手动添加食物进 staging 预数据库，后台异步联网核验。"""
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services import nutrition as svc

router = APIRouter(prefix="/api/nutrition", tags=["nutrition"])


class FoodIn(BaseModel):
    """手动加食物：名称 + 每 100g 热量必填，品牌与宏量营养素（g/100g）可空。"""

    account_id: str
    name: str
    kcal_per_100g: float
    brand: str | None = None
    protein_per_100g: float | None = None
    fat_per_100g: float | None = None
    cho_per_100g: float | None = None


@router.post("/foods")
def add_food(body: FoodIn, background: BackgroundTasks):
    """入 staging（source=user，verified=0）；核验异步进行：通过 verified=1，
    与联网值差 50% 以上保持 0（响应里 verified=false 即「待核实」）。"""
    result = svc.add_staging_food(
        body.account_id,
        body.name,
        body.kcal_per_100g,
        body.protein_per_100g,
        body.fat_per_100g,
        body.cho_per_100g,
        body.brand or "",
    )
    if result["created"] and not result["food"]["verified"]:
        background.add_task(svc.verify_staging_food, result["food"]["id"])
    return {
        **result,
        "message": "已收录进共建库，后台联网核验中；若与公开数据差异过大将保持「待核实」",
    }
