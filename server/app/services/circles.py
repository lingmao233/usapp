"""圈子、身份与成员关系：一个 account 可加入多个圈子。

- users 表不变，仍是"圈子内的成员记录"，碎片/知识/愿望继续按 user_id 工作
- accounts 是跨圈子的身份；memberships 记录 account ↔ circle ↔ user 的映射
"""
import random
import string
import uuid
from datetime import datetime

from fastapi import HTTPException

from ..db.database import generate_recovery_code, get_conn

MAX_MEMBERS = 14


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _invite_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=length))


def _create_account(nickname: str | None) -> str:
    account_id = uuid.uuid4().hex[:12]
    recovery_code = generate_recovery_code()
    get_conn().execute(
        "INSERT INTO accounts (id, nickname, created_at, recovery_code) VALUES (?, ?, ?, ?)",
        (account_id, (nickname or "").strip() or "新朋友", _now(), recovery_code),
    )
    return account_id


def _nickname_taken(circle_id: str, nickname: str) -> bool:
    """圈子内昵称唯一校验：trim + 大小写不敏感。"""
    target = nickname.strip().lower()
    rows = get_conn().execute(
        "SELECT nickname FROM users WHERE circle_id = ?", (circle_id,)
    ).fetchall()
    return any(r["nickname"].strip().lower() == target for r in rows)


def _get_account(account_id: str):
    return get_conn().execute(
        "SELECT * FROM accounts WHERE id = ?", (account_id,)
    ).fetchone()


def _create_membership(account_id: str, circle_id: str, user_id: str) -> None:
    get_conn().execute(
        "INSERT INTO memberships (id, account_id, circle_id, user_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (uuid.uuid4().hex[:12], account_id, circle_id, user_id, _now()),
    )


def _create_circle_user(circle_id: str, nickname: str) -> str:
    user_id = uuid.uuid4().hex[:12]
    get_conn().execute(
        "INSERT INTO users (id, nickname, avatar, created_at, circle_id) VALUES (?, ?, '', ?, ?)",
        (user_id, nickname.strip(), _now(), circle_id),
    )
    return user_id


def create_circle(
    name: str,
    account_id: str | None = None,
    nickname: str | None = None,
    persona_preset: str | None = None,
    persona_custom: str | None = None,
) -> dict:
    """建圈子：无 account_id 时先创建 account；nickname 留空沿用 account 昵称。
    人格可选，缺省观察员；解析优先级（自定义 > 预设）在周报生成时统一处理。"""
    conn = get_conn()
    account = None
    new_account = False
    if account_id:
        account = _get_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="身份不存在，请刷新后重试")
    if account is None:
        account_id = _create_account(nickname)
        account = _get_account(account_id)
        new_account = True

    final_nickname = (nickname or "").strip() or account["nickname"]
    final_preset = persona_preset or "observer"
    final_custom = (persona_custom or "").strip()
    circle_id = uuid.uuid4().hex[:12]
    code = _invite_code()
    conn.execute(
        """INSERT INTO circles (id, name, invite_code, created_at, persona_preset, persona_custom)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (circle_id, name, code, _now(), final_preset, final_custom),
    )
    user_id = _create_circle_user(circle_id, final_nickname)
    _create_membership(account_id, circle_id, user_id)
    conn.commit()
    from .. import tokens

    return {
        "id": circle_id,
        "name": name,
        "invite_code": code,
        "account_id": account_id,
        "user_id": user_id,
        "nickname": final_nickname,
        "persona_preset": final_preset,
        "persona_custom": final_custom,
        "recovery_code": account["recovery_code"] if new_account else None,
        "device_token": tokens.issue_token(account_id),
    }


def update_persona(
    circle_id: str, user_id: str, persona_preset: str, persona_custom: str
) -> dict:
    """换圈子人格：任何成员可改（成员校验同 fragments 写法）；整体覆盖两字段。"""
    conn = get_conn()
    if conn.execute(
        "SELECT 1 FROM circles WHERE id = ?", (circle_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="圈子不存在")
    if conn.execute(
        "SELECT 1 FROM users WHERE id = ? AND circle_id = ?", (user_id, circle_id)
    ).fetchone() is None:
        raise HTTPException(status_code=403, detail="你不是这个圈子的成员")
    conn.execute(
        "UPDATE circles SET persona_preset = ?, persona_custom = ? WHERE id = ?",
        (persona_preset, (persona_custom or "").strip(), circle_id),
    )
    conn.commit()
    return get_circle(circle_id)


def join_circle(invite_code: str, nickname: str | None, account_id: str | None = None) -> dict:
    """加入圈子：幂等——该 account 在此圈子已有 membership 时直接返回已有 user_id。"""
    conn = get_conn()
    circle = conn.execute(
        "SELECT * FROM circles WHERE invite_code = ?", (invite_code.strip().upper(),)
    ).fetchone()
    if circle is None:
        raise HTTPException(status_code=404, detail="邀请码不存在，检查一下有没有输错")

    account = None
    new_account = False
    if account_id:
        account = _get_account(account_id)
        if account is None:
            raise HTTPException(status_code=404, detail="身份不存在，请刷新后重试")
        # 幂等：已经在这个圈子里了（不受昵称唯一校验限制）
        existing = conn.execute(
            """SELECT m.user_id, u.nickname FROM memberships m
               JOIN users u ON u.id = m.user_id
               WHERE m.account_id = ? AND m.circle_id = ?""",
            (account_id, circle["id"]),
        ).fetchone()
        if existing:
            from .. import tokens

            return {
                "user_id": existing["user_id"],
                "nickname": existing["nickname"],
                "circle_id": circle["id"],
                "circle_name": circle["name"],
                "invite_code": circle["invite_code"],
                "account_id": account_id,
                "already_joined": True,
                "recovery_code": None,
                "device_token": tokens.issue_token(account_id),
            }
    else:
        # 先校验昵称再创建 account，避免 409 留下孤儿身份
        if nickname and nickname.strip() and _nickname_taken(circle["id"], nickname):
            raise HTTPException(status_code=409, detail="这个名字圈里已经有人在用了，换一个吧")
        account_id = _create_account(nickname)
        account = _get_account(account_id)
        new_account = True

    count = conn.execute(
        "SELECT COUNT(*) AS c FROM users WHERE circle_id = ?", (circle["id"],)
    ).fetchone()["c"]
    if count >= MAX_MEMBERS:
        raise HTTPException(status_code=400, detail=f"这个圈子满员啦（最多 {MAX_MEMBERS} 人）")

    final_nickname = (nickname or "").strip() or account["nickname"]
    if _nickname_taken(circle["id"], final_nickname):
        raise HTTPException(status_code=409, detail="这个名字圈里已经有人在用了，换一个吧")

    user_id = _create_circle_user(circle["id"], final_nickname)
    _create_membership(account_id, circle["id"], user_id)
    conn.commit()
    from .. import tokens

    return {
        "user_id": user_id,
        "nickname": final_nickname,
        "circle_id": circle["id"],
        "circle_name": circle["name"],
        "invite_code": circle["invite_code"],
        "account_id": account_id,
        "already_joined": False,
        "recovery_code": account["recovery_code"] if new_account else None,
        "device_token": tokens.issue_token(account_id),
    }


def get_circle(circle_id: str) -> dict:
    circle = get_conn().execute(
        "SELECT * FROM circles WHERE id = ?", (circle_id,)
    ).fetchone()
    if circle is None:
        raise HTTPException(status_code=404, detail="圈子不存在")
    return dict(circle)


def get_account(account_id: str) -> dict:
    """账号详情（含找回凭证，供"我的找回凭证"查看）。"""
    account = _get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return {
        "account_id": account["id"],
        "username": account["username"],
        "nickname": account["nickname"],
        "has_password": bool(account["password_hash"]),
        "recovery_code": account["recovery_code"],
    }


def reset_recovery_code(account_id: str) -> dict:
    """重置找回凭证：新随机 6 位码，旧凭证立即失效。"""
    if _get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    conn = get_conn()
    new_code = generate_recovery_code()
    conn.execute(
        "UPDATE accounts SET recovery_code = ? WHERE id = ?", (new_code, account_id)
    )
    conn.commit()
    return {"account_id": account_id, "recovery_code": new_code}


def set_recovery_code(account_id: str, code: str) -> dict:
    """自设找回凭证：不限字符与长度（汉字/字母/数字/符号均可），仅要求非空、≤64 字、全局唯一。"""
    if _get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    normalized = code.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="找回凭证不能为空")
    if len(normalized) > 64:
        raise HTTPException(status_code=400, detail="找回凭证最长 64 个字符")
    # 唯一性：精确匹配 + ASCII 大小写折叠双查，防与其他账号的凭证在 reset 端撞车
    exists = get_conn().execute(
        "SELECT id FROM accounts WHERE (recovery_code = ? OR UPPER(recovery_code) = ?) AND id != ?",
        (normalized, normalized.upper(), account_id),
    ).fetchone()
    if exists:
        raise HTTPException(status_code=409, detail="这个找回凭证已经被人用了，换一个")
    conn = get_conn()
    conn.execute(
        "UPDATE accounts SET recovery_code = ? WHERE id = ?", (normalized, account_id)
    )
    conn.commit()
    return {"account_id": account_id, "recovery_code": normalized}


def list_members(circle_id: str) -> list[dict]:
    rows = get_conn().execute(
        "SELECT id, nickname, avatar, created_at FROM users WHERE circle_id = ? ORDER BY created_at",
        (circle_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_account_circles(account_id: str) -> dict:
    """"我的圈子"列表：圈子名、邀请码、我在圈内的 user_id/昵称、成员数、碎片数、最近活跃、加入时间。"""
    conn = get_conn()
    if _get_account(account_id) is None:
        raise HTTPException(status_code=404, detail="身份不存在")
    account = _get_account(account_id)
    rows = conn.execute(
        """SELECT m.circle_id, m.user_id, m.created_at AS joined_at,
                  c.name AS circle_name, c.invite_code, u.nickname AS my_nickname,
                  (SELECT COUNT(*) FROM users x WHERE x.circle_id = c.id) AS member_count,
                  (SELECT COUNT(*) FROM fragments x WHERE x.circle_id = c.id) AS fragment_count,
                  (SELECT MAX(x.created_at) FROM fragments x WHERE x.circle_id = c.id) AS last_active
           FROM memberships m
           JOIN circles c ON c.id = m.circle_id
           JOIN users u ON u.id = m.user_id
           WHERE m.account_id = ?
           ORDER BY m.created_at DESC""",
        (account_id,),
    ).fetchall()
    return {
        "account_id": account_id,
        "account_nickname": account["nickname"],
        "circles": [dict(r) for r in rows],
    }
