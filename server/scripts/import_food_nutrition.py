"""《中国食物成分表》营养数据导入：拉取 json_data/ → 清洗 → 产出 vendor JSON。

数据源：https://github.com/Sanotsu/china-food-composition-data 的 json_data/ 目录
（OCR 转换，含脏数据：错位双数值如 "Fe": "24 0.4"、kJ 串到 kcal 列、空值等），
本脚本负责清洗校验，产出单一 vendor 文件 server/scripts/assets/food_nutrition.json
（随仓库分发，部署自包含，运行期不再依赖外部仓库）。

跑法：
  cd server && .venv-mac/bin/python scripts/import_food_nutrition.py            # 在线拉取（需 git）
  cd server && .venv-mac/bin/python scripts/import_food_nutrition.py --src /path/to/json_data

清洗口径（每行必须全部通过才保留）：
- foodName 去全部空白（OCR 常在中文字间插空格），空名丢弃
- energyKCal 必须是纯数字（"-" / 空 / 含空格的多段错位值一律丢弃），且 0 < kcal ≤ 1000
  （纯脂肪约 900 封顶，超出的基本是 kJ 串列）
- protein / fat / CHO：空或 "-" 记为 None；必须是纯数字且 ≤ 100（每 100g 含量），
  出现 "20.2 30.4" 这类错位双数值整行丢弃
- 宏量营养素合计 > 110g/100g 视为错位行丢弃
- 按清洗后名称去重（同名保留先出现的），保证导入侧 name 唯一
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/Sanotsu/china-food-composition-data"
ASSETS_PATH = Path(__file__).resolve().parent / "assets" / "food_nutrition.json"

# 纯数字（整数/小数）；任何含空格、多段、字母的值都判非法（OCR 错位特征）
_NUM_RE = re.compile(r"\d+(\.\d+)?")
_WS_RE = re.compile(r"\s+")

MAX_KCAL_PER_100G = 1000.0  # 每 100g 热量物理上限（纯脂肪 ~900）
MAX_MACRO_PER_100G = 100.0  # 单项宏量营养素每 100g 上限
MAX_MACRO_SUM = 110.0  # 三项合计上限（留 10g 容差）


def new_stats() -> dict:
    """清洗/导入统计计数器（键固定，便于测试与 main 打印口径一致）。"""
    return {
        "total": 0, "not_object": 0, "empty_name": 0, "bad_kcal": 0,
        "kcal_out_of_range": 0, "bad_macro": 0, "macro_out_of_range": 0,
        "macro_sum_over": 0, "duplicate_name": 0,
    }


def fetch_json_data(workdir: Path) -> Path:
    """git sparse checkout 只拉 json_data/ 目录，返回其路径。"""
    repo = workdir / "cfcd"
    subprocess.run(
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", REPO_URL, str(repo)],
        check=True,
    )
    subprocess.run(["git", "-C", str(repo), "sparse-checkout", "set", "json_data"], check=True)
    return repo / "json_data"


def parse_number(raw) -> float | None:
    """严格数值解析：空 / "-" / 非法值返回 None（调用方按字段决定丢弃还是记缺失）。"""
    s = str(raw if raw is not None else "").strip()
    if s in ("", "-", "—"):
        return None
    if not _NUM_RE.fullmatch(s):
        return None
    return float(s)


def clean_name(raw) -> str:
    """foodName 归一：去全部空白（OCR 字间空格），保留括号备注（肥瘦/品牌影响营养值）。"""
    return _WS_RE.sub("", str(raw or ""))


def clean_records(records: list[dict], stats: dict) -> list[dict]:
    """清洗一个文件的记录列表，返回合格行；stats 就地累计丢弃原因计数。"""
    out = []
    for r in records:
        stats["total"] += 1
        if not isinstance(r, dict):
            stats["not_object"] += 1
            continue
        name = clean_name(r.get("foodName"))
        if not name:
            stats["empty_name"] += 1
            continue
        kcal = parse_number(r.get("energyKCal"))
        if kcal is None:
            stats["bad_kcal"] += 1
            continue
        if not 0 < kcal <= MAX_KCAL_PER_100G:
            stats["kcal_out_of_range"] += 1
            continue
        macros = {}
        bad = False
        for src, dst in (("protein", "protein_per_100g"), ("fat", "fat_per_100g"), ("CHO", "cho_per_100g")):
            v = parse_number(r.get(src))
            if v is None:
                s = str(r.get(src) if r.get(src) is not None else "").strip()
                if s not in ("", "-", "—"):  # 非法数值（错位双数值等）整行丢弃
                    stats["bad_macro"] += 1
                    bad = True
                    break
            elif v > MAX_MACRO_PER_100G:
                stats["macro_out_of_range"] += 1
                bad = True
                break
            macros[dst] = v
        if bad:
            continue
        present_sum = sum(v for v in macros.values() if v is not None)
        if present_sum > MAX_MACRO_SUM:
            stats["macro_sum_over"] += 1
            continue
        out.append({
            "name": name,
            "kcal_per_100g": kcal,
            "protein_per_100g": macros["protein_per_100g"],
            "fat_per_100g": macros["fat_per_100g"],
            "cho_per_100g": macros["cho_per_100g"],
        })
    return out


def load_all(json_data_dir: Path) -> tuple[list[dict], dict]:
    """读取目录下全部 merged-*.json，清洗 + 按名称去重（保留先出现的）。"""
    stats = new_stats()
    seen: set[str] = set()
    kept: list[dict] = []
    files = sorted(json_data_dir.glob("*.json"))
    if not files:
        raise SystemExit(f"目录下没有 JSON 文件：{json_data_dir}")
    for f in files:
        try:
            records = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"跳过无法解析的文件 {f.name}: {exc}", file=sys.stderr)
            continue
        if not isinstance(records, list):
            print(f"跳过非数组文件 {f.name}", file=sys.stderr)
            continue
        for row in clean_records(records, stats):
            if row["name"] in seen:
                stats["duplicate_name"] += 1
                continue
            seen.add(row["name"])
            kept.append(row)
    stats["files"] = len(files)
    stats["kept"] = len(kept)
    return kept, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, default=None,
                        help="本地 json_data/ 目录路径（不给则 git sparse checkout 在线拉取）")
    parser.add_argument("--out", type=Path, default=ASSETS_PATH, help="输出 JSON 路径")
    args = parser.parse_args()

    if args.src:
        json_data_dir = args.src
        kept, stats = load_all(json_data_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="cfcd_") as tmp:
            json_data_dir = fetch_json_data(Path(tmp))
            kept, stats = load_all(json_data_dir)

    kept.sort(key=lambda r: r["name"])  # 稳定排序，vendor 文件 diff 可读
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(kept, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    size_kb = args.out.stat().st_size / 1024
    dropped = stats["total"] - stats["kept"] - stats["duplicate_name"]
    print(f"文件数：{stats['files']}，原始行：{stats['total']}")
    print(f"保留：{stats['kept']} 条（去重丢弃 {stats['duplicate_name']}，清洗丢弃 {dropped}）")
    print("清洗明细：" + ", ".join(
        f"{k}={v}" for k, v in stats.items() if k not in ("files", "total", "kept") and v
    ))
    print(f"已写入 {args.out}（{size_kb:.0f} KB）")


if __name__ == "__main__":
    main()
