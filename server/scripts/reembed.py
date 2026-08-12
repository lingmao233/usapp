"""一次性回填：全部碎片按多模态口径重 embed（含既有图片碎片读展示图文件 embed）。

跑法：cd server && .venv-mac/bin/python scripts/reembed.py
与 pipeline.fragment_embedding 同口径：图文双有取均值+归一化，纯图片用图片向量。
mock 模式下重 embed 也得到确定性结果（文本向量不变、图片碎片从占位词向量换成图片哈希向量）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
            print(f"已回填 {i}/{len(rows)} 条…")
    conn.commit()
    print(f"回填完成：共 {len(rows)} 条碎片")


if __name__ == "__main__":
    main()
