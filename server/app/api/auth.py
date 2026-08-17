from fastapi import APIRouter
from pydantic import BaseModel

from ..services import auth as svc

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    username: str
    password: str | None = None  # 可空：不填=无密码账号
    nickname: str | None = None  # 可空：默认沿用账号名


class LoginIn(BaseModel):
    username: str
    password: str | None = None


class ResetIn(BaseModel):
    username: str
    recovery_code: str
    new_password: str | None = None        # None=不改密码；空串=清空成无密码账号
    new_recovery_code: str | None = None   # 可顺便自设新找回凭证


@router.post("/register")
def register(body: RegisterIn):
    """注册：账号名唯一；返回找回凭证，前端必须强制展示一次。"""
    return svc.register(body.username, body.password, body.nickname)


@router.post("/login")
def login(body: LoginIn):
    """登录：无密码账号只校验账号名；有密码账号校验密码。"""
    return svc.login(body.username, body.password)


@router.post("/reset")
def reset(body: ResetIn):
    """找回：账号名 + 找回凭证 → 重设密码（可顺便自设新凭证）。"""
    return svc.reset(body.username, body.recovery_code, body.new_password, body.new_recovery_code)
