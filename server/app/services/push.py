"""Web Push（第 5 期）：VAPID 密钥管理、订阅存取、互动通知发送。

- VAPID 密钥对首次使用时生成，落与数据库同目录的 vapid.json（server/data/，已 gitignore，随 volume 持久化）
- 发送走统一任务层（tasks.run_task）记录成功/失败；endpoint 失效（404/410）时删除该订阅
- 推送只发给碎片作者；作者本人操作不推（隐私碎片本就不可互动，服务端早已 403，无需分支）
"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from py_vapid import Vapid, b64urlencode
from pywebpush import WebPushException, webpush

from ..config import settings
from ..db.database import get_conn
from .tasks import run_task

logger = logging.getLogger("us.push")

# VAPID claims 的 sub：规范要求 mailto: 或 https: 联系方式，可用环境变量覆盖
VAPID_SUB = settings.VAPID_SUB

_vapid: Vapid | None = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _vapid_path() -> Path:
    """密钥文件放数据库同目录（server/data/），本地与 docker volume 都天然持久化。"""
    return Path(settings.DB_PATH).parent / "vapid.json"


def _load_vapid() -> Vapid:
    """读密钥文件；不存在则生成一对并落盘（0600，仅属主可读）。"""
    global _vapid
    if _vapid is not None:
        return _vapid
    path = _vapid_path()
    if path.exists():
        vapid = Vapid.from_pem(json.loads(path.read_text())["private_pem"].encode())
    else:
        vapid = Vapid()
        vapid.generate_keys()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"private_pem": vapid.private_pem().decode()}))
        path.chmod(0o600)
        logger.info("已生成 VAPID 密钥对：%s", path)
    _vapid = vapid
    return vapid


def public_key() -> str:
    """base64url 编码的 VAPID 公钥（65 字节未压缩点），前端 subscribe 的 applicationServerKey。"""
    vapid = _load_vapid()
    raw = vapid.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return b64urlencode(raw)


# ---------- 订阅存取 ----------

def subscribe(user_id: str, endpoint: str, keys: dict) -> None:
    """存订阅；endpoint 唯一，重复订阅（或换账号）就地更新，不插重复行。"""
    conn = get_conn()
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    row = conn.execute(
        "SELECT id FROM push_subscriptions WHERE endpoint = ?", (endpoint,)
    ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE push_subscriptions SET user_id = ?, keys_json = ? WHERE id = ?",
            (user_id, json.dumps(keys), row["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO push_subscriptions (id, user_id, endpoint, keys_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex[:12], user_id, endpoint, json.dumps(keys), _now()),
        )
    conn.commit()


def unsubscribe(endpoint: str) -> None:
    """退订：按 endpoint 删行，本就不存在也返回成功（幂等）。"""
    conn = get_conn()
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
    conn.commit()


# ---------- 发送 ----------

def _send_to_user(user_id: str, payload: dict) -> None:
    """给某用户的全部订阅发推送。404/410 说明订阅已失效，删行；其他失败上抛给任务层记 failed。"""
    conn = get_conn()
    subs = conn.execute(
        "SELECT * FROM push_subscriptions WHERE user_id = ?", (user_id,)
    ).fetchall()
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": json.loads(sub["keys_json"])},
                data=json.dumps(payload, ensure_ascii=False),
                vapid_private_key=_load_vapid(),
                vapid_claims={"sub": VAPID_SUB},
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub["id"],))
                conn.commit()
                logger.info("push 订阅已失效（HTTP %s），已删除", status)
            else:
                raise


def _nickname(user_id: str) -> str:
    row = get_conn().execute("SELECT nickname FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["nickname"] if row else "有人"


def notify_comment(fragment_id: str, actor_id: str, content: str) -> None:
    """新评论 → 推给碎片作者；作者自评不推。"""
    frag = get_conn().execute(
        "SELECT user_id FROM fragments WHERE id = ?", (fragment_id,)
    ).fetchone()
    if frag is None or frag["user_id"] == actor_id:
        return
    snippet = content.strip()[:30]
    payload = {
        "title": "我们",
        "body": f"{_nickname(actor_id)} 评论了你：{snippet}",
        "url": "/wall",
    }
    run_task("push_comment", fragment_id, lambda: _send_to_user(frag["user_id"], payload))


def notify_like(fragment_id: str, actor_id: str) -> None:
    """新点赞 → 推给碎片作者；作者自赞不推（取消赞在 API 层就不会调进来）。"""
    frag = get_conn().execute(
        "SELECT user_id FROM fragments WHERE id = ?", (fragment_id,)
    ).fetchone()
    if frag is None or frag["user_id"] == actor_id:
        return
    payload = {
        "title": "我们",
        "body": f"{_nickname(actor_id)} 赞了你的碎片",
        "url": "/wall",
    }
    run_task("push_like", fragment_id, lambda: _send_to_user(frag["user_id"], payload))
