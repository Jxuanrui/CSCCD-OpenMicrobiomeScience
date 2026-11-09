"""分析配方层读取器（阶段 3 轨C）——只转印配方卡，不评价不计算。

配方=configs/recipes/<key>.json；词条三态：recommended（有仓内实测证据）/
not_recommended（有仓内实测反证）/candidate（未实测，轮子调研制补）。
护栏：recommended/not_recommended 的 evidence 路径必须在盘
（test_recipes.py 强制）——"理由必挂档案"由此成为机器规则。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECIPE_DIR = ROOT / "configs" / "recipes"
STATUS_ORDER = {"recommended": "✅推荐", "not_recommended": "❌不推荐",
                "candidate": "⏳候选"}


def load(key: str) -> dict:
    p = RECIPE_DIR / f"{key}.json"
    if not p.exists():
        avail = sorted(x.stem for x in RECIPE_DIR.glob("*.json")) or ["（无）"]
        sys.exit(f"[recipes] 无配方 {p.name}；可用: {', '.join(avail)}")
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="分析配方（推荐词条只转印）")
    ap.add_argument("--disease", required=True, help="配方 key（如 htn）")
    ap.add_argument("--category", default=None, help="只看某类目")
    args = ap.parse_args(argv)
    d = load(args.disease)
    print(f"分析配方：{d.get('key')}（词条=仓内证据转印；candidate=未实测待调研）")
    for cat, entries in d.items():
        if cat.startswith("_") or cat == "key":
            continue
        if args.category and cat != args.category:
            continue
        print(f"\n[{cat}]")
        if "_note" in entries:
            print(f"  {entries['_note']}")
            continue
        for tool, e in entries.items():
            print(f"  {STATUS_ORDER.get(e['status'], e['status'])} "
                  f"{tool}\n    理由：{e['reason']}\n    证据：{e['evidence']}"
                  f"\n    安装：{e.get('install', '—')}｜输入：{e.get('input', '—')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
