"""SQLite 连接与 schema。embedding 以 float32 blob 存储，检索时 numpy 暴力余弦。"""
import json
import logging
import random
import sqlite3
import threading
from pathlib import Path

import numpy as np

from ..config import settings

logger = logging.getLogger("us.db")

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
    username TEXT,               -- 全局唯一（大小写不敏感）；NULL=旧接口自动建的账号，不可登录
    password_hash TEXT,          -- 可空，NULL/空串=无密码账号（只校验用户名）
    created_at TEXT NOT NULL,
    recovery_code TEXT           -- 找回凭证：仅用于忘密码时重设密码
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

-- 个人功能（目标/计划/记账/热量/鞭策）：账号级数据（account_id），跨圈唯一一份，
-- 与圈子正交；可见性由 self_sharing（类别 × 圈子开关）驱动，过滤全在服务端
CREATE TABLE IF NOT EXISTS goals (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    type TEXT NOT NULL,                           -- weight_loss/savings/study/custom
    title TEXT NOT NULL,
    params TEXT NOT NULL DEFAULT '{}',            -- 目标参数(目标体重/总额/截止日期等)
    answers TEXT NOT NULL DEFAULT '{}',           -- 问卷答案
    framework TEXT NOT NULL DEFAULT '{}',         -- 规则算出的周期框架(热量预算/月预算等)
    status TEXT NOT NULL DEFAULT 'active',        -- active/done/abandoned
    nudge_enabled INTEGER NOT NULL DEFAULT 1,
    last_settled_month TEXT NOT NULL DEFAULT '',  -- 存款滚雪球结算游标 'YYYY-MM'
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_items (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    goal_id TEXT,                                 -- 可空=自定义条目
    date TEXT NOT NULL,                           -- 'YYYY-MM-DD'
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'task',            -- habit/daily/task
    source TEXT NOT NULL DEFAULT 'custom',        -- ai/custom/adjust
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    amount_fen INTEGER NOT NULL,                  -- 分；负数=收入
    category TEXT NOT NULL,
    merchant TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    spent_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',        -- vision/manual
    image_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed',     -- pending/confirmed
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calorie_entries (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    total_kcal REAL NOT NULL,
    items TEXT NOT NULL DEFAULT '[]',             -- 菜品明细 JSON
    exercise_equiv TEXT NOT NULL DEFAULT '{}',    -- MET 换算结果
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    image_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed',     -- pending/confirmed
    created_at TEXT NOT NULL
);

-- 鞭策：goal_id 与 plan_date 必居其一——目标鞭策=goal_id 非空/plan_date 空；
-- 计划鞭策=plan_date='YYYY-MM-DD'（被鞭策的当天）/goal_id 空。限频对人不对类型
CREATE TABLE IF NOT EXISTS nudges (
    id TEXT PRIMARY KEY,
    goal_id TEXT,
    plan_date TEXT,
    from_account_id TEXT NOT NULL,
    to_account_id TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nudge_blocks (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    blocked_account_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Self 共享：类别 × 圈子开关。level 仅 goal/plan 用（progress/detail），
-- ledger/calorie 只有开关无档位（level 恒 ''）；有行=共享，删行=关闭
CREATE TABLE IF NOT EXISTS self_sharing (
    account_id TEXT NOT NULL REFERENCES accounts(id),
    circle_id TEXT NOT NULL REFERENCES circles(id),
    category TEXT NOT NULL,                       -- goal/plan/ledger/calorie
    level TEXT NOT NULL DEFAULT '',               -- progress/detail/''
    created_at TEXT NOT NULL,
    PRIMARY KEY (account_id, circle_id, category)
);

-- 食物营养成分（《中国食物成分表》vendor 数据，scripts/assets/food_nutrition.json）：
-- 热量识别「识别与计算拆开」的查表数据源；宏量营养素可空（成分表本身缺失）。
-- embedding 为 name 的文本向量（灌入时算好），LIKE 不中时向量余弦兜底
CREATE TABLE IF NOT EXISTS food_nutrition (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',         -- 品牌（包装食品精准匹配用；原料/通用行为空串）
    kcal_per_100g REAL NOT NULL,
    protein_per_100g REAL,
    fat_per_100g REAL,
    cho_per_100g REAL,
    embedding BLOB,
    UNIQUE(name, brand)                     -- 「种类+品牌」两级：火鸡面/三养 与 火鸡面/ 是不同行
);

-- 营养共建预数据库（用户共建信任管线）：存「不确定真假」的营养数据。
-- source=user 用户手动添加（联网核验后 verified=1；与联网值差 50% 以上保持 verified=0 待核实）
-- source=web  查表未命中时联网搜到的（落库即 verified=1，item 标 web_pending 待用户认可）
-- approvals ≥ 3（不同账号认可，去重见下表）→ 晋升正式 food_nutrition 并删除本行
CREATE TABLE IF NOT EXISTS food_nutrition_staging (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',         -- 品牌（与正式表同口径；空串=通用款）
    kcal_per_100g REAL NOT NULL,
    protein_per_100g REAL,
    fat_per_100g REAL,
    cho_per_100g REAL,
    source TEXT NOT NULL DEFAULT 'user',    -- user/web
    verified INTEGER NOT NULL DEFAULT 0,
    approvals INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(name, brand)
);

-- 认可去重：同一账号对同一 staging 行只计一次 approvals
CREATE TABLE IF NOT EXISTS food_staging_approvals (
    staging_id INTEGER NOT NULL REFERENCES food_nutrition_staging(id),
    account_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (staging_id, account_id)
);

-- ---------- 情绪树洞（Agent 化改造）：新表独立，不动现有表 ----------

-- L0 对话原文：树洞每轮 user/assistant 全文落库，永不删（摘要不替代原文，可回溯）
CREATE TABLE IF NOT EXISTS treehole_messages (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    role TEXT NOT NULL,                    -- user/assistant
    content TEXT NOT NULL,
    image_url TEXT NOT NULL DEFAULT '',    -- 树洞发图：/api/uploads/... 原图 URL（空=纯文本）
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_treehole_messages_account
    ON treehole_messages(account_id, created_at);

-- L1 原子记忆：一条一事实（喜好/事实/事件/承诺），每轮对话后实时抽取（hot path）。
-- source_msg_ids 记录来源 L0 消息 id（JSON 数组，可回溯）；embedding 供向量召回
CREATE TABLE IF NOT EXISTS memory_atoms (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL,                    -- preference/fact/event/commitment
    content TEXT NOT NULL,
    source_msg_ids TEXT NOT NULL DEFAULT '[]',
    embedding BLOB,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_atoms_account ON memory_atoms(account_id, kind);

-- L2 场景记忆：围绕某主题聚合多条 L1（后台异步聚类产出；pinned=置顶主题）
CREATE TABLE IF NOT EXISTS memory_scenarios (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    topic TEXT NOT NULL,
    atom_ids TEXT NOT NULL DEFAULT '[]',
    pinned INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_scenarios_account ON memory_scenarios(account_id);

-- 树洞人设卡（酒馆式）：账号级一张，设立一次持久扮演；字段宽松可空
CREATE TABLE IF NOT EXISTS treehole_persona (
    account_id TEXT PRIMARY KEY REFERENCES accounts(id),
    name TEXT NOT NULL DEFAULT '',
    personality TEXT NOT NULL DEFAULT '',   -- 性格
    speaking_style TEXT NOT NULL DEFAULT '', -- 说话风格
    relationship TEXT NOT NULL DEFAULT '',  -- 与用户的关系
    background TEXT NOT NULL DEFAULT '',    -- 背景设定
    custom_prompt TEXT NOT NULL DEFAULT '', -- 整段人设粘贴：非空时优先于上面模板字段
    updated_at TEXT NOT NULL
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
    _migrate_accounts_auth()
    _migrate_self_tables_account()
    _migrate_fragments_visibility()
    _migrate_wishes_visibility()
    _migrate_pair_secret_common_wishes()
    _migrate_image_url()
    _migrate_fragments_caption()
    _migrate_circles_persona()
    _migrate_wishes_matched_status()
    _migrate_nudges_plan_date()
    _migrate_food_brand()
    _migrate_treehole_image_and_custom_persona()
    _seed_food_nutrition()
    get_conn().commit()


def _migrate_food_brand() -> None:
    """存量迁移：food_nutrition / food_nutrition_staging 无 brand 列时重建表
    （SQLite 不能改唯一约束：UNIQUE(name) → UNIQUE(name, brand)，存量行 brand 补 ''）。"""
    conn = get_conn()
    for table in ("food_nutrition", "food_nutrition_staging"):
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        if not cols or "brand" in cols:
            continue
        if table.endswith("staging"):
            tail_old = ", source, verified, approvals, created_at"
            tail_new = tail_old
        else:
            tail_old = ", embedding"
            tail_new = tail_old
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        conn.executescript(SCHEMA)  # 按新结构建表（IF NOT EXISTS，其他表不受影响）
        conn.execute(
            f"""INSERT OR IGNORE INTO {table} (id, name, brand, kcal_per_100g,
                   protein_per_100g, fat_per_100g, cho_per_100g{tail_new})
                SELECT id, name, '', kcal_per_100g, protein_per_100g, fat_per_100g,
                       cho_per_100g{tail_old} FROM {table}_old"""
        )
        conn.execute(f"DROP TABLE {table}_old")


def _seed_food_nutrition() -> None:
    """食物成分表灌入（幂等）：表为空且 vendor JSON 存在时导入，name 向量同时算好存 BLOB。

    vendor 文件随仓库分发（scripts/import_food_nutrition.py 产出），部署自包含；
    已有数据的库启动时直接跳过，不重复灌入。
    未配置 embedding 或灌入失败时跳过（不阻塞启动）：表仍为空，下次启动自动重试。
    """
    conn = get_conn()
    if conn.execute("SELECT COUNT(*) AS c FROM food_nutrition").fetchone()["c"]:
        return
    assets = Path(__file__).resolve().parents[2] / "scripts" / "assets" / "food_nutrition.json"
    if not assets.is_file():
        return
    from .. import ai  # 延迟导入：ai 依赖 config，避免模块加载顺序成环

    rows = json.loads(assets.read_text(encoding="utf-8"))
    try:
        # 批量取向量再一次性落库：几次请求代替逐条几百次（分钟级 → 秒级），
        # 且中途失败不留半灌入状态（见 docs/BUG记录.md BUG-015）
        vecs = ai.embed_texts([r["name"] for r in rows])
        values = [
            (
                r["name"],
                r["kcal_per_100g"],
                r.get("protein_per_100g"),
                r.get("fat_per_100g"),
                r.get("cho_per_100g"),
                encode_embedding(v),
            )
            for r, v in zip(rows, vecs)
        ]
    except Exception as exc:  # noqa: BLE001
        logger.warning("食物成分表灌入跳过（不影响启动，下次启动自动重试）：%s", exc)
        return
    conn.executemany(
        """INSERT OR IGNORE INTO food_nutrition
           (name, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g, embedding)
           VALUES (?, ?, ?, ?, ?, ?)""",
        values,
    )


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


def _migrate_accounts_auth() -> None:
    """存量迁移：老库 accounts 无 username / password_hash 列时补列（账号系统重构）。

    老账号 username 为 NULL（不可登录，需重新注册）；密码 NULL=无密码账号。
    """
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    if not cols:
        return
    if "username" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN username TEXT")
    if "password_hash" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN password_hash TEXT")
    # 唯一索引在列就绪后建（SQLite 唯一索引允许多个 NULL）
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username ON accounts(username)"
    )


def _migrate_self_tables_account() -> None:
    """schema 重建（账号系统重构）：self 六表从 user_id（每圈身份）改为 account_id 归属。

    测试数据全清、不做存量迁移：老库检测到旧列结构直接 DROP 重建（幂等）。
    """
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(goals)").fetchall()]
    if cols and "account_id" not in cols:
        for table in (
            "nudge_blocks", "nudges", "calorie_entries", "expenses", "plan_items", "goals",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.executescript(SCHEMA)  # 重建（全部 CREATE IF NOT EXISTS，幂等）


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


def _migrate_treehole_image_and_custom_persona() -> None:
    """存量迁移：treehole_messages 补 image_url 列（树洞发图）；
    treehole_persona 补 custom_prompt 列（整段人设粘贴，非空时优先于模板字段）。"""
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(treehole_messages)").fetchall()]
    if cols and "image_url" not in cols:
        conn.execute("ALTER TABLE treehole_messages ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(treehole_persona)").fetchall()]
    if cols and "custom_prompt" not in cols:
        conn.execute("ALTER TABLE treehole_persona ADD COLUMN custom_prompt TEXT NOT NULL DEFAULT ''")


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


def _migrate_nudges_plan_date() -> None:
    """存量迁移（今日计划鞭策）：nudges 加 plan_date 列、goal_id 放宽为可空。

    SQLite 不能改列约束，只能建新表→拷贝→drop→rename；幂等：已有 plan_date 列直接跳过。
    存量行全部是目标鞭策（goal_id 非空），plan_date 补 NULL。
    """
    conn = get_conn()
    cols = [r[1] for r in conn.execute("PRAGMA table_info(nudges)").fetchall()]
    if not cols or "plan_date" in cols:
        return
    conn.executescript(
        """
        CREATE TABLE nudges_new (
            id TEXT PRIMARY KEY,
            goal_id TEXT,
            plan_date TEXT,
            from_account_id TEXT NOT NULL,
            to_account_id TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        INSERT INTO nudges_new (id, goal_id, plan_date, from_account_id, to_account_id, message, created_at)
            SELECT id, goal_id, NULL, from_account_id, to_account_id, message, created_at FROM nudges;
        DROP TABLE nudges;
        ALTER TABLE nudges_new RENAME TO nudges;
        """
    )


def reset_db() -> None:
    """仅测试用：清空全部数据。"""
    conn = get_conn()
    for table in ("treehole_persona", "memory_scenarios", "memory_atoms", "treehole_messages", "food_staging_approvals", "food_nutrition_staging", "self_sharing", "nudge_blocks", "nudges", "calorie_entries", "expenses", "plan_items", "goals", "chat_messages", "chat_threads", "common_wishes_cache", "pair_relationships", "user_profiles", "task_runs", "reports", "wishes", "comments", "likes", "push_subscriptions", "knowledge_items", "fragments", "memberships", "users", "circles", "accounts"):
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
