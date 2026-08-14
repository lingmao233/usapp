"""SQLite 连接与 schema。embedding 以 float32 blob 存储，检索时 numpy 暴力余弦。"""
import random
import sqlite3
import threading
from pathlib import Path

import numpy as np

from ..config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS circles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    invite_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    persona_preset TEXT NOT NULL DEFAULT 'observer',
    persona_custom TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    nickname TEXT NOT NULL,
    created_at TEXT NOT NULL,
    recovery_code TEXT
);

CREATE TABLE IF NOT EXISTS memberships (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    circle_id TEXT NOT NULL REFERENCES circles(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE(account_id, circle_id)
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    nickname TEXT NOT NULL,
    avatar TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    circle_id TEXT NOT NULL REFERENCES circles(id)
);

CREATE TABLE IF NOT EXISTS fragments (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    circle_id TEXT NOT NULL REFERENCES circles(id),
    content TEXT NOT NULL,
    type TEXT DEFAULT 'text',
    tags TEXT DEFAULT '[]',
    mood TEXT DEFAULT '',
    embedding BLOB,
    created_at TEXT NOT NULL,
    is_knowledge INTEGER DEFAULT 0,
    is_wish INTEGER DEFAULT 0,
    wish_category TEXT DEFAULT '',
    ai_summary TEXT DEFAULT '',
    processed INTEGER DEFAULT 0,
    visibility TEXT NOT NULL DEFAULT 'public',
    image_url TEXT,
    caption TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS knowledge_items (
    id TEXT PRIMARY KEY,
    fragment_id TEXT NOT NULL REFERENCES fragments(id),
    circle_id TEXT NOT NULL REFERENCES circles(id),
    title TEXT DEFAULT '',
    url TEXT DEFAULT '',
    content TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    embedding BLOB,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wishes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    circle_id TEXT NOT NULL REFERENCES circles(id),
    content TEXT NOT NULL,
    category TEXT DEFAULT 'do',
    fragment_id TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    matched_users TEXT DEFAULT '[]',
    embedding BLOB,
    plan TEXT,
    created_at TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public',
    image_url TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    circle_id TEXT NOT NULL REFERENCES circles(id),
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    content TEXT NOT NULL,
    key_connections TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(circle_id, week_start)
);

-- 最小统一任务层：每次管线运行落一行，失败/降级不再静默
CREATE TABLE IF NOT EXISTS task_runs (
    id TEXT PRIMARY KEY,
    task_name TEXT NOT NULL,
    entity_id TEXT DEFAULT '',
    status TEXT NOT NULL,
    error TEXT DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT
);

-- 记忆层（第 2 期）：每 circle 每 user 一行画像
CREATE TABLE IF NOT EXISTS user_profiles (
    circle_id TEXT NOT NULL REFERENCES circles(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    profile TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL,
    dirty INTEGER DEFAULT 1,
    PRIMARY KEY (circle_id, user_id)
);

-- 记忆层（第 2 期）：每 circle 内无序用户对一行（user_a < user_b）
CREATE TABLE IF NOT EXISTS pair_relationships (
    circle_id TEXT NOT NULL REFERENCES circles(id),
    user_a TEXT NOT NULL REFERENCES users(id),
    user_b TEXT NOT NULL REFERENCES users(id),
    semantic REAL DEFAULT 0,
    interaction REAL DEFAULT 0,
    common_wishes REAL DEFAULT 0,
    secret_common_wishes INTEGER DEFAULT 0,
    common_topics REAL DEFAULT 0,
    topics TEXT DEFAULT '[]',
    summary TEXT DEFAULT '',
    updated_at TEXT NOT NULL,
    dirty INTEGER DEFAULT 1,
    PRIMARY KEY (circle_id, user_a, user_b)
);

-- 互动（第 4 期）：楼中楼评论，parent_id 为 NULL 是顶级评论
CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    circle_id TEXT NOT NULL REFERENCES circles(id),
    fragment_id TEXT NOT NULL REFERENCES fragments(id),
    author_id TEXT NOT NULL REFERENCES users(id),
    parent_id TEXT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 互动（第 4 期）：点赞，(fragment_id, user_id) 唯一，取消即删行
CREATE TABLE IF NOT EXISTS likes (
    id TEXT PRIMARY KEY,
    circle_id TEXT NOT NULL REFERENCES circles(id),
    fragment_id TEXT NOT NULL REFERENCES fragments(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE(fragment_id, user_id)
);

-- 推送（第 5 期）：Web Push 订阅，endpoint 唯一（重复订阅去重 / 换设备换绑用户都走更新）
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    endpoint TEXT NOT NULL UNIQUE,
    keys_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 共同愿望匹配结果缓存：圈级一行，匹配池指纹变化才重算（不每个写路径手动失效）
CREATE TABLE IF NOT EXISTS common_wishes_cache (
    circle_id TEXT PRIMARY KEY REFERENCES circles(id),
    fingerprint TEXT NOT NULL,
    result TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 会话（方案追问）：通用设计——kind + ref_id 标记关联对象（plan → wish_id），
-- 未来独立 AI 聊天页直接复用这两张表（kind='free' 之类）
CREATE TABLE IF NOT EXISTS chat_threads (
    id TEXT PRIMARY KEY,
    circle_id TEXT NOT NULL REFERENCES circles(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL DEFAULT 'plan',
    ref_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(user_id, kind, ref_id)
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES chat_threads(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def get_conn() -> sqlite3.Connection:
    """线程本地连接（FastAPI 同步路由跑在线程池里）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(settings.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # WAL 下读不阻塞写，但写与写仍串行：把等锁上限从默认 5s 提到 30s，
        # 避免蒸馏/周报等长任务期间并发写直接报 database is locked
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def init_db() -> None:
    get_conn().executescript(SCHEMA)
    _migrate_accounts_recovery_code()
    _migrate_fragments_visibility()
    _migrate_wishes_visibility()
    _migrate_pair_secret_common_wishes()
    _migrate_image_url()
    _migrate_fragments_caption()
    _migrate_circles_persona()
    _migrate_wishes_matched_status()
    get_conn().commit()


# ---------- 恢复码 ----------

# 去掉易混淆字符（0/O、1/I/L）的大写字母数字集
RECOVERY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
RECOVERY_LENGTH = 6  # 新码 6 位；存量 8 位码依然有效（claim 兼容任意长度）


def generate_recovery_code() -> str:
    """生成全局唯一的 6 位随机码（存量 8 位码依然有效；与自定义码按 ASCII 折叠查重）。"""
    conn = get_conn()
    while True:
        code = "".join(random.choices(RECOVERY_ALPHABET, k=RECOVERY_LENGTH))
        exists = conn.execute(
            "SELECT 1 FROM accounts WHERE UPPER(recovery_code) = ?", (code,)
        ).fetchone()
        if not exists:
            return code


def _migrate_accounts_recovery_code() -> None:
    """存量迁移：老库 accounts 无 recovery_code 列时补列，并为存量记录补生成。"""
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    if cols and "recovery_code" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN recovery_code TEXT")
    # 列就绪后再建唯一索引（老库在 executescript 阶段尚无此列）
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_recovery ON accounts(recovery_code)"
    )
    rows = conn.execute(
        "SELECT id FROM accounts WHERE recovery_code IS NULL OR recovery_code = ''"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE accounts SET recovery_code = ? WHERE id = ?",
            (generate_recovery_code(), row["id"]),
        )


def _migrate_fragments_visibility() -> None:
    """存量迁移：老库 fragments 无 visibility 列时补列，存量数据默认 public。"""
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(fragments)").fetchall()]
    if cols and "visibility" not in cols:
        conn.execute(
            "ALTER TABLE fragments ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'"
        )


def _migrate_wishes_visibility() -> None:
    """存量迁移：老库 wishes 无 visibility 列时补列；碎片来源愿望同步来源碎片的可见性，
    手动愿望（fragment_id=''）保持默认 public。须在 fragments 迁移之后跑。"""
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(wishes)").fetchall()]
    if cols and "visibility" not in cols:
        conn.execute(
            "ALTER TABLE wishes ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'"
        )
        conn.execute(
            """UPDATE wishes SET visibility = 'private'
               WHERE fragment_id IN (SELECT id FROM fragments WHERE visibility = 'private')"""
        )


def _migrate_pair_secret_common_wishes() -> None:
    """存量迁移：老库 pair_relationships 无 secret_common_wishes 列时补列。"""
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(pair_relationships)").fetchall()]
    if cols and "secret_common_wishes" not in cols:
        conn.execute(
            "ALTER TABLE pair_relationships ADD COLUMN secret_common_wishes INTEGER DEFAULT 0"
        )


def _migrate_image_url() -> None:
    """存量迁移：老库 fragments / wishes 无 image_url 列时补列（图片配图）。"""
    conn = get_conn()
    for table in ("fragments", "wishes"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if cols and "image_url" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN image_url TEXT")


def _migrate_fragments_caption() -> None:
    """存量迁移：老库 fragments 无 caption 列时补列（图片智能化：视觉模型描述）。"""
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(fragments)").fetchall()]
    if cols and "caption" not in cols:
        conn.execute("ALTER TABLE fragments ADD COLUMN caption TEXT DEFAULT ''")


def _migrate_circles_persona() -> None:
    """存量迁移：老库 circles 无人格两列时补列（人格系统），存量圈默认观察员。"""
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(circles)").fetchall()]
    if cols and "persona_preset" not in cols:
        conn.execute(
            "ALTER TABLE circles ADD COLUMN persona_preset TEXT NOT NULL DEFAULT 'observer'"
        )
        conn.execute(
            "ALTER TABLE circles ADD COLUMN persona_custom TEXT NOT NULL DEFAULT ''"
        )


def _migrate_wishes_matched_status() -> None:
    """数据迁移：matched 语义下线（生成方案不再改状态，完成与否只由用户勾选决定），
    存量 matched 愿望迁回 active 重新进入共同愿望匹配池。幂等，每次启动跑无负担。"""
    get_conn().execute("UPDATE wishes SET status='active' WHERE status='matched'")


def reset_db() -> None:
    """仅测试用：清空全部数据。"""
    conn = get_conn()
    for table in ("chat_messages", "chat_threads", "common_wishes_cache", "pair_relationships", "user_profiles", "task_runs", "reports", "wishes", "comments", "likes", "push_subscriptions", "knowledge_items", "fragments", "memberships", "users", "circles", "accounts"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


# ---------- embedding blob 编解码 ----------

def encode_embedding(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def decode_embedding(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    # 维度不一致（换过 embedding 维度/端点的存量数据）视为不相似，不抛异常拖垮整批计算；
    # 想恢复真实相似度，跑 scripts/reembed.py 回填统一维度
    if a.shape != b.shape:
        return 0.0
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
