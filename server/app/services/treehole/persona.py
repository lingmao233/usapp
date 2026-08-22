"""树洞人设卡（酒馆式）：账号级一张，设立一次持久扮演。未设立时用默认倾听者人设。

与圈人格体系（resolve_persona）相互独立：圈人格管周报，人设卡管树洞。
"""
from datetime import datetime

from fastapi import HTTPException

from ...db.database import get_conn
from .. import selfshare

# 未设立时的默认人设（不落库，读取时合成）
DEFAULT_PERSONA = {
    "name": "树洞",
    "personality": "温和耐心的倾听者，不打断不评判",
    "speaking_style": "口语化、短句、像深夜里回消息的老朋友",
    "relationship": "最信得过的朋友",
    "background": "",
}

_FIELDS = ("name", "personality", "speaking_style", "relationship", "background", "custom_prompt")

# 思考程度档位（用户可调，随人设卡存储；空 = balanced 模型默认）：
# fast=关思考更快回 / balanced=模型默认 / deep=深思更细但更慢
THINKING_LEVELS = ("fast", "balanced", "deep")


def _norm_thinking(raw) -> str:
    level = str(raw or "").strip().lower()
    return level if level in THINKING_LEVELS else ""


def get_persona(account_id: str) -> dict:
    """读人设卡：未设立返回默认倾听者人设（default=True 标记，前端据此显示「去设立」）。"""
    conn = get_conn()
    selfshare.require_account(conn, account_id)
    row = conn.execute(
        "SELECT * FROM treehole_persona WHERE account_id = ?", (account_id,)
    ).fetchone()
    if row is None:
        return {**DEFAULT_PERSONA, "custom_prompt": "", "thinking": "balanced", "default": True}
    return {f: row[f] for f in _FIELDS} | {
        "thinking": row["thinking"] or "balanced", "default": False}


def put_persona(account_id: str, fields: dict) -> dict:
    """设立/覆盖人设卡（宽松字段：全部可空，超长截断，空名字回退「树洞」；
    custom_prompt 整段人设截断 4000，非空时生成优先于模板字段）。
    thinking 三档 fast/balanced/deep，非法值 400（白名单校验，不静默吞）。"""
    conn = get_conn()
    selfshare.require_account(conn, account_id)
    raw_thinking = str(fields.get("thinking") or "").strip()
    if raw_thinking and raw_thinking not in THINKING_LEVELS:
        raise HTTPException(status_code=400,
                            detail="思考程度只能是 fast / balanced / deep")
    values = {f: str(fields.get(f) or "").strip()[:200] for f in _FIELDS}
    values["custom_prompt"] = str(fields.get("custom_prompt") or "").strip()[:4000]
    if not any(values.values()) and not raw_thinking:
        raise HTTPException(status_code=400, detail="人设卡至少填一个字段")
    values["name"] = values["name"] or "树洞"
    thinking = raw_thinking or "balanced"
    conn.execute(
        """INSERT INTO treehole_persona (account_id, name, personality, speaking_style,
                                          relationship, background, custom_prompt, thinking, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET
             name=excluded.name, personality=excluded.personality,
             speaking_style=excluded.speaking_style, relationship=excluded.relationship,
             background=excluded.background, custom_prompt=excluded.custom_prompt,
             thinking=excluded.thinking, updated_at=excluded.updated_at""",
        (account_id, values["name"], values["personality"], values["speaking_style"],
         values["relationship"], values["background"], values["custom_prompt"], thinking,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return {**values, "thinking": thinking, "default": False}
