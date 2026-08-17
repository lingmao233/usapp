"""碎片发布后的异步管线：图片步骤（caption）→ 分类 → embedding → 知识归档 → 愿望提取。"""
import json
import logging
import re
import uuid
from datetime import datetime

import httpx
import numpy as np

from .. import ai
from ..db.database import encode_embedding, get_conn
from . import fragments, memory, tasks

logger = logging.getLogger("us.pipeline")

_URL_RE = re.compile(r"https?://[^\s]+")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fetch_link(url: str) -> dict:
    """httpx + readability 抓正文；抓不到降级只存标题/URL。"""
    try:
        resp = httpx.get(
            url,
            timeout=10.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; UsAppBot/1.0)"},
        )
        resp.raise_for_status()
        from readability import Document

        doc = Document(resp.text)
        content = re.sub(r"<[^>]+>", "", doc.summary())
        content = re.sub(r"\s+", " ", content).strip()
        return {"title": doc.title().strip() or url, "content": content[:8000]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("链接抓取失败（%s），降级只存 URL", exc)
        return {"title": url, "content": ""}


def process_fragment(fragment_id: str) -> None:
    """BackgroundTasks 入口。经任务层执行：失败重试 + 状态落 task_runs，不再静默。"""
    tasks.run_task("fragment_pipeline", fragment_id, lambda: _process(fragment_id))


def fragment_embedding(content: str, caption: str = "") -> np.ndarray:
    """碎片向量：统一走纯文本 embedding——带图碎片用「正文 + caption」拼接文本；
    视觉关闭（无 caption）退回 正文 or "[图片]" 的占位逻辑。"""
    return ai.embed_text(" ".join(p for p in (content, caption) if p) or "[图片]")


def _process(fragment_id: str) -> None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM fragments WHERE id = ?", (fragment_id,)).fetchone()
    if row is None:
        return
    content: str = row["content"]
    circle_id: str = row["circle_id"]
    user_id: str = row["user_id"]
    image_url: str = row["image_url"] or ""
    # 纯图片碎片：文本类 AI 输入统一用占位词，分类/embedding 照常跑，保证数据结构一致
    ai_text = content or "[图片]"

    # 0. 图片 caption（可选）：未配视觉模型 / 调用失败都优雅跳过，不影响管线
    caption = ""
    if image_url:
        data, fmt = fragments.read_display_image(image_url)
        if data:
            caption = ai.image_caption(data, fmt)
            if caption:
                conn.execute("UPDATE fragments SET caption=? WHERE id=?", (caption, fragment_id))

    # 1. 分类（输入 = 原文 + caption，让图片内容参与打标签）
    classify_input = " ".join(p for p in (content, caption) if p) or "[图片]"
    result = ai.classify_fragment(classify_input)
    conn.execute(
        """UPDATE fragments SET type=?, tags=?, is_knowledge=?, is_wish=?,
           wish_category=?, ai_summary=? WHERE id=?""",
        (
            result["type"],
            json.dumps(result["tags"], ensure_ascii=False),
            int(result["is_knowledge"]),
            int(result["is_wish"]),
            result["wish_category"] or "",
            result["ai_summary"],
            fragment_id,
        ),
    )

    # 2. embedding（正文 + caption 拼接的纯文本向量；无 caption 退回占位词）
    vec = fragment_embedding(content, caption)
    conn.execute(
        "UPDATE fragments SET embedding=? WHERE id=?",
        (encode_embedding(vec), fragment_id),
    )

    # 3. 知识归档（隐私碎片不进知识库，其余步骤照常）
    if result["is_knowledge"] and row["visibility"] == "public":
        url_match = _URL_RE.search(content)
        url = url_match.group(0) if url_match else ""
        if url:
            fetched = fetch_link(url)
            title, body = fetched["title"], fetched["content"]
            if len(body) < 100:
                # 反爬站点（小红书/公众号等）只返回版权页样板文字，视为未抓到正文
                body = ""
        else:
            title, body = result["ai_summary"] or ai_text[:30], ai_text
        summary = ai.summarize_text(body) if body else ai.summarize_text(ai_text)
        # embedding 素材：标题 + 用户备注 + 抓到的正文前 500 字（正文缺失时只用前两项）
        parts = [title, ai_text]
        if body and body != content:
            parts.append(body[:500])
        k_vec = ai.embed_text(" ".join(p for p in parts if p))
        conn.execute(
            """INSERT INTO knowledge_items
               (id, fragment_id, circle_id, title, url, content, summary, tags, embedding, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                uuid.uuid4().hex[:12],
                fragment_id,
                circle_id,
                title,
                url,
                body,
                summary,
                json.dumps(result["tags"], ensure_ascii=False),
                encode_embedding(k_vec),
                _now(),
            ),
        )

    # 4. 愿望提取（visibility 与配图继承来源碎片）
    if result["is_wish"]:
        w_vec = ai.embed_text(ai_text)
        conn.execute(
            """INSERT INTO wishes
               (id, user_id, circle_id, content, category, fragment_id, status,
                matched_users, embedding, plan, created_at, visibility, image_url)
               VALUES (?, ?, ?, ?, ?, ?, 'active', '[]', ?, NULL, ?, ?, ?)""",
            (
                uuid.uuid4().hex[:12],
                user_id,
                circle_id,
                ai_text,
                result["wish_category"] or "do",
                fragment_id,
                encode_embedding(w_vec),
                _now(),
                row["visibility"],
                row["image_url"],
            ),
        )

    conn.execute("UPDATE fragments SET processed=1 WHERE id=?", (fragment_id,))
    conn.commit()
    # 写路径打点：画像与相关用户对标记 dirty，等每晚蒸馏重算
    memory.mark_dirty(circle_id, user_id)
