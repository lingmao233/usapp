from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services import ledger as svc

router = APIRouter(prefix="/api/ledger", tags=["ledger"])
calories_router = APIRouter(prefix="/api/calories", tags=["calories"])


class RecognizeIn(BaseModel):
    account_id: str
    image_url: str


@router.post("/recognize")
def recognize_expenses(body: RecognizeIn):
    """小票/支付截图识别 → 一图多笔 pending 条目集；未配视觉模型 400。"""
    return svc.recognize_expenses(body.account_id, body.image_url)


class ExpenseIn(BaseModel):
    """手动 / 确认入账：带 id = 确认待入账条目（可同时改字段）；不带 = 手动新录。"""

    account_id: str
    id: str | None = None
    amount_fen: int | None = None
    category: str | None = None
    merchant: str | None = None
    note: str | None = None
    spent_at: str | None = None


@router.post("/expenses")
def add_or_confirm_expense(body: ExpenseIn):
    if body.id:
        return svc.confirm_expense(
            body.id, body.account_id, body.amount_fen, body.category, body.merchant, body.note, body.spent_at
        )
    return svc.add_expense(
        body.account_id, body.amount_fen, body.category or "其他",
        body.merchant or "", body.note or "", body.spent_at,
    )


class ExpenseEditIn(BaseModel):
    account_id: str
    amount_fen: int | None = None
    category: str | None = None
    merchant: str | None = None
    note: str | None = None
    spent_at: str | None = None


@router.put("/expenses/{expense_id}")
def update_expense(expense_id: str, body: ExpenseEditIn):
    return svc.update_expense(
        expense_id, body.account_id, body.amount_fen, body.category, body.merchant, body.note, body.spent_at
    )


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: str, account_id: str):
    return svc.delete_expense(expense_id, account_id)


@router.get("/expenses")
def list_expenses(account_id: str, month: str | None = None):
    return svc.list_expenses(account_id, month)


class CalorieRecognizeIn(BaseModel):
    account_id: str
    image_url: str
    hint: str = ""  # 拍照时补的一句描述（"红烧肉一碗约 300g"），可空


@calories_router.post("/recognize")
def recognize_calorie(body: CalorieRecognizeIn, background_tasks: BackgroundTasks):
    """食物照片识别 → pending 条目（含运动等效）；未配视觉模型 400。
    查表未命中的菜名在响应后后台联网入库（不堵识别，与用户是否入账解耦）。"""
    result = svc.recognize_calorie(body.account_id, body.image_url, body.hint)
    missed = result.pop("missed", None) or []
    if missed:
        background_tasks.add_task(svc.backfill_food_web, missed)
    return result


class CalorieIn(BaseModel):
    """确认（带 id，数字可改）/ 手动录入（不带 id）；确认都会触发超预算联动。
    items 为手动录入的可选结构化明细（食物名+克数+热量，查表命中后由前端带上）。"""

    account_id: str
    id: str | None = None
    total_kcal: float | None = None
    note: str | None = None
    items: list[dict] | None = None


@calories_router.post("")
def add_or_confirm_calorie(body: CalorieIn):
    if body.id:
        return svc.confirm_calorie(body.id, body.account_id, body.total_kcal, body.note)
    return svc.add_calorie(body.account_id, body.total_kcal, body.note or "", body.items)


@calories_router.get("/lookup")
def lookup_food(account_id: str, name: str, grams: float | None = None):
    """手动录入即时匹配：查正式表/共建库返回每 100g 热量；给了克数顺带算好总热量。"""
    return svc.lookup_food(account_id, name, grams)


class CalorieItemIn(BaseModel):
    """改某菜品：index 为 items 下标；grams / name 至少给一个。
    改名后服务端重走匹配链（查表→联网→保留估值）；kcal 按 kcal_per_100g 重算。"""

    account_id: str
    index: int
    grams: float | None = None
    name: str | None = None


@calories_router.put("/{entry_id}/items")
def update_calorie_item(entry_id: str, body: CalorieItemIn):
    """改克数/改名（pending/已入账都可改）：返回更新后的整条记录与超预算联动结果。"""
    return svc.update_calorie_item(entry_id, body.account_id, body.index, body.grams, body.name)


@calories_router.delete("/{entry_id}")
def delete_calorie(entry_id: str, account_id: str):
    """删除一条热量记录（仅本人）。"""
    return svc.delete_calorie(entry_id, account_id)


@calories_router.get("/corrections")
def list_corrections(account_id: str):
    """我的识别纠正记录（名字/克数），纠正错了可删。"""
    return svc.list_corrections(account_id)


@calories_router.delete("/corrections/{kind}/{correction_id}")
def delete_correction(kind: str, correction_id: str, account_id: str):
    """删除一条识别纠正（kind=name/gram，仅本人）。"""
    return svc.delete_correction(account_id, kind, correction_id)


@calories_router.get("")
def list_calories(account_id: str, date: str | None = None):
    return svc.list_calories(account_id, date)
