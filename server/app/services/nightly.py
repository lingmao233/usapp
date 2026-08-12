"""每晚蒸馏任务：对全部圈子的 dirty 画像/用户对重算分量并生成画像与关系摘要。

cron 入口（排在每日备份 3:17 之前，见 docs/部署指南.md）：

    cd server && python -m app.services.nightly
"""
import logging

from ..db.database import get_conn, init_db
from . import memory, tasks

logger = logging.getLogger("us.nightly")


def run() -> dict:
    """每个圈子各跑一个 nightly_distill 任务（走任务层），只处理 dirty 行。返回状态计数。"""
    init_db()
    stats: dict[str, int] = {}
    circles = get_conn().execute("SELECT id FROM circles").fetchall()
    for c in circles:
        status = tasks.run_task(
            "nightly_distill", c["id"], lambda cid=c["id"]: memory.refresh_dirty(cid)
        )
        stats[status] = stats.get(status, 0) + 1
    logger.info("每晚蒸馏完成：%d 个圈子，%s", len(circles), stats)
    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run()


if __name__ == "__main__":
    main()
