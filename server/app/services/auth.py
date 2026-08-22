"""账号认证：用户名全局唯一 + 可选密码（pbkdf2_hmac，零新依赖）。

- 密码可空：NULL/空串 = 无密码账号，登录只校验用户名
- recovery_code 语义 = 找回凭证：注册成功强制展示一次；忘密码时凭它走 reset 重设
- 个人恢复码登录体系已作废（claim/recover-lookup 接口下线）
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime

from fastapi import HTTPException

from ..db.database import generate_recovery_code, get_conn

PBKDF2_ITERATIONS = 120_000
USERNAME_MAX = 32


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return f"pbkdf2${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), expected)
    except (ValueError, AttributeError):
        return False


def _find_by_username(conn, username: str):
    """用户名查找：先精确，再按 ASCII 大小写折叠（与唯一性校验同口径）。"""
    row = conn.execute(
        "SELECT * FROM accounts WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM accounts WHERE UPPER(username) = ?", (username.upper(),)
        ).fetchone()
    return row


def _check_username(username: str) -> str:
    name = (username or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="账号名不能为空")
    if len(name) > USERNAME_MAX:
        raise HTTPException(status_code=400, detail=f"账号名最长 {USERNAME_MAX} 个字符")
    return name


def _session_dict(account) -> dict:
    """登录/注册成功返回的会话结构（recovery_code 只在注册/找回时附带，用于强制展示）。
    device_token：设备令牌（services/tokens.py），前端存 localStorage 每次请求带 Bearer 头。"""
    from .. import tokens

    return {
        "account_id": account["id"],
        "username": account["username"],
        "nickname": account["nickname"],
        "has_password": bool(account["password_hash"]),
        "device_token": tokens.issue_token(account["id"]),
    }


def register(username: str, password: str | None = None, nickname: str | None = None) -> dict:
    """注册：账号名唯一（ASCII 大小写不敏感）；密码可选；返回找回凭证供前端强制展示。"""
    conn = get_conn()
    name = _check_username(username)
    if conn.execute(
        "SELECT 1 FROM accounts WHERE username = ? OR UPPER(username) = ?",
        (name, name.upper()),
    ).fetchone():
        raise HTTPException(status_code=409, detail="这个账号名已被注册，换一个")
    account_id = uuid.uuid4().hex[:12]
    recovery_code = generate_recovery_code()
    pwd = (password or "").strip()
    conn.execute(
        """INSERT INTO accounts (id, nickname, username, password_hash, created_at, recovery_code)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            account_id,
            (nickname or "").strip() or name,
            name,
            _hash_password(pwd) if pwd else None,
            _now(),
            recovery_code,
        ),
    )
    conn.commit()
    account = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return {**_session_dict(account), "recovery_code": recovery_code}


def login(username: str, password: str | None = None) -> dict:
    """登录：无密码账号只校验账号名；有密码账号必须带密码且校验通过。"""
    conn = get_conn()
    name = _check_username(username)
    account = _find_by_username(conn, name)
    if account is None:
        raise HTTPException(status_code=404, detail="没找到这个账号名，检查一下有没有输错")
    if account["password_hash"]:
        if not (password or ""):
            raise HTTPException(status_code=403, detail="请输入密码")
        if not _verify_password(password, account["password_hash"]):
            raise HTTPException(status_code=403, detail="密码不对")
    return _session_dict(account)


def reset(
    username: str,
    recovery_code: str,
    new_password: str | None = None,
    new_recovery_code: str | None = None,
) -> dict:
    """找回：账号名 + 找回凭证核验 → 重设密码（可空=清空成无密码账号），可顺便自设新找回凭证。

    凭证匹配沿用旧 claim 口径：先精确（自定义码区分大小写），再 ASCII 大小写折叠。
    成功后返回当前找回凭证（自设过则为新值），前端应再次强制展示。
    """
    conn = get_conn()
    name = _check_username(username)
    account = _find_by_username(conn, name)
    if account is None:
        raise HTTPException(status_code=404, detail="没找到这个账号名，检查一下有没有输错")
    code = (recovery_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="填一下找回凭证")
    stored = account["recovery_code"] or ""
    if stored != code and stored.upper() != code.upper():
        raise HTTPException(status_code=403, detail="找回凭证不对")

    if new_password is not None:
        pwd = new_password.strip()
        conn.execute(
            "UPDATE accounts SET password_hash = ? WHERE id = ?",
            (_hash_password(pwd) if pwd else None, account["id"]),
        )
    current_code = account["recovery_code"]
    if new_recovery_code is not None:
        normalized = new_recovery_code.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="找回凭证不能为空")
        if len(normalized) > 64:
            raise HTTPException(status_code=400, detail="找回凭证最长 64 个字符")
        exists = conn.execute(
            "SELECT id FROM accounts WHERE (recovery_code = ? OR UPPER(recovery_code) = ?) AND id != ?",
            (normalized, normalized.upper(), account["id"]),
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="这个找回凭证已经被人用了，换一个")
        conn.execute(
            "UPDATE accounts SET recovery_code = ? WHERE id = ?",
            (normalized, account["id"]),
        )
        current_code = normalized
    conn.commit()
    account = conn.execute(
        "SELECT * FROM accounts WHERE id = ?", (account["id"],)
    ).fetchone()
    return {**_session_dict(account), "recovery_code": current_code}
