"""树洞服务编排：API 侧入口。图跑一轮 → 整包响应；历史/清空直读 L0 与 checkpoint。"""
import base64
import re

from fastapi import HTTPException

from ... import ai
from ...config import settings
from ...db.database import get_conn
from .. import selfshare, tasks
from ..memory import layers
from . import graph as graph_mod
from . import streaming

# 图片消息只接受本服务上传产物（防任意文件读取）：/api/uploads/{32位hex}[_d].{ext}
_IMAGE_URL_RE = re.compile(r"^/api/uploads/([0-9a-f]{32}(_d)?\.(jpg|png|webp|gif))$")
_IMG_FMT = {".jpg": "jpeg", ".png": "png", ".webp": "webp", ".gif": "gif"}


def _require_account(account_id: str) -> None:
    selfshare.require_account(get_conn(), account_id)


def _load_image(image_url: str) -> tuple[bytes, str] | None:
    """读上传图片为 (字节, fmt)：优先 800px 识别副本（_s.jpg，更快），其次 1600px
    展示副本（_d.jpg），最后原图；URL 形状非法或文件不存在返回 None（调用方降级纯文本）。"""
    m = _IMAGE_URL_RE.match(image_url or "")
    if not m:
        return None
    path = settings.upload_dir / m.group(1)
    if not m.group(2):  # 原图：有副本优先用副本
        small = path.with_name(f"{path.stem}_s.jpg")
        display = path.with_name(f"{path.stem}_d.jpg")
        if small.is_file():
            path = small
        elif display.is_file():
            path = display
    if not path.is_file():
        return None
    return path.read_bytes(), _IMG_FMT[path.suffix]


def send_message(account_id: str, message: str, image_url: str | None = None,
                 on_delta=None) -> dict:
    """跑一轮树洞图，返回 {reply, citations, tools_used, intent, guardrail}（整包响应）。

    图片消息：先 caption（写进 L0 原文/L1 抽取，兼作降级文本），原图 data URL 随图进 generate，
    让模型亲眼看到图片；读图/caption 失败均降级为纯文本轮，不阻塞对话。
    on_delta(text) 给定时生成内容逐段回调（流式端点用；经线程本地槽传给 generate 节点，
    不进 LangGraph 状态——checkpoint 不可序列化函数）。
    全程经任务层落 task_runs（treehole_chat）：延迟（started/finished）、降级、失败可查；
    图跑挂了如实 502（不裸 500），任务层已把失败记档。
    """
    _require_account(account_id)
    message = (message or "").strip()[:2000]
    box: dict = {}

    def _run() -> None:
        nonlocal message
        image_b64 = ""
        stored_image_url = ""  # 只有真正读出来的图才落库/展示，坏 URL 不留破图
        if image_url:
            loaded = _load_image(image_url)
            if loaded:
                data, fmt = loaded
                image_b64 = f"data:image/{fmt};base64,{base64.b64encode(data).decode()}"
                stored_image_url = image_url
                caption = ai.treehole_image_caption(data, fmt, user_text=message)
                if caption:
                    message = f"{message}\n[图片：{caption}]" if message else f"[图片：{caption}]"
            if not message:
                message = "（发来一张图片）"
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")
        if on_delta:
            streaming.set_delta_cb(on_delta)
        try:
            final = graph_mod.get_graph().invoke(
                {"account_id": account_id, "message": message,
                 "image": image_b64, "image_url": stored_image_url},
                config={"configurable": {"thread_id": graph_mod.thread_id_of(account_id)}},
            )
        finally:
            streaming.clear()
        box["result"] = {
            "reply": final.get("reply") or "",
            "citations": final.get("citations") or [],
            "tools_used": final.get("tools_used") or [],
            "intent": final.get("intent") or "vent",
            "guardrail": bool(final.get("guardrail")),
        }

    status = tasks.run_task("treehole_chat", account_id, _run, retries=0)
    if status == "failed" or "result" not in box:
        raise HTTPException(status_code=502, detail="树洞这会儿走神了，稍等再试一次")
    return box["result"]


def history(account_id: str, limit: int | None = None,
            before_created: str | None = None) -> dict:
    """对话原文（L0 正序）；默认最近 200 条（分页：before_created 逐页往前翻，
    has_more 标记还有更早的）--替代原全量返回，长对话首屏不随历史线性变慢。
    citations/tools 已随行带出。"""
    _require_account(account_id)
    page_size = min(500, max(1, int(limit or 200)))
    items = layers.list_messages(account_id, limit=page_size + 1,
                                 before_created=before_created)
    has_more = len(items) > page_size
    if has_more:
        items = items[:page_size]
    return {"items": items, "has_more": has_more}


def clear_history(account_id: str) -> dict:
    """清空 L0 原文 + LangGraph 会话状态（滚动摘要随 checkpoint 一并消失）。

    L1/L2 记忆保留——清空的是对话，不是记忆（与人设卡解耦，人设卡也不动）。
    """
    _require_account(account_id)
    layers.clear_messages(account_id)
    graph_mod.clear_session(account_id)
    return {"status": "cleared"}
