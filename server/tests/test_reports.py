"""周报滚动刷新测试：已有本周报告但之后有新公开内容时，懒触发应重生成覆盖旧版。

运行：cd server && .venv-mac/bin/python -m pytest tests/test_reports.py -v
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

# 独立测试数据库 + 强制 mock 模式（覆盖 .env 里可能存在的 key），必须在 import app 之前设置
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="us_test_reports_"), "test.db")
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DOUBAO_API_KEY"] = ""
SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVER_DIR)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import reports as svc  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _make_circle_with_fragment(client: TestClient):
    """建圈 + 一名成员发一条公开碎片，返回 (circle_id, user_id)。"""
    circle = client.post("/api/circles", json={"name": "周报刷新测试圈"}).json()
    u1 = client.post(
        "/api/circles/join", json={"invite_code": circle["invite_code"], "nickname": "阿澈"}
    ).json()
    r = client.post(
        "/api/fragments",
        json={"circle_id": circle["id"], "user_id": u1["user_id"], "content": "今天去爬山了"},
    )
    assert r.status_code == 200, r.text
    return circle["id"], u1["user_id"]


def test_weekly_report_rolling_refresh(client: TestClient):
    cid, uid = _make_circle_with_fragment(client)
    ws, we = svc.current_week_range()

    # 本周无报告 → 懒触发
    assert svc.list_reports(cid)["generating"] == "trigger"
    first = svc.generate_report(cid, ws, we)
    assert first["status"] == "generated"
    first_id = first["report_id"]

    # 没有新内容：不再触发；无 force 不覆盖
    assert svc.list_reports(cid)["generating"] is False
    assert svc.generate_report(cid, ws, we)["status"] == "exists"

    # 有新公开碎片（created_at 推进一小时，避开秒级精度并列）
    r = client.post(
        "/api/fragments",
        json={"circle_id": cid, "user_id": uid, "content": "晚上又想去海边"},
    )
    assert r.status_code == 200, r.text
    later = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
    conn = _db()
    conn.execute(
        "UPDATE fragments SET created_at = ? WHERE circle_id = ? AND content = ?",
        (later, cid, "晚上又想去海边"),
    )
    conn.commit()

    # 过期检测：懒触发应再次触发
    assert svc.list_reports(cid)["generating"] == "trigger"

    # force 重生成：旧版被覆盖，同一周只有一份新报告
    second = svc.generate_report(cid, ws, we, force=True)
    assert second["status"] == "generated"
    assert second["report_id"] != first_id
    rows = conn.execute(
        "SELECT id FROM reports WHERE circle_id = ? AND week_start = ?", (cid, ws)
    ).fetchall()
    assert [r["id"] for r in rows] == [second["report_id"]]
    conn.close()


def test_manual_generate_force_refresh(client: TestClient):
    """手动 POST /generate = 强制刷新：即使没有新内容也重新生成。"""
    cid, _uid = _make_circle_with_fragment(client)
    first = client.post("/api/reports/generate", json={"circle_id": cid}).json()
    assert first["status"] == "generated"
    second = client.post("/api/reports/generate", json={"circle_id": cid}).json()
    assert second["status"] == "generated"
    assert second["report_id"] != first["report_id"]
