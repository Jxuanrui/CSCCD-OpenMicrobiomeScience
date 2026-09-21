"""研究会话状态（KSDS 式）：问题/计划/发现/预算全程落盘，断点可续、报告从状态编译。"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_RESEARCH_ROOT = Path(__file__).resolve().parents[3] / "var" / "research"
DEFAULT_LLM_CALL_CAP = 50  # Q2 决策：单会话上限，超限即停


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Finding:
    claim: str
    tool: str
    inputs: dict
    evidence: dict = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict:
        return asdict(self)


class ResearchSession:
    """一次自主研究会话的可审计状态。state.json 每次变更后整体覆写。"""

    def __init__(self, question: str, target: str, run_id: str | None = None,
                 root: Path = DEFAULT_RESEARCH_ROOT, llm_call_cap: int = DEFAULT_LLM_CALL_CAP):
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.run_dir = Path(root) / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.run_dir / "state.json"
        self.state = {
            "run_id": self.run_id, "question": question, "target": target,
            "status": "running", "iterations": 0, "llm_calls": 0,
            "llm_call_cap": llm_call_cap, "plan": [], "findings": [], "report": None,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.save()

    # ---------- 预算 ----------
    def record_llm_call(self) -> None:
        if self.state["llm_calls"] >= self.state["llm_call_cap"]:
            self.state["status"] = "budget_stopped"
            self.save()
            raise BudgetExceeded(
                f"LLM 调用达到上限 {self.state['llm_call_cap']}（会话 {self.run_id}）")
        self.state["llm_calls"] += 1

    # ---------- 计划与发现 ----------
    def set_plan(self, plan: list[dict]) -> None:
        self.state["plan"] = plan
        self.save()

    def add_finding(self, finding: Finding) -> None:
        self.state["findings"].append(finding.to_dict())
        self.save()

    def add_artifact(self, name: str, content: str) -> Path:
        path = self.run_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def finish(self, report: dict) -> None:
        self.state["status"] = "done"
        self.state["report"] = report
        self.save()

    def save(self) -> None:
        self.state_path.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, run_dir: Path, root: Path = DEFAULT_RESEARCH_ROOT) -> "ResearchSession":
        state = json.loads((Path(run_dir) / "state.json").read_text(encoding="utf-8"))
        session = cls.__new__(cls)
        session.run_id = state["run_id"]
        session.run_dir = Path(run_dir)
        session.state_path = session.run_dir / "state.json"
        session.state = state
        return session
