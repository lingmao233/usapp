"""成分表静态补充（优化清单第 2 项）：常见家常菜/外卖/饮品补进正式表。

为什么：《中国食物成分表》vendor 数据缺 饺子/包子/挂面 等高频品类（评测实证 0 行），
识别到只能走联网（20-25s）或模型估值；饮品（红茶 1 kcal）本该本地就有——BUG-022 的
红茶 294 错值事故，根因之一就是本地查不到才走联网。补进正式表后这类查询毫秒级命中
且值是确定的。口径：每 100g/100ml 可食（即饮）形态；数值取常见代表值。

跑法：cd server && .venv-mac/bin/python scripts/supplement_food_nutrition.py
幂等：INSERT OR IGNORE（UNIQUE(name, brand)）；重复跑零新增。
embedding：优先真实 embed_texts（与灌库同口径）；未配置时落 NULL（LIKE 匹配仍可用，
向量兜底缺失——配好 key 后跑 scripts/reembed.py 补）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import ai  # noqa: E402
from app.db.database import encode_embedding, get_conn, init_db  # noqa: E402

ASSET = os.path.join(os.path.dirname(__file__), "assets", "food_nutrition_supplement.json")


def main() -> None:
    init_db()
    rows = json.loads(open(ASSET, encoding="utf-8").read())
    conn = get_conn()

    existing = {r["name"] for r in conn.execute("SELECT name FROM food_nutrition")}
    todo = [r for r in rows if r["name"] not in existing]
    if not todo:
        print(f"补充数据已是最新：{len(rows)} 行全部存在，零新增。")
        return

    embeds: list = [None] * len(todo)
    try:
        vecs = ai.embed_texts([r["name"] for r in todo])
        embeds = [encode_embedding(v) for v in vecs]
    except Exception as exc:  # noqa: BLE001 —— 未配置/失败：NULL 落库，LIKE 可用
        print(f"向量不可用（{exc}），按 NULL 落库；配好 key 后跑 scripts/reembed.py 补。")

    for r, emb in zip(todo, embeds):
        conn.execute(
            """INSERT OR IGNORE INTO food_nutrition
               (name, brand, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g, embedding)
               VALUES (?, '', ?, ?, ?, ?, ?)""",
            (r["name"], r["kcal_per_100g"], r.get("protein_per_100g"),
             r.get("fat_per_100g"), r.get("cho_per_100g"), emb),
        )
    conn.commit()
    print(f"已补充 {len(todo)} 行：{('、'.join(r['name'] for r in todo))}")
    print(f"（全集 {len(rows)} 行，重复执行幂等零新增）")


if __name__ == "__main__":
    main()
