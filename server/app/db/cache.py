"""Redis 缓存/会话。连不上时降级为进程内字典并打 warning，保证无 Redis 也能跑通。"""
import logging
import time
from typing import Any

from ..config import settings

logger = logging.getLogger("us.cache")


class _DictBackend:
    """进程内字典降级实现（接口对齐用到的 redis 子集）。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float | None]] = {}

    def get(self, key: str) -> Any:
        item = self._store.get(key)
        if item is None:
            return None
        value, expire_at = item
        if expire_at is not None and time.time() > expire_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        expire_at = time.time() + ex if ex else None
        self._store[key] = (value, expire_at)
        return True

    def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    def ping(self) -> bool:
        return True


def _connect() -> Any:
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        logger.info("Redis 已连接: %s", settings.REDIS_URL)
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 连接失败（%s），降级为进程内字典缓存", exc)
        return _DictBackend()


client: Any = _connect()
