"""基线对账器（序3 执行前门禁，评审 2026-11-08 第五轮裁定）。

一句话：把 D 阶段产卡管线在**隔离副本**里用同参数原样重跑一遍，
三件产物与主子图冻结件逐字节比对——全等才开"拔积木"分析的闸。

为什么必须整目录复跑而非重算函数：卡片是 chains+edges+外证+库索引
的复合产物，任何"选择性重算"都可能漏某个输入；整管线复跑+逐字节比对
是唯一无死角的基线证明。隔离副本=不动主子图一个字节。
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_SUB = ROOT / "data/kg/subgraph_htn_20261101_120403_t2merge"
CMP_FILES = ("hypothesis_cards.tsv", "top_hypotheses.tsv",
             "hypothesis_cards_null_control.tsv")
ORIG_PARAMS = json.loads(
    (SRC_SUB / "hypothesis_manifest.json").read_text(encoding="utf-8")
)["params"]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="序3 基线对账（隔离复跑+逐字节比）")
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    for d in (args.scratch, args.out):
        if d.exists() and any(d.iterdir()):
            raise SystemExit(f"[reconcile] {d} 非空——换时间戳（fail-closed）")
    args.scratch.mkdir(parents=True)
    args.out.mkdir(parents=True)

    # 1) 整目录复制主子图（冻结原件零触碰；复跑只写 scratch）
    for f in SRC_SUB.iterdir():
        if f.is_file():
            shutil.copy2(f, args.scratch / f.name)
    # 2) 同参数复跑（参数取自 hypothesis_manifest.params——非我手拼）
    p = ORIG_PARAMS
    cmd = [str(ROOT / ".venv/bin/python"), "src/06_tool/hypothesize.py",
           "--subgraph-dir", str(args.scratch),
           "--ext102", str(ROOT / p["ext102"]),
           "--gutmgene-dir", p["gutmgene_dir"]]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    (args.out / "rerun_console.log").write_text(
        "$ " + " ".join(cmd) + "\n" + r.stdout + r.stderr, encoding="utf-8")
    if r.returncode != 0:
        (args.out / "reconcile.json").write_text(json.dumps(
            {"verdict": "RERUN_FAILED", "rc": r.returncode}, ensure_ascii=False),
            encoding="utf-8")
        print("[reconcile] 复跑失败 rc=", r.returncode)
        return 1

    # 3) 三件产物逐字节比对
    rows = {}
    ok = True
    for name in CMP_FILES:
        a, b = SRC_SUB / name, args.scratch / name
        same = a.exists() and b.exists() and a.read_bytes() == b.read_bytes()
        rows[name] = {"frozen_sha": sha(a) if a.exists() else None,
                      "rerun_sha": sha(b) if b.exists() else None,
                      "byte_identical": same}
        ok &= same
    res = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": "PASS" if ok else "FAIL",
        "meaning": "PASS=现行榜单可由在盘输入完整复现（序3 基线门禁开）",
        "params_from_manifest": ORIG_PARAMS,
        "cmp": rows,
        "rerun_cmd": cmd,
    }
    (args.out / "reconcile.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"{sha(f)}  {f.name}" for f in sorted(args.out.iterdir())
             if f.name != "SHA256SUMS"]
    (args.out / "SHA256SUMS").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
    print(f"[reconcile] verdict={res['verdict']}")
    for k, v in rows.items():
        print(f"  {k}: {'逐字节一致 ✓' if v['byte_identical'] else '不一致 ✗'}"
              f" ({v['frozen_sha'][:16]}…)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
