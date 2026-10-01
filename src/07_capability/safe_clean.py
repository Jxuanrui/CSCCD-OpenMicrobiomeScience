#!/usr/bin/env python3
"""安全清理器（监工计划 v2 P0-1：Q3 误删事故的防线，2026-10-01）。

规则：
  1. 默认演练（dry-run）：逐条列出将删除的键路径，不写任何文件；
  2. --apply 才真删；删除前把目标文件 tar 备份到 <root>/data/backups/<ts>/；
  3. 只按精确键路径删（点分路径逐级校验存在），禁止按字样递归匹配；
  4. 路径中含通配或"任意字样匹配"直接拒绝。
回归测试见 test_safe_clean.py（含"暂未落盘"字样兄弟对象必须存活的最小用例）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path


def resolve(obj, dotted: str):
    """精确点分路径解析；含通配符直接拒。返回 (父对象, 末级键) 或 None。"""
    if any(w in dotted for w in ("*", "?", "[", "]")):
        raise ValueError(f"禁止通配路径：{dotted}")
    cur = obj
    for k in dotted.split(".")[:-1]:
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    last = dotted.split(".")[-1]
    return (cur, last) if isinstance(cur, dict) and last in cur else None


def safe_clean(json_path: Path, keys: list[str], apply: bool = False) -> dict:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    plan, missing = [], []
    for k in keys:
        hit = resolve(data, k)
        if hit:
            plan.append((k, json.dumps(hit[0][hit[1]], ensure_ascii=False)[:80]))
        else:
            missing.append(k)
    if not apply:
        return {"mode": "dry-run", "would_delete": plan, "missing": missing,
                "note": "演练完成未写盘；确认无误后加 --apply"}
    # 真删：先 tar 备份
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    _root = json_path
    while _root.name != 'data' and _root.parent != _root.parent.parent:
        _root = _root.parent
    bdir = (_root if _root.name == 'data' else json_path.parent) / 'backups' / ts  # data/backups/<ts>/（监工 C0-3）
    bdir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bdir / f"{json_path.name}.tar", "w") as tf:
        tf.add(json_path, arcname=json_path.name)
    deleted = []
    for k, _ in plan:
        parent, last = resolve(data, k)
        del parent[last]
        deleted.append(k)
    tmp = json_path.with_suffix(".tmp")  # P1 原子写：先临时文件再 rename
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(json_path)
    return {"mode": "applied", "deleted": deleted, "missing": missing, "backup": str(bdir)}


def main():
    ap = argparse.ArgumentParser(description="JSON 安全清理（默认演练）")
    ap.add_argument("json_file")
    ap.add_argument("keys", nargs="+", help="精确点分键路径（如 top.k1.k2），禁通配")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    print(json.dumps(safe_clean(Path(args.json_file), args.keys, args.apply),
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
