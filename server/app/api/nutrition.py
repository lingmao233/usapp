"""营养共建：用户手动添加食物进 staging 预数据库，后台异步联网核验；staging 管理面板（治理错值）。"""
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services import events, nutrition as svc

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


# ---------- staging 管理面板（共建库治理：查/改/删，所有账号可用，操作留痕） ----------


class StagingEditIn(BaseModel):
    """改 staging 行：传什么改什么（至少一项）；verified 可单独切换。"""

    account_id: str
    name: str | None = None
    brand: str | None = None
    kcal_per_100g: float | None = None
    protein_per_100g: float | None = None
    fat_per_100g: float | None = None
    cho_per_100g: float | None = None
    verified: bool | None = None


@router.get("/staging")
def list_staging(account_id: str, query: str = "", page: int = 1, page_size: int = 50,
                 include_deleted: bool = False):
    """staging 列表（搜索/分页；include_deleted=true 连软删行一起看，审计用）。"""
    return svc.list_staging(query, page, page_size, include_deleted)


@router.patch("/staging/{staging_id}")
def update_staging(staging_id: int, body: StagingEditIn):
    """改 staging 行（软删行改完自动复活）；改动广播给在线页面刷新。"""
    result = svc.update_staging_food(
        body.account_id, staging_id, body.name, body.brand, body.kcal_per_100g,
        body.protein_per_100g, body.fat_per_100g, body.cho_per_100g, body.verified,
    )
    events.publish_all({"type": "staging_updated", "name": result["food"]["name"]})
    return result


@router.delete("/staging/{staging_id}")
def delete_staging(staging_id: int, account_id: str):
    """软删 staging 行（匹配/回填/认可即刻跳过）；广播给在线页面刷新。"""
    result = svc.soft_delete_staging(account_id, staging_id)
    events.publish_all({"type": "staging_updated"})
    return result
