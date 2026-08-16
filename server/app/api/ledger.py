from fastapi import APIRouter
from pydantic import BaseModel

from ..services import ledger as svc

router = APIRouter(prefix="/api/ledger", tags=["ledger"])
calories_router = APIRouter(prefix="/api/calories", tags=["calories"])


class RecognizeIn(BaseModel):
    user_id: str
    image_url: str


@router.post("/recognize")
def recognize_expenses(body: RecognizeIn):
    """小票/支付截图识别 → 一图多笔 pending 条目集；未配视觉模型 400。"""
    return svc.recognize_expenses(body.user_id, body.image_url)


class ExpenseIn(BaseModel):
    """手动 / 确认入账：带 id = 确认待入账条目（可同时改字段）；不带 = 手动新录。"""

    user_id: str
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
            body.id, body.user_id, body.amount_fen, body.category, body.merchant, body.note, body.spent_at
        )
    return svc.add_expense(
        body.user_id, body.amount_fen, body.category or "其他",
        body.merchant or "", body.note or "", body.spent_at,
    )


class ExpenseEditIn(BaseModel):
    user_id: str
    amount_fen: int | None = None
    category: str | None = None
    merchant: str | None = None
    note: str | None = None
    spent_at: str | None = None


@router.put("/expenses/{expense_id}")
def update_expense(expense_id: str, body: ExpenseEditIn):
    return svc.update_expense(
        expense_id, body.user_id, body.amount_fen, body.category, body.merchant, body.note, body.spent_at
    )


@router.delete("/expenses/{expense_id}")
def delete_expense(expense_id: str, user_id: str):
    return svc.delete_expense(expense_id, user_id)


@router.get("/expenses")
def list_expenses(user_id: str, month: str | None = None):
    return svc.list_expenses(user_id, month)


class CalorieRecognizeIn(BaseModel):
    user_id: str
    image_url: str
    hint: str = ""  # 拍照时补的一句描述（"红烧肉一碗约 300g"），可空


@calories_router.post("/recognize")
def recognize_calorie(body: CalorieRecognizeIn):
    """食物照片识别 → pending 条目（含运动等效）；未配视觉模型 400。"""
    return svc.recognize_calorie(body.user_id, body.image_url, body.hint)


class CalorieIn(BaseModel):
    """确认（带 id，数字可改）/ 手动录入（不带 id）；确认都会触发超预算联动。"""

    user_id: str
    id: str | None = None
    total_kcal: float | None = None
    note: str | None = None


@calories_router.post("")
def add_or_confirm_calorie(body: CalorieIn):
    if body.id:
        return svc.confirm_calorie(body.id, body.user_id, body.total_kcal, body.note)
    return svc.add_calorie(body.user_id, body.total_kcal, body.note or "")


@calories_router.get("")
def list_calories(user_id: str, date: str | None = None):
    return svc.list_calories(user_id, date)
