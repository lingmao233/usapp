"""最小统一任务层：包裹管线执行，失败重试 + 运行状态落 task_runs，消灭 AI 降级静默。

不引任何框架；同步函数，跑在调用方原有线程/BackgroundTasks 模型里。
"""
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime

from .. import ai
from ..db.database import get_conn

logger = logging.getLogger("us.tasks")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def run_task(task_name: str, entity_id: str, fn: Callable[[], None], retries: int = 2) -> str:
    """执行一个任务并落库状态，返回最终 status（success / degraded / failed）。

    - 失败按 retries 次数重试，指数退避（1s、2s…），最终失败记 failed + error
    - 成功但期间有真实 AI 调用回退 mock → degraded（见 ai.last_call_used_mock）
    - 异常不外抛，调用方凭返回值决定是否自行报错（保持各管线对外行为不变）
    """
    conn = get_conn()
    run_id = uuid.uuid4().hex[:12]
    conn.execute(
        "INSERT INTO task_runs (id, task_name, entity_id, status, error, started_at)"
        " VALUES (?, ?, ?, 'running', '', ?)",
        (run_id, task_name, entity_id, _now()),
    )
    conn.commit()

    status, error = "failed", ""
    for attempt in range(retries + 1):
        ai.reset_mock_signal()
        try:
            fn()
            status = "degraded" if ai.last_call_used_mock() else "success"
            break
        except Exception as exc:  # noqa: BLE001
            conn.rollback()  # 丢弃本次尝试的半截写入，保证重试幂等
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("任务 %s（%s）第 %d 次尝试失败：%s", task_name, entity_id, attempt + 1, error)
            if attempt < retries:
                time.sleep(2 ** attempt)

    conn.execute(
        "UPDATE task_runs SET status=?, error=?, finished_at=? WHERE id=?",
        (status, error if status == "failed" else "", _now(), run_id),
    )
    conn.commit()
    return status
