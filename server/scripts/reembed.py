"""一次性回填：全部碎片/愿望/知识条目按多模态口径重 embed（含既有图片碎片读展示图文件 embed）。

跑法：cd server && .venv-mac/bin/python scripts/reembed.py
口径与各写入路径严格一致：
- 碎片 = pipeline.fragment_embedding（图文双有取均值+归一化，纯图片用图片向量）
- 愿望 = add_wish / 碎片管线同口径的纯文本向量（content 为空用占位词）
- 知识条目 = 标题 + 来源碎片原文 + 正文前 500 字（pipeline 归档公式）
要求真实 EMBEDDING 配置（未配置会在首行 embed 抛 AINotConfiguredError）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import ai  # noqa: E402
from app.db.database import encode_embedding, get_conn, init_db  # noqa: E402
from app.services.pipeline import fragment_embedding  # noqa: E402


def main() -> None:
    init_db()
    conn = get_conn()

    rows = conn.execute("SELECT id, content, image_url FROM fragments").fetchall()
    for i, r in enumerate(rows, 1):
        vec = fragment_embedding(r["content"], r["image_url"])
        conn.execute(
            "UPDATE fragments SET embedding=? WHERE id=?", (encode_embedding(vec), r["id"])
        )
        if i % 50 == 0:
            conn.commit()
            print(f"碎片已回填 {i}/{len(rows)} 条…")
    conn.commit()
    print(f"碎片回填完成：共 {len(rows)} 条")

    rows = conn.execute("SELECT id, content FROM wishes").fetchall()
    for r in rows:
        vec = ai.embed_text(r["content"] or "[图片]")
        conn.execute(
            "UPDATE wishes SET embedding=? WHERE id=?", (encode_embedding(vec), r["id"])
        )
    conn.commit()
    print(f"愿望回填完成：共 {len(rows)} 条")

    rows = conn.execute(
        """SELECT k.id, k.title, k.content, f.content AS frag_content
           FROM knowledge_items k JOIN fragments f ON f.id = k.fragment_id"""
    ).fetchall()
    for r in rows:
        parts = [r["title"], r["frag_content"]]
        if r["content"] and r["content"] != r["frag_content"]:
            parts.append(r["content"][:500])
        vec = ai.embed_text(" ".join(p for p in parts if p))
        conn.execute(
            "UPDATE knowledge_items SET embedding=? WHERE id=?",
            (encode_embedding(vec), r["id"]),
        )
    conn.commit()
    print(f"知识条目回填完成：共 {len(rows)} 条")


if __name__ == "__main__":
    main()
