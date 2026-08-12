"""验证 mock embedding 对相似中文短文本的余弦是否足够高（开发自测用）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DB_PATH"] = "/tmp/us-sim-check.db"

from app.ai import mock  # noqa: E402
from app.db.database import cosine  # noqa: E402

pairs = [
    ("想去海边看日出", "想去海边看日出，吹吹风"),
    ("想学滑板", "想学滑板呀"),
    ("想学滑板", "最近好想学滑板，有没有一起的"),
    ("看到一篇讲海边城市旅行攻略的文章，先存着", "海边旅行攻略"),
    ("今天加班好累", "想学滑板呀"),
]
for a, b in pairs:
    print(f"{cosine(mock.embed(a), mock.embed(b)):.4f}  {a!r} vs {b!r}")
