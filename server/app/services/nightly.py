"""每晚蒸馏任务：对全部圈子的 dirty 画像/用户对重算分量并生成画像与关系摘要。

顺带为共同愿望预生成「一起去」方案（缓存进 wishes.plan，点开即见，不必现场等 LLM）。

cron 入口（排在每日备份 3:17 之前，见 docs/部署指南.md）：

    cd server && python -m app.services.nightly
"""
import logging

from ..db.database import get_conn, init_db
from . import memory, tasks, wishes

logger = logging.getLogger("us.nightly")


def _pregen_plans(circle_id: str) -> None:
    """预生成：本圈共同愿望里还没有方案的，逐簇生成（已有的走缓存秒回，不重复调 LLM）。

    匹配读 common_wishes 的圈级缓存——池子没变时零 LLM 调用；缓存过期时
    批处理场景同步重算（接口的 stale-while-revalidate 只适用于请求路径）。
    """
    out = wishes.common_wishes(circle_id)
    if out.get("refreshing"):
        wishes.refresh_common_wishes(circle_id)
        out = wishes.common_wishes(circle_id)
    for c in out["common_wishes"]:
        wid = (c.get("wish_ids") or [""])[0]
        if wid:
            wishes.generate_plan(wid)


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
        tasks.run_task("pregen_plans", c["id"], lambda cid=c["id"]: _pregen_plans(cid))
    logger.info("每晚蒸馏完成：%d 个圈子，%s", len(circles), stats)
    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run()


if __name__ == "__main__":
    main()
