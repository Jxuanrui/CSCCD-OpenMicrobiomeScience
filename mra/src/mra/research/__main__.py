"""研究循环 CLI。

用法：
  ARK_API_KEY=... python -m mra.research --question "<研究问题>" --target "<数据/队列描述>" \
      [--model doubao-seed-2.0-lite] [--max-iterations 16]
数据契约经 var/cohort_config.json（不入库）注入，代码不含任何队列细节。
"""
from __future__ import annotations

import argparse
import json

from ..kg.graph import KGGraph
from ..kg.snapshot import latest_snapshot
from .loop import run_session
from .session import ResearchSession


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="自主研究会话（受控工具面 + 预算熔断）")
    parser.add_argument("--question", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--model", default=None, help="缺省 doubao-seed-2.0-lite")
    parser.add_argument("--max-iterations", type=int, default=16)
    parser.add_argument("--llm-call-cap", type=int, default=50)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--resume", default=None, help="断点续跑：既有 run_id（与 --run-id 互斥）")
    args = parser.parse_args(argv)

    from .planner import make_ark_planner
    from .session import DEFAULT_RESEARCH_ROOT, ResearchSession
    session = None
    if args.resume:
        session = ResearchSession.load(DEFAULT_RESEARCH_ROOT / args.resume)
    session = run_session(
        args.question, args.target,
        graph=KGGraph(latest_snapshot()),
        planner_fn=make_ark_planner(args.model),
        max_iterations=args.max_iterations,
        session=session or ResearchSession(question=args.question, target=args.target,
                                            run_id=args.run_id, llm_call_cap=args.llm_call_cap),
    )
    state = session.state
    print(json.dumps({
        "run_id": state["run_id"], "status": state["status"],
        "llm_calls": state["llm_calls"], "iterations": state["iterations"],
        "n_findings": len(state["findings"]),
        "report": state["report"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
