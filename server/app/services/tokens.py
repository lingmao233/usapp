"""设备令牌鉴权（优化清单第 4 项）：account 附属凭证，签名自校验，零新依赖。

背景：全项目 API 靠客户端明文携带 account_id 识别身份（拿到 id 就能读对方树洞隐私）。
登录体系存在但不发任何凭证；大量账号由圈子邀请码自动创建（无密码、没登录过）。

设计：device_token = {account_id}.{hmac_sha256(secret, account_id)[:24]}
- 服务端无状态（不落表）：secret 来自 DEVICE_SECRET 配置，缺省时**首次启动随机生成并
  持久化到 data/ 目录**（重启不失效，多进程共享同一文件）
- 签发点：注册 / 登录 / 建圈 / 入圈（这四个入口必然产生 account，全覆盖存量路径）
- 校验：verify_token(account_id, token) 恒时比较；token 不对即 401
- 过渡期（TOKEN_ENFORCE != "on"，默认）：缺 Authorization 头的请求放行但记 warning
  --存量已登录用户的 localStorage 里还没有 token，强制会把他们全锁外面；前端拿到
  token 后每次请求自动带上，验证覆盖后把 TOKEN_ENFORCE=on 收口
"""
import hashlib
import hmac
import logging
import secrets
from pathlib import Path

from ..config import settings
from ..db.database import get_conn

logger = logging.getLogger("us.auth.tokens")

_SECRET_CACHE: str | None = None


def _device_secret() -> str:
    """设备签名密钥：DEVICE_SECRET 优先；缺省时生成并持久化到 data/device_secret。"""
    global _SECRET_CACHE
    if _SECRET_CACHE:
        return _SECRET_CACHE
    if settings.DEVICE_SECRET:
        _SECRET_CACHE = settings.DEVICE_SECRET
        return _SECRET_CACHE
    path = Path(settings.DB_PATH).parent / "device_secret"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        _SECRET_CACHE = path.read_text(encoding="utf-8").strip()
    else:
        _SECRET_CACHE = secrets.token_hex(32)
        path.write_text(_SECRET_CACHE, encoding="utf-8")
        path.chmod(0o600)
    return _SECRET_CACHE


def issue_token(account_id: str) -> str:
    """为账号签发设备令牌（同一账号令牌恒定：无状态设计，不做过期/吊销）。
    恒定性的取舍：本项目单机自部署、无「登出所有设备」需求，换来的是零存储与
    服务重启不失效；未来需要吊销时改为落表加盐即可。"""
    sig = hmac.new(_device_secret().encode(), account_id.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{account_id}.{sig}"


def verify_token(account_id: str, token: str) -> bool:
    """校验设备令牌是否属于该账号（恒时比较）。"""
    if not account_id or not token:
        return False
    return hmac.compare_digest(issue_token(account_id), token)


def enforce_enabled() -> bool:
    """TOKEN_ENFORCE=on 时缺令牌直接 401；默认过渡期放行（前端带上后即可收紧）。"""
    return settings.TOKEN_ENFORCE.strip().lower() == "on"


def require_authorized(account_id: str, authorization: str | None) -> None:
    """API 侧统一校验入口：账号存在性 + 设备令牌。

    - Bearer token 正确 -> 放行
    - 带 token 但不对 -> 401（凭证错误绝不放行，与过渡期无关）
    - 未带 token -> 过渡期放行（warning 提示收紧），ENFORCE=on 时 401
    """
    conn = get_conn()
    row = conn.execute("SELECT id FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="账号不存在")
    header = (authorization or "").strip()
    if not header.startswith("Bearer "):
        if enforce_enabled():
            from fastapi import HTTPException
            raise HTTPException(status_code=401, detail="缺少访问凭证，请重新登录")
        logger.warning("账号 %s 的请求未带设备令牌（过渡期放行；TOKEN_ENFORCE=on 收紧）",
                       account_id)
        return
    if not verify_token(account_id, header[len("Bearer "):].strip()):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="访问凭证不对，请重新登录")
