from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from ..services import fragments as svc
from ..services import pipeline, push

router = APIRouter(prefix="/api/fragments", tags=["fragments"])


class CreateFragmentIn(BaseModel):
    circle_id: str
    user_id: str
    content: str = ""  # 配图碎片允许纯图片，content 可空（服务端校验二者至少其一）
    visibility: str = "public"
    image_url: str | None = None


class CreateCommentIn(BaseModel):
    author_id: str
    content: str
    parent_id: str | None = None


class ToggleLikeIn(BaseModel):
    user_id: str


@router.post("")
def create_fragment(body: CreateFragmentIn, background_tasks: BackgroundTasks):
    result = svc.create_fragment(
        body.circle_id, body.user_id, body.content, body.visibility, body.image_url
    )
    # 分类 + embedding + 知识归档 + 愿望提取，异步执行
    background_tasks.add_task(pipeline.process_fragment, result["id"])
    return result


@router.get("")
def list_fragments(
    circle_id: str,
    user_id: str | None = None,
    author: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    return svc.list_fragments(circle_id, user_id, author, limit, offset)


@router.get("/{fragment_id}")
def get_fragment(fragment_id: str, user_id: str | None = None):
    return svc.get_fragment(fragment_id, user_id)


@router.get("/{fragment_id}/related")
def related_fragments(fragment_id: str, user_id: str | None = None):
    return svc.related_fragments(fragment_id, user_id)


@router.delete("/{fragment_id}")
def delete_fragment(fragment_id: str, user_id: str):
    return svc.delete_fragment(fragment_id, user_id)


# ---------- 互动（第 4 期）：仅公共碎片可评论/点赞，服务端校验 ----------

@router.post("/{fragment_id}/comments")
def create_comment(fragment_id: str, body: CreateCommentIn, background_tasks: BackgroundTasks):
    result = svc.add_comment(fragment_id, body.author_id, body.content, body.parent_id)
    # 异步给碎片作者发 Web Push（作者自评不推，service 内部判断）
    background_tasks.add_task(push.notify_comment, fragment_id, body.author_id, body.content)
    return result


@router.get("/{fragment_id}/comments")
def list_comments(fragment_id: str):
    return svc.list_comments(fragment_id)


@router.put("/{fragment_id}/like")
def toggle_like(fragment_id: str, body: ToggleLikeIn, background_tasks: BackgroundTasks):
    result = svc.toggle_like(fragment_id, body.user_id)
    # 只在「赞上」时推送，取消赞不推
    if result["liked"]:
        background_tasks.add_task(push.notify_like, fragment_id, body.user_id)
    return result
