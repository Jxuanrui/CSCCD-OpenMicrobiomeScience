"""统一 runner（阶段 2 W2.3）——一条命令按依赖序跑完整条工具链。

为什么需要它：七模块先后顺序与中间目录衔接此前靠人记住（并行计划病根
之三）；runner 把顺序写死在代码里，用户只说"哪个病+数据在哪"。

设计约束：
1. 步内调用=与 golden 链**逐字同参**（同 argv 转发各模块 main）——
   保证 runner 路径与手工路径产物逐字节一致（golden 闸前提）；
2. 断点续跑：每步完成即写 run_state.json（步名+产物指纹），
   --resume 时"已完且指纹相符"的步直接跳过；
3. 步序固定不许配置——顺序是依赖关系不是偏好（spec §一 runtime 画界）。
"""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import deepchain  # noqa: E402
import hypothesize  # noqa: E402
import inference  # noqa: E402
import interact  # noqa: E402
import subgraph  # noqa: E402

STEPS = ["tier0", "merge", "interact", "deepchain", "inference", "hypothesize"]

# 每步的代表性产物（相对 out_dir）——checkpoint 指纹与续跑跳过的判据
STEP_PRODUCTS = {
    "tier0": ["tier0/merged_edges.tsv"],
    "merge": ["subgraph/merged_edges.tsv"],
    "interact": ["subgraph/interaction_report.tsv"],
    "deepchain": ["subgraph/chains.tsv"],
    "inference": ["subgraph/chain_annotations.tsv"],
    "hypothesize": ["subgraph/hypothesis_cards.tsv"],
}

MODULES = {"tier0": subgraph, "merge": subgraph, "interact": interact,
           "deepchain": deepchain, "inference": inference,
           "hypothesize": hypothesize}


def _sha16(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


def build_argv(step: str, disease: str, out_dir: Path,
               tier2_batch: Path) -> list[str]:
    """构造与 golden 链逐字同参的 argv（唯一事实来源，测试锁定）。

    注意：只有 subgraph 有子命令（argv 首位=tier0/merge）；其余模块
    argparse 无子命令——argv 首位必须是选项（首版就栽在多塞了命令名）。
    """
    t0 = str(out_dir / "tier0")
    sg = str(out_dir / "subgraph")
    if step == "tier0":
        return ["tier0", "--disease", disease, "--out", t0]
    if step == "merge":
        return ["merge", "--disease", disease, "--subgraph-dir", t0,
                "--tier2-batch", str(tier2_batch), "--out", sg]
    if step == "interact":
        return ["--disease", disease, "--subgraph-dir", sg]
    if step == "deepchain":
        return ["--subgraph-dir", sg]
    if step == "inference":
        return ["--subgraph-dir", sg]
    if step == "hypothesize":
        return ["--disease", disease, "--subgraph-dir", sg]
    raise ValueError(step)


def load_state(out_dir: Path) -> dict:
    p = out_dir / "run_state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"steps": {}}


def save_state(out_dir: Path, state: dict) -> None:
    (out_dir / "run_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def step_done(state: dict, step: str, out_dir: Path) -> bool:
    """已完=state 记录 done 且全部代表产物现存且指纹相符。"""
    rec = state["steps"].get(step)
    if not rec or rec.get("status") != "done":
        return False
    for rel, sha in rec.get("outputs", {}).items():
        p = out_dir / rel
        if not p.exists() or _sha16(p) != sha:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="W2.3 一键全流程 runner")
    ap.add_argument("--disease", required=True, help="疾病档案 key")
    ap.add_argument("--tier2-batch", required=True, type=Path,
                    help="tier2 冻结批次目录（merge 步输入，运行时指定）")
    ap.add_argument("--out-dir", required=True, type=Path,
                    help="运行目录（tier0/subgraph 子目录落于此）")
    ap.add_argument("--from-step", choices=STEPS, default="tier0")
    ap.add_argument("--to-step", choices=STEPS, default="hypothesize")
    ap.add_argument("--resume", action="store_true",
                    help="跳过已完且产物指纹相符的步")
    ap.add_argument("--cohort", default=None,
                    help="队列档案 key（透传 inference/hypothesize）")
    ap.add_argument("--dryrun", action="store_true",
                    help="只列步序与代表产物，不执行")
    args = ap.parse_args(argv)

    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    state = load_state(out)
    seq = STEPS[STEPS.index(args.from_step):STEPS.index(args.to_step) + 1]

    if args.dryrun:
        for step in seq:
            prods = "、".join(STEP_PRODUCTS[step])
            print(f"[dryrun] {step} → {prods}")
        print("[dryrun] 干跑结束，未执行任何步")
        return 0
    for step in seq:
        if args.resume and step_done(state, step, out):
            print(f"[run] {step}: 跳过（已完且指纹相符）")
            continue
        argv_step = build_argv(step, args.disease, out, args.tier2_batch)
        if args.cohort and step in ("inference", "hypothesize"):  # interact 不接队列档案（其参数=病卡+数据腿）
            argv_step += ["--cohort", args.cohort]  # 仅接队列档案的三步透传
        print(f"[run] {step}: {' '.join(argv_step[:2])} …")
        rc = MODULES[step].main(argv_step)
        missing = [rel for rel in STEP_PRODUCTS[step]
                   if not (out / rel).exists()]
        if rc == 0 and missing:
            print(f"[run] {step}: 产物缺失 {missing}（rc=0 但件不在=完整性"
                  f"事故，fail-closed）")
            return 1
        if rc != 0:
            save_state(out, state)
            print(f"[run] {step}: 失败 rc={rc}（状态已存，可 --resume 续跑）")
            return rc
        state["steps"][step] = {
            "status": "done",
            "finished_at": datetime.now().strftime("%F %T"),
            "outputs": {rel: _sha16(out / rel) for rel in STEP_PRODUCTS[step]},
        }
        save_state(out, state)

<<<<<<< HEAD
    if "finished_at" not in state:  # 全跳过时不覆写真实完成时间（评审 P2-1）
=======
    if "finished_at" not in state:  # 全跳过时不覆写真实完成时间（监工 P2-1）
>>>>>>> origin/main
        state["finished_at"] = datetime.now().strftime("%F %T")
    save_state(out, state)
    print(f"[run] 完成：{seq[0]}…{seq[-1]} → {out}")
    return 0


def run_null_arm(out_dir: Path, null_arm_input: Path) -> int:
    """空证对照臂（轨A1 后收口=转发 hypothesize 正式参数）。

    旧"整目录复制+掏空重跑"已由 hypothesize --null-arm-input/out
    进程内直算取代（sha=3d4fa018 等价已证）；本函数保留为便捷入口，
<<<<<<< HEAD
    一行转发防两套口径分叉（评审轨A审 P1-2）。
=======
    一行转发防两套口径分叉（监工轨A审 P1-2）。
>>>>>>> origin/main
    """
    import hypothesize
    return hypothesize.main(
        ["--subgraph-dir", str(out_dir / "subgraph"),
         "--null-arm-input", str(null_arm_input),
         "--null-arm-out", str(out_dir / "subgraph" / "null_control_arm_cards.tsv")])


if __name__ == "__main__":
    sys.exit(main())
