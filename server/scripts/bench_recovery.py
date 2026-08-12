"""恢复码生成性能基准：计时 1000 次生成，验证无超线性退化。

背景：用户反馈"身份码迟迟生成不出来"。本脚本证明生成算法本身不是瓶颈
（真正的根因是旧后端进程无 --reload 导致的前后端接口不一致，见 dev.sh 注释）。

运行：.venv/bin/python scripts/bench_recovery.py
"""
import os
import sys
import time
import uuid

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "bench_recovery.db")
os.environ["DB_PATH"] = os.path.abspath(DB_PATH)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.db.database import generate_recovery_code, get_conn, init_db  # noqa: E402


def bench(n: int) -> tuple[float, float, float]:
    """插入 n 个 account（每个都走 generate_recovery_code），返回总/前半/后半耗时。"""
    t0 = time.perf_counter()
    t_mid = 0.0
    for i in range(n):
        code = generate_recovery_code()
        get_conn().execute(
            "INSERT INTO accounts (id, nickname, created_at, recovery_code) VALUES (?, '压测', '2026-01-01', ?)",
            (uuid.uuid4().hex[:12], code),
        )
        if i == n // 2 - 1:
            t_mid = time.perf_counter()
    total = time.perf_counter() - t0
    first_half = t_mid - t0
    second_half = time.perf_counter() - t_mid
    get_conn().commit()
    return total, first_half, second_half


def main() -> None:
    if os.path.exists(os.environ["DB_PATH"]):
        os.remove(os.environ["DB_PATH"])
    init_db()

    total, h1, h2 = bench(1000)
    print(f"生成 1000 个码（含写库）：总耗时 {total:.3f}s，平均 {total / 1000 * 1000:.3f}ms/个")
    print(f"前 500 个 {h1:.3f}s vs 后 500 个 {h2:.3f}s（随库内码量增长对比）")

    # 无超线性退化：后 500 个耗时不超过前 500 个的 3 倍（唯一索引下应近似持平）
    assert h2 < h1 * 3 + 0.05, f"出现超线性退化：{h1:.3f}s → {h2:.3f}s"
    # 绝对耗时上限：单个码平均不超过 5ms
    assert total / 1000 < 0.005, f"单码平均耗时过高：{total / 1000 * 1000:.2f}ms"

    # 唯一性终检
    count = get_conn().execute(
        "SELECT COUNT(DISTINCT recovery_code) AS c FROM accounts"
    ).fetchone()["c"]
    assert count == 1000, f"唯一索引下出现重复：{count}"
    print("✅ 无超线性退化，1000 个码全局唯一")
    os.remove(os.environ["DB_PATH"])


if __name__ == "__main__":
    main()
