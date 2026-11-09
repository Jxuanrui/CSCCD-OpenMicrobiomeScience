#!/usr/bin/env python3
"""golden_check——阶段 2+ 每件改造后的等价硬闸（golden 规格 §四.5 三合一档）。

为什么是脚本而不是人工比对：一步一审制下每道关口都要档化证据
（评审 W0.1 审条件 2："终端输出不作为证据"）——比对逻辑必须可复算。

三合一（顺序即规格）：
  ①先核输入：打印并比对 run 侧与交付版侧 manifest 内嵌的输入 sha 组
    （manifest 规范比对本身覆盖这些字段，此处显式打印供人读档）
  ②13 件科学产物 sha256 对冻结清单逐字节比对（清单首 12 件在 run 目录，
    第 13 件=臂件，来自臂重跑目录的 hypothesis_cards.tsv）
  ③manifest 剔白名单 9 键后的规范串 diff（run vs 交付版）
退出码：全等=0；任何不等=1（fail-closed，供 CI/关口判定）。
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ROOT / "data/logs/golden_baseline_v3_20261102_sha256.txt"
DELIVERED = ROOT / "data/kg/subgraph_htn_20261101_120403_t2merge"

# 与 golden 规格 §1.2 完全一致的 9 键白名单（勿增删——增删须过评审勘误）
WHITELIST = re.compile(
    r"^created_at$|^subgraph_id$|^lineage\.merged_from_subgraph$"
    r"|^lineage\.tier1_json$|^params\.subgraph_dir$|^params\.obs_dir$"
    r"|^params\.ext102$|^params\.bridge_tsv$|^params\.ko_map$")

MANIFESTS = ["manifest.json", "interaction_manifest.json", "chains_manifest.json",
             "inference_manifest.json", "hypothesis_manifest.json"]


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def canon(obj, run_name, deliv_name, pre=""):
    """剔白名单键+目录名归一后的规范串（两目录名互认等价）。

    白名单锚定的是全路径键（如 params.obs_dir）——递归必须携带路径
    前缀匹配，不能只看叶键名（首版栽点：叶键 obs_dir 不匹配
    ^params\\.obs_dir$，嵌套白名单键全部漏网被误判 DIFF）。
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in sorted(obj.items()):
            fk = f"{pre}.{k}" if pre else k
            if WHITELIST.search(fk):
                continue
            out[k] = canon(v, run_name, deliv_name, fk)
        return out
    if isinstance(obj, str):
        return obj.replace(run_name, "<DIR>").replace(deliv_name, "<DIR>")
    if isinstance(obj, list):
        return [canon(v, run_name, deliv_name, pre) for v in obj]
    return obj


def input_sha_groups(d: Path) -> dict:
    """从各 manifest 抽出内嵌的输入指纹组（键名含 inputs_sha256/master_before）。"""
    out = {}
    for mf in MANIFESTS:
        p = d / mf
        if not p.exists():
            continue
        data = json.loads(p.read_text())

        def walk(o, pre=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("inputs_sha256", "master_before") and isinstance(v, dict):
                        out[f"{mf}:{pre}{k}"] = v
                    walk(v, f"{pre}{k}.")
        walk(data)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="golden 等价三合一检查")
    ap.add_argument("--run-dir", required=True, type=Path, help="新链路输出目录(_t2)")
    ap.add_argument("--arm-cards", required=True, type=Path,
                    help="臂重跑目录的 hypothesis_cards.tsv")
    ap.add_argument("--label", required=True, help="工作项号（如 W1.2/W2.1）")
    # v4 参数化（T1，2026-11-06）：默认用 v3 冻结清单，行为零变更
    ap.add_argument("--frozen-list", type=Path, default=None,
                    help="冻结清单（默认 data/logs/golden_baseline_v3_*.txt；"
                         "v4 检查传 golden_baseline_v4_20261106_sha256.txt）")
    args = ap.parse_args(argv)

    _frozen_path = args.frozen_list or FROZEN
    frozen = [ln.split("  ", 1) for ln in _frozen_path.read_text().splitlines()
              if ln and not ln.startswith("#")]
    run, arm = args.run_dir, args.arm_cards
    lines = [f"golden_check 三合一档 | label={args.label} | {datetime.now():%F %T}",
             f"run_dir={run} | arm_cards={arm}", ""]
    fail = []

    # ① 输入指纹组
    lines.append("== ① 输入指纹组（run vs 交付版，显式打印） ==")
    ri, di = input_sha_groups(run), input_sha_groups(DELIVERED)
    for k in sorted(set(ri) | set(di)):
        a, b = ri.get(k), di.get(k)
        ok = a == b
        lines.append(f"{'OK ' if ok else 'DIFF'} {k}: "
                     f"{len(a) if a else 0} 项" + ("" if ok else f" {a} vs {b}"))
        if not ok:
            fail.append(k)

    # ② 13 件 sha 对冻结清单
    lines += ["", "== ② 13 件科学产物 sha256 vs 冻结清单 =="]
    for i, (fsha, fname) in enumerate(frozen):
        if fname.startswith("null_control_arm_cards"):
            path = arm            # 第 13 件注记名（=runA_arm/...）优先判
        elif "/" in fname:
            # v4 跨目录清单（T1，2026-11-06）：纯路径名 → 仓根相对
            path = ROOT / fname
        else:
            path = run / fname
        if not path.exists():
            lines.append(f"MISS {fname}（{path}）")
            fail.append(fname)
            continue
        actual = sha(path)
        # 冻结清单第 13 行文件名带注记（=runA_arm/...），只对 sha
        ok = actual == fsha
        lines.append(f"{'OK ' if ok else 'DIFF'} {fname} frozen={fsha[:16]} "
                     f"actual={actual[:16]}")
        if not ok:
            fail.append(fname)

    # ③ manifest 规范串 diff
    lines += ["", "== ③ manifest 剔白名单 9 键规范串（run vs 交付版） =="]
    for mf in MANIFESTS:
        p1, p2 = run / mf, DELIVERED / mf
        if not p1.exists():
            lines.append(f"MISS {mf}")
            fail.append(mf)
            continue
        c1 = canon(json.loads(p1.read_text()), run.name, DELIVERED.name)
        c2 = canon(json.loads(p2.read_text()), run.name, DELIVERED.name)
        if c1 == c2:
            lines.append(f"OK   {mf}")
        else:
            fail.append(mf)
            lines.append(f"DIFF {mf}——差异键集：")
            flat1 = {k: str(v) for k, v in _flat(c1).items()}
            flat2 = {k: str(v) for k, v in _flat(c2).items()}
            for k in sorted(set(flat1) | set(flat2)):
                if flat1.get(k) != flat2.get(k):
                    lines.append(f"  {k}: {flat1.get(k, '<缺>')[:50]} | "
                                 f"{flat2.get(k, '<缺>')[:50]}")

    verdict = "PASS——与 golden 基线全等" if not fail else f"FAIL——{len(fail)} 处不等: {fail}"
    lines += ["", f"结论：{verdict}"]
    out = ROOT / f"data/logs/golden_check_{args.label}_{datetime.now():%Y%m%d_%H%M%S}.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[golden_check] {verdict}\n[golden_check] 档案={out}")
    return 0 if not fail else 1


def _flat(o, pre=""):
    """规范串再压平一行一键，供 ③ 差异打印。"""
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(_flat(v, f"{pre}.{k}" if pre else k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(_flat(v, f"{pre}[{i}]"))
    else:
        out[pre] = o
    return out


if __name__ == "__main__":
    sys.exit(main())
