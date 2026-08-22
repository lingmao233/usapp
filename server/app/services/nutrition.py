"""菜品/原料名 → 成分表营养行匹配：LIKE 归一优先，向量余弦兜底。

- 主匹配：名称归一（去括号备注、去空白）后做子串互含匹配。
  不走 FTS5——默认分词器按空格/标点切词，对中文整词基本失效
- 兜底：查询名与 food_nutrition.name 的文本向量做余弦（表向量灌入时已算好存 BLOB），
  相似度 < 0.75 视为成分表没有这道菜，返回 None（调用方回退模型估值）
- 测试里 embedding 是 tests/fakes.py 的确定性字符 n-gram 哈希桩，两级匹配结果都可测

用户共建信任管线（staging）：
- match 两级都不中时再查预数据库 food_nutrition_staging（LIKE 归一），命中标 source=staging，
  数据「不确定真假」，UI 显示「待核实」
- 用户手动添加（source=user）异步联网核验：与联网值差 ≤50% verified=1，离谱保持 0
- 查表未命中联网搜到的（source=web）落库即 verified=1，item 标 web_pending 待用户认可
- 认可（确认入账）按 (staging_id, account_id) 去重计数，approvals ≥ 3 晋升正式表并删 staging 行
"""
import re
from datetime import datetime

from fastapi import HTTPException

from .. import ai
from ..db.database import cosine, decode_embedding, encode_embedding, get_conn
from . import selfshare

VECTOR_THRESHOLD = 0.75  # 向量命中下限
PROMOTE_APPROVALS = 3  # 晋升正式表所需的不同账号认可数
VERIFY_TOLERANCE = 0.5  # 联网核验容差：与用户值差 >50% 视为离谱（保持待核实）
MAX_KCAL_PER_100G = 1000.0  # 每 100g 热量物理上限（与导入清洗口径一致）

# ---------- 联网值合理性校验（BUG-022：红茶 294 kcal/100g 错值落全局库污染所有账号） ----------

KJ_FACTOR = 4.184  # kJ/kcal 换算系数（联网把 kJ 当 kcal 的典型误报：294kJ ≈ 70kcal）
DRINK_MAX_KCAL_PER_100G = 80.0  # 饮品先验上限（含糖饮料 ~40-65；奶茶类放宽见下）
MILKY_DRINK_MAX_KCAL_PER_100G = 150.0  # 奶茶/拿铁这类「固体感」饮品的放宽上限
# 饮品判断用「结尾词」：茶/水/汁/汤收尾才算饮品——中缀不会误伤（茶肠/茶树菇/水果都不以此结尾）
_DRINK_SUFFIXES = ("茶", "水", "汁", "汤", "咖啡", "可乐", "汽水", "奶昔", "豆浆", "椰汁")
_MILKY_DRINK_RE = re.compile(r"奶茶|奶盖|拿铁|摩卡|星冰乐|厚乳")

# 做法词降级：识别名带做法（炒鸡蛋/红烧肉）全名查不到时，剥掉做法词再匹配通称行（→鸡蛋/肉）。
# 只在词首剥；卤/酱/拌这类品类字不在名单里（「卤蛋」绝不能剥成「蛋」）；拍（拍黄瓜）是
# 凉菜做法，剥掉后按黄瓜计价是合理近似
_COOKING_PREFIX_RE = re.compile(
    r"^(?:红烧|清炒|白灼|凉拌|爆炒|油焖|糖醋|干煸|香煎|油煎|水煮|清蒸|炭烤|干锅|铁板|蒜蓉|清炖"
    r"|炒|煎|炸|蒸|煮|炖|烤|烩|焖|拍)"
)
# 高油做法：查询带这些做法时，不接受不含做法信息的通称行（炒鸡蛋≠煮鸡蛋，热量差一截），
# 让给降级/联网拿做法级单价
_OILY_COOKING_RE = re.compile(r"红烧|糖醋|油焖|干煸|香煎|油煎|炭烤|干锅|铁板|炒|煎|炸")

# 括号备注（肥瘦/品牌/别名/罐头做法等）与空白在匹配时一律忽略
_BRACKET_RE = re.compile(r"[（(\[][^（）()\[\]]*[)）\]]")
_WS_RE = re.compile(r"\s+")

# 方括号别名提取：《中国食物成分表》把别名写在 [] 里（马铃薯[土豆、洋芋]、番茄[西红柿]），
# normalize 会把括号整体剥掉 → LIKE 层永远看不到别名。厂商数据还有半角开全角关的破损
# 括号（[西红柿】），一并覆盖。圆括号是备注/部位/做法，不算别名。
_ALIAS_BRACKET_RE = re.compile(r"[\[【]([^()\[\]【】]*)[\]】]")
_ALIAS_SPLIT_RE = re.compile(r"[、，,/；;]")


def _alias_keys(name: str) -> list[str]:
    """行名方括号里的别名键（归一后去重）：马铃薯[土豆、洋芋] → [土豆, 洋芋]。"""
    keys: list[str] = []
    for group in _ALIAS_BRACKET_RE.findall(str(name or "")):
        for piece in _ALIAS_SPLIT_RE.split(group):
            k = normalize(piece)
            if k and k not in keys:
                keys.append(k)
    return keys


def normalize(name: str) -> str:
    """匹配归一：去括号备注、去全部空白。"""
    return _WS_RE.sub("", _BRACKET_RE.sub("", str(name or "")))


def _row_dict(row, via: str) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "brand": row["brand"] if "brand" in row.keys() else "",
        "kcal_per_100g": row["kcal_per_100g"],
        "protein_per_100g": row["protein_per_100g"],
        "fat_per_100g": row["fat_per_100g"],
        "cho_per_100g": row["cho_per_100g"],
        "via": via,
        "source": "table",
    }


def _brand_groups(rows, brand: str):
    """品牌优先级分组：识别出品牌 → 该品牌行 > 通用行 > 其他品牌行（兜底）；
    没识别出品牌 → 通用行 > 品牌行（笼统查询优先拿代表值，实在没有再借品牌行）。"""
    b = normalize(brand)
    branded = [r for r in rows if b and normalize(r["brand"] if "brand" in r.keys() else "") == b]
    generic = [r for r in rows if not (r["brand"] if "brand" in r.keys() else "")]
    others = [r for r in rows if r not in branded and r not in generic]
    groups = [g for g in (branded, generic) if g]
    if others:
        groups.append(others)
    return groups


def _like_rows(rows, name: str):
    """在一组行里做归一子串互含匹配：完全一致优先于互含；并列取原名最短（备注最少）。
    行名方括号里的别名（马铃薯[土豆、洋芋]）与主名同级参与匹配——别名精确命中同 rank 0。
    单字护栏（双向）：查询或键任一不足两字不做互含扩展——查询侧防「茶」吸「茶肠」，
    键侧防「小西红柿」吸「柿」（74 vs 25，实测 196% 误差）。
    查询带高油做法（炒鸡蛋）时，原名含同类做法信息的行优先（煎荷包蛋→荷包蛋(油煎)）。"""
    q = normalize(name)
    if not q:
        return None
    q_oily = _OILY_COOKING_RE.search(q)
    best = None
    for r in rows:
        keys = [normalize(r["name"])] + _alias_keys(r["name"])
        exact = any(q == k for k in keys if k)
        best_ratio = None
        if not exact:
            if len(q) < 2:
                continue  # 单字查询只收完全一致（护栏，防「茶」→「茶肠」）
            for k in keys:
                if not k or len(k) < 2:
                    continue  # 单字键不互含（护栏，防「小西红柿」→「柿」）
                if q in k or k in q:
                    ratio = min(len(q), len(k)) / max(len(q), len(k))
                    if best_ratio is None or ratio > best_ratio:
                        best_ratio = ratio
            if best_ratio is None:
                continue
        rank, ratio = (0, 1.0) if exact else (1, best_ratio)
        # 做法偏好：查询带高油做法而候选行原名（含括号备注）没有做法信息 → 罚一档
        cook_penalty = 1 if (q_oily and not _OILY_COOKING_RE.search(r["name"])) else 0
        key = (rank, cook_penalty, -ratio, len(r["name"]), r["name"], r["id"])
        if best is None or key < best[0]:
            best = (key, r)
    return best[1] if best else None


def _like_match(name: str, brand: str = "") -> dict | None:
    """归一子串互含，按品牌分组优先级依次尝试（品牌行 > 通用行 > 其他品牌行）。"""
    rows = get_conn().execute(
        "SELECT id, name, brand, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g"
        " FROM food_nutrition"
    ).fetchall()
    for group in _brand_groups(rows, brand):
        hit = _like_rows(group, name)
        if hit is not None:
            return _row_dict(hit, "like")
    return None


def _vector_match(name: str, brand: str = "") -> dict | None:
    """向量兜底：查询名与表名余弦（按品牌分组依次尝试），最佳相似度 ≥ VECTOR_THRESHOLD 才算命中。"""
    vec = ai.embed_text(str(name or ""))
    rows = get_conn().execute(
        "SELECT id, name, brand, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g,"
        " embedding FROM food_nutrition WHERE embedding IS NOT NULL"
    ).fetchall()
    for group in _brand_groups(rows, brand):
        best_row, best_sim = None, 0.0
        for r in group:
            emb = decode_embedding(r["embedding"])
            if emb is None:
                continue
            sim = cosine(vec, emb)
            if sim > best_sim:
                best_row, best_sim = r, sim
        if best_row is not None and best_sim >= VECTOR_THRESHOLD:
            return _row_dict(best_row, "vector")
    return None


def match(name: str, brand: str = "") -> dict | None:
    """菜名(+品牌) → 最匹配的营养行；正式表两级都不中再查 staging，全不中返回 None。

    命中字典带 source：table=正式成分表；staging=预数据库（不确定真假，UI 标「待核实」，
    另带 staging_id 供确认入账时计认可）。brand 为空时按通用款匹配。
    高油做法守卫：查询带炒/煎/炸/红烧等高油做法而命中行原名无做法信息 → 视为未命中，
    让给降级/联网拿做法级单价（炒鸡蛋绝不按煮鸡蛋计价）。
    """
    hit = _like_match(name, brand) or _vector_match(name, brand)
    if hit is not None and _oily_mismatch(name, hit["name"]):
        hit = None
    if hit is None:
        hit = _cooking_fallback(name, brand)
        if hit is not None and _oily_mismatch(name, hit["name"]):
            hit = None
    return hit or _staging_match(name, brand)


def _oily_mismatch(query: str, hit_name: str) -> bool:
    """高油做法守卫：查询（归一后）含高油做法，而命中行原名（含括号备注）不含做法信息。"""
    return bool(_OILY_COOKING_RE.search(normalize(query))) and not _OILY_COOKING_RE.search(hit_name)


def _cooking_fallback(name: str, brand: str = "") -> dict | None:
    """做法降级：识别名带做法词（炒鸡蛋/红烧肉）全名查不到时，剥词首做法再 LIKE 通称行
    （炒鸡蛋→鸡蛋）。剥完与原名相同或不足两字则不动。"""
    q = normalize(name)
    stripped = _COOKING_PREFIX_RE.sub("", q)
    if not stripped or stripped == q or len(stripped) < 2:
        return None
    hit = _like_match(stripped, brand)
    if hit is not None:
        hit["via"] = "cooking_fallback"  # 可观测：标明是剥了做法才命中的
    return hit


# ---------- 用户共建信任管线（staging 预数据库） ----------


def is_drink_name(name: str) -> bool:
    """品类先验：结尾词判断是否饮品（「茶肠/茶树菇/茶叶蛋」不以饮品词结尾，不会误伤）。"""
    n = normalize(name)
    return bool(n) and n.endswith(_DRINK_SUFFIXES)


def drink_prior_max(name: str) -> float:
    """饮品先验上限：奶茶/拿铁这类「固体感」饮品放宽（奶盖厚乳可到 ~110+）。"""
    return MILKY_DRINK_MAX_KCAL_PER_100G if _MILKY_DRINK_RE.search(str(name or "")) \
        else DRINK_MAX_KCAL_PER_100G


def sanitize_web_value(name: str, kcal_per_100g, model_per_100g=None) -> tuple[float, str] | None:
    """联网值入库前清洗：可信返回 (kcal_per_100g, 备注)，不可信返回 None（拒绝落库）。

    1. kJ 误报修正（交叉校验的保留形态）：联网值 ≈ 模型估值 × 4.184（±20%）→ 判为把
       kJ 当 kcal，折算回 kcal（294kJ ≈ 70kcal 这类单位误报）
    2. 品类先验：饮品名超先验上限 → 拒绝（红茶 294 即此例：无糖茶 ~1-4、含糖饮料 ≤65、
       奶茶类 ≤150，再高必是错值）
    注：曾考虑「与模型估值差 >10 倍即拒」的对称交叉校验——实测不可靠：模型对未知食物
    高估/低估都常见（能量棒估 1 kcal/100g、果汁估 565），对称倍差会把正确的联网值一并
    拒掉（test_nutrition_staging 的能量棒/花生酱用例即反例）。模型只配当 kJ 折算的
    参照，拒绝判定交给品类先验；非饮品错值靠管理面板软删治理。
    """
    try:
        kcal = float(kcal_per_100g)
    except (TypeError, ValueError):
        return None
    if not 0 < kcal <= MAX_KCAL_PER_100G:
        return None
    note = ""
    try:
        ref = float(model_per_100g) if model_per_100g is not None else None
    except (TypeError, ValueError):
        ref = None
    if ref and ref > 0:
        ratio = kcal / ref
        if KJ_FACTOR * 0.8 <= ratio <= KJ_FACTOR * 1.2:
            kcal = kcal / KJ_FACTOR
            note = f"kJ 误报折算：{kcal_per_100g} → {round(kcal, 1)}"
    if is_drink_name(name) and kcal > drink_prior_max(name):
        return None
    return round(kcal, 1), note


def _staging_match(name: str, brand: str = "") -> dict | None:
    """staging 匹配：只走 LIKE 归一互含（staging 行无向量），品牌分组优先级同正式表；
    软删行（管理面板删掉的错值）不参与匹配。"""
    rows = get_conn().execute(
        "SELECT id, name, brand, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g"
        " FROM food_nutrition_staging WHERE deleted = 0"
    ).fetchall()
    for group in _brand_groups(rows, brand):
        hit = _like_rows(group, name)
        if hit is not None:
            out = _row_dict(hit, "like")
            out["source"] = "staging"
            out["staging_id"] = hit["id"]
            return out
    return None


def _staging_dict(row) -> dict:
    d = dict(row)
    d["verified"] = bool(d["verified"])
    if "deleted" in d:
        d["deleted"] = bool(d["deleted"])
    return d


def _check_staging_values(name: str, kcal_per_100g, macros: tuple, brand: str = "") -> tuple[str, str, float, list]:
    """名称/品牌/热量/宏量校验：名字非空，品牌可空（≤30 字），kcal ∈ (0, 1000]，宏量可空但须在 [0, 100]。"""
    name = str(name or "").strip()[:50]
    if not name:
        raise HTTPException(status_code=400, detail="食物名称必填")
    brand = str(brand or "").strip()[:30]
    try:
        kcal = round(float(kcal_per_100g), 1)
    except (TypeError, ValueError):
        kcal = 0.0
    if kcal <= 0 or kcal > MAX_KCAL_PER_100G:
        raise HTTPException(status_code=400, detail="每 100g 热量必填且需在 0-1000 kcal 之间")
    out = []
    for v in macros:
        if v is None or v == "":
            out.append(None)
            continue
        try:
            f = round(float(v), 1)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="宏量营养素必须是数字（克/100g）")
        if f < 0 or f > 100:
            raise HTTPException(status_code=400, detail="宏量营养素需在 0-100 g/100g 之间")
        out.append(f)
    return name, brand, kcal, out


def staging_id_by_name(conn, name: str, brand: str = "") -> int | None:
    """按（名，品牌）查未软删的 staging 行 id；不存在返回 None。"""
    row = conn.execute(
        "SELECT id FROM food_nutrition_staging WHERE name = ? AND brand = ? AND deleted = 0",
        (name, brand),
    ).fetchone()
    return row["id"] if row else None


def upsert_staging_web(name: str, web: dict, brand: str = "",
                       model_per_100g=None) -> dict | None:
    """联网搜到的食物写入 staging（source=web，落库即 verified=1）；同名同品牌已存在直接复用。

    入库前先过 sanitize_web_value 清洗（交叉校验 + 品类先验，见 BUG-022）：不可信返回
    None 不落库；kJ 误报折算后落折算值。命中曾被管理面板软删的同名行时以新值复活
    （deleted=0）。返回 {"staging_id", "kcal_per_100g", "note"}。
    """
    cleaned = sanitize_web_value(name, web.get("kcal_per_100g"), model_per_100g)
    if cleaned is None:
        return None
    kcal, note = cleaned
    conn = get_conn()
    name, brand, _, macros = _check_staging_values(
        name, kcal,
        (web.get("protein_per_100g"), web.get("fat_per_100g"), web.get("cho_per_100g")),
        brand,
    )
    conn.execute(
        """INSERT INTO food_nutrition_staging
           (name, brand, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g,
            source, verified, approvals, created_at, deleted)
           VALUES (?, ?, ?, ?, ?, ?, 'web', 1, 0, ?, 0)
           ON CONFLICT(name, brand) DO UPDATE SET
             kcal_per_100g = excluded.kcal_per_100g,
             protein_per_100g = excluded.protein_per_100g,
             fat_per_100g = excluded.fat_per_100g,
             cho_per_100g = excluded.cho_per_100g,
             source = 'web', verified = 1, deleted = 0, updated_by = '', updated_at = NULL
           WHERE food_nutrition_staging.deleted = 1""",
        (name, brand, kcal, macros[0], macros[1], macros[2],
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    # 活行已存在时不覆盖（沿用原语义：用户手动加的正确值优先），回读实际生效值
    row = conn.execute(
        "SELECT id, kcal_per_100g FROM food_nutrition_staging WHERE name = ? AND brand = ?",
        (name, brand),
    ).fetchone()
    if row is None:  # 理论不可达（刚 upsert 完）；防御性兜底
        return None
    return {"staging_id": row["id"], "kcal_per_100g": float(row["kcal_per_100g"]), "note": note}


def add_staging_food(
    account_id: str,
    name: str,
    kcal_per_100g,
    protein_per_100g=None,
    fat_per_100g=None,
    cho_per_100g=None,
    brand: str = "",
) -> dict:
    """用户手动添加 → staging（source=user，verified=0 待核验）；同名同品牌去重幂等。

    联网核验由调用方（API 层）异步触发 verify_staging_food。
    """
    conn = get_conn()
    selfshare.require_account(conn, account_id)
    name, brand, kcal, macros = _check_staging_values(
        name, kcal_per_100g, (protein_per_100g, fat_per_100g, cho_per_100g), brand
    )
    existing = staging_id_by_name(conn, name, brand)
    if existing is not None:
        row = conn.execute(
            "SELECT * FROM food_nutrition_staging WHERE id = ?", (existing,)
        ).fetchone()
        return {"food": _staging_dict(row), "created": False}
    # 撞上软删行（管理面板删过的同名错值）→ 以用户这次给的值复活该行
    conn.execute(
        """INSERT INTO food_nutrition_staging
           (name, brand, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g,
            source, verified, approvals, created_at, deleted)
           VALUES (?, ?, ?, ?, ?, ?, 'user', 0, 0, ?, 0)
           ON CONFLICT(name, brand) DO UPDATE SET
             kcal_per_100g = excluded.kcal_per_100g,
             protein_per_100g = excluded.protein_per_100g,
             fat_per_100g = excluded.fat_per_100g,
             cho_per_100g = excluded.cho_per_100g,
             source = 'user', verified = 0, approvals = 0, deleted = 0, updated_by = ''""",
        (name, brand, kcal, macros[0], macros[1], macros[2],
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM food_nutrition_staging WHERE name = ? AND brand = ?", (name, brand)
    ).fetchone()
    return {"food": _staging_dict(row), "created": True}


def verify_staging_food(staging_id: int) -> bool | None:
    """联网核验 staging 行：与联网值差 ≤50% → verified=1；离谱/搜不到保持 0（待核实）。

    返回 True=核验通过，False=离谱，None=无法核验（未开启/搜索失败/行不存在）。
    只升不降：已 verified=1 的行（如 web 来源）不再改。
    """
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM food_nutrition_staging WHERE id = ? AND deleted = 0", (staging_id,)
    ).fetchone()
    if row is None:
        return None
    web = ai.web_search_food(row["name"], row["brand"] if "brand" in row.keys() else "")
    if web is None:
        return None
    try:
        web_kcal = float(web["kcal_per_100g"])
    except (TypeError, ValueError, KeyError):
        return None
    user_kcal = float(row["kcal_per_100g"])
    if user_kcal <= 0:
        return None
    if abs(web_kcal - user_kcal) / user_kcal > VERIFY_TOLERANCE:
        return False  # 离谱：保持 verified=0，UI 标「待核实」
    conn.execute(
        "UPDATE food_nutrition_staging SET verified = 1 WHERE id = ?", (staging_id,)
    )
    conn.commit()
    return True


def approve_staging(staging_id, account_id: str) -> dict:
    """认可一条 staging 数据：同一账号对同一食物只计一次；满 PROMOTE_APPROVALS 晋升正式表。

    返回 {"counted": bool, "approvals": int, "promoted": bool}；行不存在/已软删返回 None。
    """
    conn = get_conn()
    if not isinstance(staging_id, int) or isinstance(staging_id, bool):
        return None
    row = conn.execute(
        "SELECT * FROM food_nutrition_staging WHERE id = ? AND deleted = 0", (staging_id,)
    ).fetchone()
    if row is None:
        return None
    cur = conn.execute(
        """INSERT OR IGNORE INTO food_staging_approvals (staging_id, account_id, created_at)
           VALUES (?, ?, ?)""",
        (staging_id, account_id, datetime.now().isoformat(timespec="seconds")),
    )
    counted = cur.rowcount > 0
    if counted:
        conn.execute(
            "UPDATE food_nutrition_staging SET approvals = approvals + 1 WHERE id = ?",
            (staging_id,),
        )
    approvals = conn.execute(
        "SELECT approvals FROM food_nutrition_staging WHERE id = ?", (staging_id,)
    ).fetchone()["approvals"]
    promoted = False
    if approvals >= PROMOTE_APPROVALS:
        promoted = _promote_staging(conn, row)
    conn.commit()
    return {"counted": counted, "approvals": approvals, "promoted": promoted}


def _promote_staging(conn, row) -> bool:
    """staging → 正式 food_nutrition：算 name 向量灌入（同名同品牌已存在则跳过插入），随后删除 staging 行。"""
    conn.execute(
        """INSERT OR IGNORE INTO food_nutrition
           (name, brand, kcal_per_100g, protein_per_100g, fat_per_100g, cho_per_100g, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            row["name"],
            row["brand"] if "brand" in row.keys() else "",
            row["kcal_per_100g"],
            row["protein_per_100g"],
            row["fat_per_100g"],
            row["cho_per_100g"],
            encode_embedding(ai.embed_text(row["name"])),
        ),
    )
    conn.execute("DELETE FROM food_staging_approvals WHERE staging_id = ?", (row["id"],))
    conn.execute("DELETE FROM food_nutrition_staging WHERE id = ?", (row["id"],))
    return True


# ---------- staging 管理面板（共建库治理：错值可查/改/删） ----------


def list_staging(query: str = "", page: int = 1, page_size: int = 50,
                 include_deleted: bool = False) -> dict:
    """staging 列表（管理面板）：按名称/品牌模糊搜，含认可数与核验状态；软删行默认不列。"""
    conn = get_conn()
    where = "WHERE deleted = 0" if not include_deleted else ""
    args: list = []
    q = str(query or "").strip()
    if q:
        where = (where + " AND" if where else "WHERE") + " (name LIKE ? OR brand LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM food_nutrition_staging {where}", args
    ).fetchone()["c"]
    page = max(1, int(page or 1))
    page_size = min(100, max(10, int(page_size or 50)))
    rows = conn.execute(
        f"""SELECT * FROM food_nutrition_staging {where}
            ORDER BY deleted, rowid DESC LIMIT ? OFFSET ?""",
        args + [page_size, (page - 1) * page_size],
    ).fetchall()
    return {"items": [_staging_dict(r) for r in rows], "total": total,
            "page": page, "page_size": page_size}


def update_staging_food(account_id: str, staging_id: int, name=None, brand=None,
                        kcal_per_100g=None, protein_per_100g=None, fat_per_100g=None,
                        cho_per_100g=None, verified=None) -> dict:
    """管理面板改 staging 行（软删行也能改——改完自动复活）。传什么改什么，记录操作者。

    改名/改品牌撞上已有活行的 UNIQUE(name, brand) → 409 提示重名。
    """
    conn = get_conn()
    selfshare.require_account(conn, account_id)
    if not isinstance(staging_id, int) or isinstance(staging_id, bool):
        raise HTTPException(status_code=400, detail="staging_id 不合法")
    row = conn.execute(
        "SELECT * FROM food_nutrition_staging WHERE id = ?", (staging_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="共建库行不存在")
    fields, values = [], []
    if any(v is not None for v in (name, brand, kcal_per_100g,
                                   protein_per_100g, fat_per_100g, cho_per_100g)):
        # 任一名称/营养字段给了就整套过校验（缺项沿用现值），保证行内数据自洽
        n, b, k, macros = _check_staging_values(
            name if name is not None else row["name"],
            kcal_per_100g if kcal_per_100g is not None else row["kcal_per_100g"],
            (protein_per_100g if protein_per_100g is not None else row["protein_per_100g"],
             fat_per_100g if fat_per_100g is not None else row["fat_per_100g"],
             cho_per_100g if cho_per_100g is not None else row["cho_per_100g"]),
            brand if brand is not None else row["brand"],
        )
        clash = conn.execute(
            "SELECT id FROM food_nutrition_staging WHERE name = ? AND brand = ? AND id != ?"
            " AND deleted = 0", (n, b, staging_id),
        ).fetchone()
        if clash:
            raise HTTPException(status_code=409, detail="已有同名同品牌的活行，先处理那一行")
        fields += ["name = ?", "brand = ?", "kcal_per_100g = ?",
                   "protein_per_100g = ?", "fat_per_100g = ?", "cho_per_100g = ?"]
        values += [n, b, k, macros[0], macros[1], macros[2]]
    if verified is not None:
        fields.append("verified = ?")
        values.append(1 if verified else 0)
    if not fields:
        raise HTTPException(status_code=400, detail="没有改动")
    fields += ["updated_by = ?", "updated_at = ?", "deleted = 0"]  # 编辑即复活（治理完直接生效）
    values += [account_id, datetime.now().isoformat(timespec="seconds"), staging_id]
    conn.execute(f"UPDATE food_nutrition_staging SET {', '.join(fields)} WHERE id = ?", values)
    conn.commit()
    return {"food": _staging_dict(conn.execute(
        "SELECT * FROM food_nutrition_staging WHERE id = ?", (staging_id,)).fetchone())}


def soft_delete_staging(account_id: str, staging_id: int) -> dict:
    """管理面板软删 staging 行：匹配/回填/认可即刻跳过，认可记录一并清（复活从零计）。

    保留行体（不物理删）留审计痕迹；UNIQUE(name, brand) 占位，之后联网再搜到同名
    会以新值复活该行（upsert_staging_web 的 deleted=1 分支）。
    """
    conn = get_conn()
    selfshare.require_account(conn, account_id)
    if not isinstance(staging_id, int) or isinstance(staging_id, bool):
        raise HTTPException(status_code=400, detail="staging_id 不合法")
    row = conn.execute(
        "SELECT id, deleted FROM food_nutrition_staging WHERE id = ?", (staging_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="共建库行不存在")
    if not row["deleted"]:
        conn.execute("DELETE FROM food_staging_approvals WHERE staging_id = ?", (staging_id,))
    conn.execute(
        """UPDATE food_nutrition_staging
           SET deleted = 1, approvals = 0, updated_by = ?, updated_at = ? WHERE id = ?""",
        (account_id, datetime.now().isoformat(timespec="seconds"), staging_id),
    )
    conn.commit()
    return {"status": "deleted"}
