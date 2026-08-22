"""进程内 SSE 事件总线：账号 → 在线页面订阅队列。

单进程部署（uvicorn 单 worker）下够用；跨进程不保证（无 Redis 之类广播），断线由前端
EventSource 自动重连兜底。发布侧可能跑在线程池（BackgroundTasks / ThreadPoolExecutor），
asyncio.Queue 非线程安全，跨线程投递必须经 loop.call_soon_threadsafe。
"""
import asyncio
import json
import logging
from collections import defaultdict

logger = logging.getLogger("us.events")

HEARTBEAT_SECONDS = 30.0  # 心跳间隔（保活代理链路，防中间层断空闲连接）
QUEUE_MAXSIZE = 32  # 单订阅者积压上限：满了丢新事件（页面刷新本就是幂等拉取）

# account_id → {(loop, queue)}：订阅者注册表（loop 用于跨线程投递）
_subs: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = defaultdict(set)


def publish(account_id: str, event: dict) -> None:
    """给该账号所有在线订阅者推一条事件（线程安全；无人订阅即 no-op）。"""
    for loop, q in list(_subs.get(account_id, ())):
        def _put(q=q, event=event):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE 订阅者队列已满，丢弃事件：%s", event)

        try:
            loop.call_soon_threadsafe(_put)
        except RuntimeError:
            pass  # 订阅者事件循环已关（页面刚离开），交给 finally 清理


def publish_all(event: dict) -> None:
    """广播给所有在线订阅者（staging 是全局共享表，治理动作影响所有人）。"""
    for account_id in list(_subs):
        publish(account_id, event)


async def stream(account_id: str):
    """SSE 响应体生成器：先发 retry 对齐重连节奏，再循环推事件 / 心跳注释行。"""
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    loop = asyncio.get_running_loop()
    _subs[account_id].add((loop, q))
    try:
        yield "retry: 10000\n\n"
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": ping\n\n"
    finally:
        _subs[account_id].discard((loop, q))
        if not _subs[account_id]:
            _subs.pop(account_id, None)
