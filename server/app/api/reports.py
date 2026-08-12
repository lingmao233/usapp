from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services import reports as svc

router = APIRouter(prefix="/api/reports", tags=["reports"])


class GenerateIn(BaseModel):
    circle_id: str
    week_start: str | None = None
    week_end: str | None = None


@router.get("")
def list_reports(circle_id: str, background_tasks: BackgroundTasks):
    result = svc.list_reports(circle_id)
    if result["generating"] == "trigger":
        # 懒触发：本周无报告则生成；已有报告但有新公开内容则滚动刷新（force 覆盖旧版）
        week = result["current_week"]
        background_tasks.add_task(
            svc.generate_report, circle_id, week["week_start"], week["week_end"], force=True
        )
        result["generating"] = True
    return result


@router.post("/generate")
def generate_report(body: GenerateIn):
    # 手动触发 = 强制刷新本周报告
    return svc.generate_report(body.circle_id, body.week_start, body.week_end, force=True)


@router.get("/{report_id}")
def get_report(report_id: str):
    return svc.get_report(report_id)
