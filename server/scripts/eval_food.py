"""食物识别管线评测（优化清单第 1 项：改 prompt/匹配前先拿数字，改完回归对比）。

两层评测：

1. 匹配层（默认，离线只读，不烧 token）：识别名 → nutrition.match() 查成分表，
   度量命中率与命中值相对误差。种子用例的参照值全部钉在《中国食物成分表》真实行上
   （ref 值取自本地库实际数据，不是拍脑袋）。must 级必须 ≥95% 通过（退出码非 0），
   want 级是改进目标（别名/向量层的验收位），只报告不卡退出码。
2. 端到端（--e2e，需视觉配置 + 标注照片）：真实拍照 → recognize_calorie 全管线，
   度量名字准确率 / 克数 MAE / 热量误差。把照片放 scripts/eval/foods/、按
   scripts/eval/food_labels.json 格式标注（厨房秤称真实克数），零标注时跳过并给指引。

跑法：
    cd server && .venv-mac/bin/python scripts/eval_food.py             # 匹配层（离线）
    .venv-mac/bin/python scripts/eval_food.py --compare                # 看历史趋势
    .venv-mac/bin/python scripts/eval_food.py --e2e                    # 端到端（烧 token）

结果追加到 scripts/eval/food_match_history.jsonl（改 prompt / 加别名后必跑对比）。
注意：向量兜底层需要 EMBEDDING 配置——未配置时 LIKE 未中的用例计 vector_err 单列，
不冒充 miss 也不冒充通过。
"""
import argparse
import json
import os
import shutil
import statistics
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import ai  # noqa: E402
from app.db.database import get_conn, init_db  # noqa: E402
from app.services import nutrition  # noqa: E402

EVAL_DIR = os.path.join(os.path.dirname(__file__), "eval")
HISTORY = os.path.join(EVAL_DIR, "food_match_history.jsonl")
MUST_THRESHOLD = 0.95

# 匹配层种子：q=识别风格查询名；ref=成分表真实行值；tol=相对容差；lvl=must(基本盘)/want(改进目标)
# must 依据：当前 LIKE 层可精确/互含命中（钉死过的行）；want：别名/向量/组合菜的目标位。
SEED_CASES = [
    # ---- must：主食/蛋奶/肉/蔬菜基本盘（LIKE 确认可命中） ----
    {"q": "米饭", "ref": 118, "tol": 0.35, "lvl": "must", "note": "粳米饭(蒸) 118"},
    {"q": "鸡蛋", "ref": 139, "tol": 0.30, "lvl": "must", "note": "鸡蛋(代表值) 139"},
    {"q": "牛肉", "ref": 193, "tol": 0.35, "lvl": "must", "note": "牛肉 193"},
    {"q": "猪肉", "ref": 143, "tol": 0.60, "lvl": "must", "note": "部位差异大，瘦 143"},
    {"q": "牛奶", "ref": 66, "tol": 0.45, "lvl": "must", "note": "鲜牛奶 66"},
    {"q": "酸奶", "ref": 67, "tol": 0.40, "lvl": "must", "note": "低脂 64 / 调味 88"},
    {"q": "番茄", "ref": 15, "tol": 0.50, "lvl": "must", "note": "番茄[西红柿] 15"},
    {"q": "马铃薯", "ref": 81, "tol": 0.30, "lvl": "must", "note": "马铃薯[土豆、洋芋] 81"},
    {"q": "玉米", "ref": 112, "tol": 0.60, "lvl": "must", "note": "鲜 112 / 干 348 差异大"},
    {"q": "馒头", "ref": 223, "tol": 0.40, "lvl": "must", "note": "馒头(代表值) 223"},
    {"q": "豆腐", "ref": 67, "tol": 0.50, "lvl": "must", "note": "内酯 50 / 北 116"},
    {"q": "香蕉", "ref": 93, "tol": 0.35, "lvl": "must", "note": "香蕉[甘蕉] 93"},
    {"q": "苹果", "ref": 54, "tol": 0.30, "lvl": "must", "note": "品种 34-62"},
    {"q": "大白菜", "ref": 14, "tol": 0.40, "lvl": "must", "note": "白口 14 / 青口 12"},
    {"q": "生菜", "ref": 12, "tol": 0.50, "lvl": "must", "note": "生菜[叶用莴苣] 12"},
    {"q": "西兰花", "ref": 27, "tol": 0.40, "lvl": "must", "note": "西兰花[绿菜花] 27"},
    {"q": "带鱼", "ref": 108, "tol": 0.40, "lvl": "must", "note": "带鱼(切段) 108"},
    {"q": "红薯", "ref": 61, "tol": 0.50, "lvl": "want", "note": "甘薯（红心）[山芋、红薯]，别名在括号里"},
    # ---- must：确无此物（防误吸，命中即扣分）——补充脚本上线后饺子/包子已本地命中，改判应命中 ----
    {"q": "饺子", "ref": 230, "tol": 0.25, "lvl": "must", "note": "补充行 饺子（猪肉馅，煮）230"},
    {"q": "包子", "ref": 230, "tol": 0.25, "lvl": "must", "note": "补充行 包子（猪肉大葱馅）230"},
    {"q": "红茶", "ref": 1, "tol": 0.5, "lvl": "must", "note": "补充行 红茶 1（BUG-022 后本地兜住，不再走联网）"},
    # ---- want：别名/组合菜/匹配质量目标 ----
    {"q": "西红柿", "ref": 15, "tol": 0.50, "lvl": "want", "note": "别名在 [] 里，LIKE 剥掉括号匹配不上"},
    {"q": "土豆", "ref": 81, "tol": 0.35, "lvl": "want", "note": "同上：马铃薯[土豆、洋芋]"},
    {"q": "洋芋", "ref": 81, "tol": 0.35, "lvl": "want", "note": "同上"},
    {"q": "小西红柿", "ref": 25, "tol": 0.50, "lvl": "want", "note": "樱桃番茄[小西红柿] 25"},
    {"q": "黄瓜", "ref": 16, "tol": 0.50, "lvl": "want", "note": "表里无短名黄瓜行"},
    {"q": "面条", "ref": 110, "tol": 0.50, "lvl": "want", "note": "富强粉煮 107；现被互含吸到玉米面面条 350"},
    {"q": "炒鸡蛋", "ref": 143, "tol": 0.45, "lvl": "want", "note": "做法降级 + 高油守卫的博弈位"},
    {"q": "番茄炒蛋", "ref": 90, "tol": 0.80, "lvl": "want", "note": "组合菜，材料+做法语序"},
    {"q": "拍黄瓜", "ref": 30, "tol": 1.00, "lvl": "want", "note": "做法在前的凉菜"},
    {"q": "红烧肉", "ref": 500, "tol": 0.50, "lvl": "want", "note": "五花肉系，做法词守卫位"},
]


def _one_case(case: dict) -> dict:
    """跑一个匹配用例，返回 {q, ok, detail, layer}。异常（向量层未配置等）单列不算 miss。"""
    q = case["q"]
    try:
        hit = nutrition.match(q)
    except Exception as exc:  # noqa: BLE001 —— 向量层未配置等环境问题，与匹配质量分开算
        return {"q": q, "ok": None, "layer": "vector_err", "detail": f"{type(exc).__name__}: {exc}"[:80]}
    if hit is None:
        ok = case["ref"] is None  # 期望 miss 的用例：None 才对
        return {"q": q, "ok": ok, "layer": "miss",
                "detail": "miss（期望命中）" if case["ref"] is not None else "如预期 miss"}
    if case["ref"] is None:
        return {"q": q, "ok": False, "layer": hit.get("source", "?") + "/" + hit.get("via", "?"),
                "detail": f"误吸：{hit['name']} {hit['kcal_per_100g']}"}
    err = abs(float(hit["kcal_per_100g"]) - case["ref"]) / case["ref"]
    return {"q": q, "ok": err <= case["tol"],
            "layer": f"{hit.get('source', '?')}/{hit.get('via', '?')}",
            "detail": f"{hit['name']} {hit['kcal_per_100g']}（ref {case['ref']}，误差 {err:.0%}）"}


def run_match_layer() -> dict:
    init_db()
    results = [_one_case(c) for c in SEED_CASES]
    lv = {l: [r for r, c in zip(results, SEED_CASES) if c["lvl"] == l] for l in ("must", "want")}
    must_ok = sum(1 for r in lv["must"] if r["ok"])
    want_ok = sum(1 for r in lv["want"] if r["ok"])
    metrics = {
        "must_pass": f"{must_ok}/{len(lv['must'])}",
        "must_rate": round(must_ok / len(lv["must"]), 3) if lv["must"] else 1.0,
        "want_pass": f"{want_ok}/{len(lv['want'])}",
        "vector_err": sum(1 for r in results if r["ok"] is None),
        "total": len(results),
    }
    print(f"匹配层评测（{len(results)} 用例，库：{os.environ.get('DB_PATH', '默认 dev 库')}）")
    for label in ("must", "want"):
        for r, c in zip(results, SEED_CASES):
            if c["lvl"] != label:
                continue
            mark = "✓" if r["ok"] else ("?" if r["ok"] is None else "✗")
            print(f"  [{label}] {mark} {r['q']:<6} → {r['detail']}  [{r['layer']}]")
    print(f"must：{metrics['must_pass']}（阈值 {MUST_THRESHOLD:.0%}）  "
          f"want：{metrics['want_pass']}  向量层环境错误：{metrics['vector_err']}")
    if metrics["vector_err"]:
        print("提示：向量层需要 EMBEDDING 配置；未配置时 LIKE 未中的用例无法验证向量兜底。")
    return metrics


# ---------- 端到端（真实照片 + 厨房秤标注） ----------

EVAL_ACCOUNT = "eval_food_001"


def run_e2e() -> dict | None:
    labels_path = os.path.join(EVAL_DIR, "food_labels.json")
    if not os.path.isfile(labels_path):
        print("端到端跳过：没有标注文件。指引：照片放 scripts/eval/foods/，"
              "再按 scripts/eval/food_labels.example.json 建 food_labels.json"
              "（items 里 grams 用厨房秤称的真实值）。")
        return None
    labels = json.loads(open(labels_path, encoding="utf-8").read())
    if not settings_vision_ready():
        print("端到端跳过：未配置视觉模型（VISION_*）。")
        return None
    from app.config import settings
    from app.services import ledger as svc

    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO accounts (id, nickname, created_at) VALUES (?, '评测账号', ?)",
        (EVAL_ACCOUNT, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    name_acc, grams_errs, kcal_errs, cases = [], [], [], 0
    used_ids: list[str] = []
    for label in labels:
        img = os.path.join(EVAL_DIR, label["image"])
        if not os.path.isfile(img):
            print(f"  跳过（照片不存在）：{label['image']}")
            continue
        # 复制进 upload_dir 拿站内 URL（识别路由只认本站地址），评测完连同记录一起清
        stem = f"eval_{datetime.now().strftime('%H%M%S')}_{cases}"
        shutil.copy(img, settings.upload_dir / f"{stem}.jpg")
        cases += 1
        try:
            result = svc.recognize_calorie(
                EVAL_ACCOUNT, f"/api/uploads/{stem}.jpg", label.get("hint", ""))
        except Exception as exc:  # noqa: BLE001
            print(f"  识别失败 {label['image']}：{exc}")
            continue
        used_ids.append(result["entry"]["id"])
        exp_items = label["items"]
        got_items = result["entry"]["items"]
        for exp in exp_items:
            # 名字命中：标注名与识别名互含（番茄 vs 西红柿这类靠别名的留给匹配层评）
            hit = next((g for g in got_items
                        if exp["name"] in g["name"] or g["name"] in exp["name"]), None)
            name_acc.append(hit is not None)
            if hit and hit.get("grams") and exp.get("grams"):
                grams_errs.append(abs(hit["grams"] - exp["grams"]) / exp["grams"])
        exp_kcal = sum(i.get("kcal", 0) for i in exp_items)
        if exp_kcal > 0:
            kcal_errs.append(abs(result["entry"]["total_kcal"] - exp_kcal) / exp_kcal)
    # 清理：评测产生的 pending 记录与临时图（账号保留复用）
    for eid in used_ids:
        conn.execute("DELETE FROM calorie_entries WHERE id = ?", (eid,))
    for f in os.listdir(settings.upload_dir):
        if f.startswith("eval_"):
            try:
                os.remove(settings.upload_dir / f)
            except OSError:
                pass
    conn.commit()
    metrics = {
        "cases": cases,
        "name_acc": round(sum(name_acc) / len(name_acc), 3) if name_acc else None,
        "grams_mae": round(statistics.mean(grams_errs), 3) if grams_errs else None,
        "kcal_mae": round(statistics.mean(kcal_errs), 3) if kcal_errs else None,
    }
    print(f"端到端评测：{cases} 例，名字准确率 {metrics['name_acc']}，"
          f"克数 MAE {metrics['grams_mae']}，热量 MAE {metrics['kcal_mae']}")
    return metrics


def settings_vision_ready() -> bool:
    from app.config import settings
    return bool(settings.VISION_MODEL and settings.VISION_API_KEY)


def save_history(mode: str, metrics: dict) -> None:
    os.makedirs(EVAL_DIR, exist_ok=True)
    with open(HISTORY, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                            "mode": mode, **metrics}, ensure_ascii=False) + "\n")


def show_history() -> None:
    if not os.path.isfile(HISTORY):
        print("还没有历史记录。")
        return
    lines = [json.loads(l) for l in open(HISTORY, encoding="utf-8") if l.strip()]
    print(f"评测历史（{len(lines)} 次，匹配层）：")
    for h in [l for l in lines if l["mode"] == "match"]:
        print(f"  {h['ts']}  must {h.get('must_pass')}  want {h.get('want_pass')}"
              f"  vector_err {h.get('vector_err')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="食物识别管线评测")
    parser.add_argument("--e2e", action="store_true", help="端到端（需视觉配置+标注照片）")
    parser.add_argument("--compare", action="store_true", help="只看历史趋势")
    args = parser.parse_args()
    if args.compare:
        show_history()
        return
    if args.e2e:
        m = run_e2e()
        if m is not None:
            save_history("e2e", m)
        return
    metrics = run_match_layer()
    save_history("match", metrics)
    show_history()
    if metrics["must_rate"] < MUST_THRESHOLD:
        print("❌ must 通过率低于阈值")
        sys.exit(1)
    print("✅ must 通过率达标")


if __name__ == "__main__":
    main()
