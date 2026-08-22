"""一次性清流：识别纠正震荡数据 + staging 品类先验错值（BUG-021/022 存量清理，见 docs/BUG记录.md）。

跑法：
    cd server && .venv-mac/bin/python scripts/cleanup_pollution.py           # dry-run（默认，只报告）
    cd server && .venv-mac/bin/python scripts/cleanup_pollution.py --apply   # 真正执行

清理两处：
1. 名字纠正循环（红茶→火腿 + 火腿→红茶 双向震荡）：按账号算「生效纠正图」（每个识别名取
   最新一条），找到环上的名字，把这些名字的全部纠正行删掉——包括被环内行压住的旧行
   （只删环行会让旧行复活成新的错误纠正，火腿→茶 就是这么漏网的）。
2. staging 饮品类错值：source=web 且名字是饮品（结尾词判断，复用 nutrition 品类先验）、
   每 100g 热量超先验上限的行（红茶 294 即此例）——物理删除 + 清认可记录。
   staging 是全局共享表，错值污染所有账号。

受影响的已入账条目只报告不改动（历史账目动不动由用户决定，页面支持逐条删除/改名重算）。
幂等：重复跑第二遍无东西可清。
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import get_conn, init_db  # noqa: E402
from app.services import nutrition  # noqa: E402


def _cycle_names(conn) -> dict[str, list[dict]]:
    """按账号找纠正环：生效图（recognized→最新 corrected）上从任意点出发能走回自己的名字集。

    返回 {account_id: [将被删除的行...]}（环上名字的全部行，含被压住的旧行）。
    """
    rows = conn.execute(
        "SELECT rowid, account_id, recognized_name, corrected_name, created_at"
        " FROM calorie_name_corrections ORDER BY rowid").fetchall()
    per_account: dict[str, list] = defaultdict(list)
    for r in rows:
        per_account[r["account_id"]].append(r)

    out: dict[str, list[dict]] = {}
    for account_id, arows in per_account.items():
        latest: dict[str, str] = {}  # 生效纠正图：recognized → 最新 corrected
        for r in arows:  # 按 rowid 升序，后来者覆盖
            latest[r["recognized_name"]] = r["corrected_name"]
        on_cycle: set[str] = set()
        for start in latest:
            seen: dict[str, int] = {}
            node, step = start, 0
            while node in latest and node not in seen:
                seen[node] = step
                node = latest[node]
                step += 1
            if node in seen:  # 走回了访问过的点 → 这段路径成环
                for n in seen:
                    if seen[n] >= seen[node]:  # 只收环上（入口之后）的名字
                        on_cycle.add(n)
        if on_cycle:
            out[account_id] = [dict(r) for r in arows if r["recognized_name"] in on_cycle]
    return out


def _drink_outlier_rows(conn) -> list[dict]:
    """staging 里饮品超先验的 web 错值行（红茶 294 这类；用户手填行交给管理面板处理）。"""
    rows = conn.execute(
        "SELECT id, name, brand, kcal_per_100g, source, verified, created_at"
        " FROM food_nutrition_staging WHERE deleted = 0").fetchall()
    return [dict(r) for r in rows
            if r["source"] == "web"
            and nutrition.is_drink_name(r["name"])
            and float(r["kcal_per_100g"]) > nutrition.drink_prior_max(r["name"])]


def _affected_entries(conn, names: list[str], staging_ids: list[int]) -> list[str]:
    """引用了被清名字/staging 行的已入账条目（只报告，不动）。"""
    marks: list[str] = []
    name_set = set(names)
    for r in conn.execute(
            "SELECT id, account_id, total_kcal, items, created_at FROM calorie_entries").fetchall():
        try:
            items = json.loads(r["items"] or "[]")
        except ValueError:
            continue
        hit = any(
            (isinstance(it, dict) and (it.get("name") in name_set
                                       or it.get("raw_name") in name_set
                                       or it.get("staging_id") in staging_ids))
            for it in items
        )
        if hit:
            marks.append(f"  {r['id']}（{r['created_at'][:16]}，{r['total_kcal']} kcal，"
                         f"账号 {r['account_id']}）")
    return marks


def main() -> None:
    parser = argparse.ArgumentParser(description="纠正震荡 + staging 饮品错值一次性清理")
    parser.add_argument("--apply", action="store_true", help="真正执行（默认 dry-run 只报告）")
    args = parser.parse_args()
    init_db()
    conn = get_conn()

    cycles = _cycle_names(conn)
    print("=" * 60)
    print("① 名字纠正循环（红茶→火腿 这类双向震荡）：")
    removed_names: list[str] = []
    cycle_rows = 0
    if not cycles:
        print("  无")
    for account_id, rows in cycles.items():
        print(f"  账号 {account_id}，将删除 {len(rows)} 行：")
        for r in rows:
            print(f"    [{r['created_at'][:16]}] {r['recognized_name']} → {r['corrected_name']}")
            removed_names.extend((r["recognized_name"], r["corrected_name"]))
        cycle_rows += len(rows)

    outliers = _drink_outlier_rows(conn)
    print("=" * 60)
    print("② staging 饮品类错值（source=web 且超品类先验）：")
    staging_ids: list[int] = []
    if not outliers:
        print("  无")
    for r in outliers:
        prior = nutrition.drink_prior_max(r["name"])
        print(f"  id={r['id']} {r['name']}（{r['brand'] or '通用'}）"
              f" {r['kcal_per_100g']} kcal/100g > 先验 {prior}，"
              f"verified={r['verified']}，{r['created_at'][:16]} 落库")
        staging_ids.append(r["id"])

    marks = _affected_entries(conn, sorted(set(removed_names)), staging_ids)
    print("=" * 60)
    print(f"③ 受影响的已入账条目（只报告，不改动；可在热量页逐条删除或改名重算）共 {len(marks)} 条：")
    for m in marks:
        print(m)

    if not args.apply:
        print("=" * 60)
        print("dry-run：以上仅为报告。确认无误后加 --apply 执行。")
        return

    if cycle_rows:
        conn.executemany(
            "DELETE FROM calorie_name_corrections WHERE rowid = ?",
            [(r["rowid"],) for rows in cycles.values() for r in rows])
    for sid in staging_ids:
        conn.execute("DELETE FROM food_staging_approvals WHERE staging_id = ?", (sid,))
        conn.execute("DELETE FROM food_nutrition_staging WHERE id = ?", (sid,))
    conn.commit()
    print("=" * 60)
    print(f"已执行：删除纠正行 {cycle_rows} 条，删除 staging 错值行 {len(staging_ids)} 行。")
    print("注意：下次联网再搜到同名饮品仍会被品类先验拦下（代码修复的一部分），不会复发。")


if __name__ == "__main__":
    main()
