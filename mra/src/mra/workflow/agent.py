"""Model-driven governance agent with a deliberately small tool surface."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mra.audit import AnalysisSpec, Verdict, run_audit
from mra.knowledge import KnowledgeStore
from mra.model_runtime import Message, ModelRef, ModelRequest, ModelRuntime, ToolSpec
from mra.pep import ArtifactRef, Pep, PepError
from mra.workflow import tools
from mra.workflow.orchestrate import _analysis_spec


class _RunTaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_id: str = Field(min_length=1)


class _ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _AuditArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alpha: float = Field(default=0.05, gt=0, le=1)


class _ProposeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _KnowledgeSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)


@dataclass
class AgentRunResult:
    goal: str
    trajectory: list[dict[str, Any]] = field(default_factory=list)
    audit_findings: list[str] = field(default_factory=list)
    proposal_request_id: str | None = None
    status: str = "COMPLETED"


class GovernanceAgent:
    """A bounded tool-calling loop whose side effects all pass through PEP."""

    def __init__(
        self,
        runtime: ModelRuntime,
        pep: Pep,
        model_ref: ModelRef,
        max_iterations: int = 8,
        knowledge: KnowledgeStore | None = None,
    ) -> None:
        if max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        self.runtime = runtime
        self.pep = pep
        self.model_ref = model_ref
        self.max_iterations = max_iterations
        self.knowledge = knowledge
        self._current: ArtifactRef | None = None
        self._audit_findings: list[str] = []
        self._audit_done = False
        self._proposal_request_id: str | None = None

    def _tool_specs(self: GovernanceAgent | None = None) -> tuple[ToolSpec, ...]:
        def spec(name: str, description: str, model: type[BaseModel]) -> ToolSpec:
            return ToolSpec(name, description, model.model_json_schema())

        specs = [
            spec("run_registered_task", "Run a registered research task through the PEP.", _RunTaskArgs),
            spec("read_current_result", "Read a concise summary of the current artifact.", _ReadArgs),
            spec("audit_current_results", "Audit the current analysis artifact with deterministic rules.", _AuditArgs),
            spec("propose_promotion", "Propose promotion only after an audit without FAIL findings.", _ProposeArgs),
        ]
        knowledge = self.knowledge if self is not None else None
        if knowledge is not None:
            specs.append(
                spec(
                    "knowledge_search",
                    "Search approved knowledge and return concise evidence references.",
                    _KnowledgeSearchArgs,
                )
            )
        return tuple(specs)

    def _knowledge_search(self, query: str) -> str:
        if self.knowledge is None:
            return "错误：知识检索工具未启用。"
        hits = self.knowledge.search(query, include_retired=False)
        if not hits:
            return "未找到匹配的已批准知识条目。"
        lines = []
        for hit in hits:
            lines.append(
                f"{hit.entry.title} | {hit.entry.evidence_level} | "
                f"{hit.entry.applicability} | {hit.source_summary}"
            )
        return "\n".join(lines)

    def _run_task(self, task_id: str) -> str:
        # The model chooses only the registered ID. Workflow arguments stay trusted.
        args: tuple[str, ...] = ()
        subject_path: Path | None = None
        if task_id == "simulate_association":
            subject_path = Path(self.pep.staging_root) / "workflow-subjects" / f"{uuid.uuid4().hex}.json"
            args = ("--seed", "20260911", "--out", str(subject_path))
        artifact = tools.run_registered_task(
            self.pep, task_id, args, {"network_mode": "deny", "credential_refs": []}, 60
        )
        self._current = artifact
        if subject_path is not None and subject_path.is_file():
            data = subject_path.read_bytes()
            self._current = ArtifactRef(
                request_id=artifact.request_id,
                digest=hashlib.sha256(data).hexdigest(),
                path=subject_path,
            )
        return json.dumps({"request_id": self._current.request_id, "path": str(self._current.path), "digest": self._current.digest})

    def _read_current(self) -> str:
        if self._current is None:
            raise PepError("No current artifact is available.")
        try:
            document = json.loads(self._current.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise PepError("Current artifact is not readable JSON.") from None
        if not isinstance(document, dict):
            raise PepError("Current artifact JSON must be an object.")
        numeric: dict[str, Any] = {}
        for key, value in document.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric[key] = value
            if len(numeric) >= 8:
                break
        return json.dumps({"keys": list(document)[:30], "numeric": numeric}, ensure_ascii=False)

    def _audit_current(self, alpha: float) -> str:
        if self._current is None:
            raise PepError("No current artifact is available.")
        try:
            document = json.loads(self._current.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise PepError("Current artifact is not readable JSON.") from None
        if not isinstance(document, dict):
            raise PepError("Current artifact JSON must be an object.")
        data = dict(_analysis_spec(document).model_dump())
        data["alpha"] = alpha
        findings = run_audit(AnalysisSpec.model_validate(data))
        self._audit_findings = [
            f"{finding.rule_id}:{finding.verdict.value}:{finding.details.get('reason', '')}"
            for finding in findings
        ]
        self._audit_done = True
        return "\n".join(self._audit_findings)

    def _propose(self) -> str:
        if self._current is None:
            return "拒绝提议晋升：当前没有产物。"
        if not self._audit_done:
            return "拒绝提议晋升：必须先完成审计。"
        if any(":FAIL:" in finding for finding in self._audit_findings):
            return "拒绝提议晋升：审计存在 FAIL。"
        tools.propose_promotion(self.pep, self._current.request_id, self._current.digest)
        self._proposal_request_id = self._current.request_id
        return f"已创建待审批晋升请求：{self._proposal_request_id}"

    def _execute(self, name: str, arguments_json: str) -> str:
        models: dict[str, type[BaseModel]] = {
            "run_registered_task": _RunTaskArgs,
            "read_current_result": _ReadArgs,
            "audit_current_results": _AuditArgs,
            "propose_promotion": _ProposeArgs,
        }
        if self.knowledge is not None:
            models["knowledge_search"] = _KnowledgeSearchArgs
        if name not in models:
            return f"错误：未知工具名 {name}。"
        try:
            args = models[name].model_validate_json(arguments_json)
            if name == "run_registered_task":
                return self._run_task(args.task_id)
            if name == "read_current_result":
                return self._read_current()
            if name == "audit_current_results":
                return self._audit_current(args.alpha)
            if name == "knowledge_search":
                return self._knowledge_search(args.query)
            return self._propose()
        except PepError as error:
            return f"PEP 拦截：{error}"
        except Exception as error:
            return f"工具错误：{error}"

    def run(self, goal: str) -> AgentRunResult:
        result = AgentRunResult(goal=goal)
        messages: list[Message] = [
            Message(role="system", content="你是治理型科研 Agent。只能调用给定工具；分析须先审计再提议晋升；审计 FAIL 不得晋升；任务完成后用一句话总结。"),
            Message(role="user", content=goal),
        ]
        for turn in range(self.max_iterations):
            request = ModelRequest(
                request_id=uuid.uuid4().hex,
                model=self.model_ref,
                messages=tuple(messages),
                tools=self._tool_specs(),
            )
            try:
                response = self.runtime.complete(request)
            except Exception as error:
                result.status = "FAILED"
                result.trajectory.append({"turn": turn, "tool": "model", "args": {}, "outcome": str(error)})
                return result
            if not response.tool_calls:
                result.status = "WAITING_APPROVAL" if result.proposal_request_id else "COMPLETED"
                return result
            messages.append(Message(role="assistant", content=response.content, tool_calls=response.tool_calls))
            for call in response.tool_calls:
                outcome = self._execute(call.name, call.arguments_json)
                try:
                    recorded_args: Any = json.loads(call.arguments_json)
                except json.JSONDecodeError:
                    recorded_args = call.arguments_json
                result.trajectory.append({"turn": turn, "tool": call.name, "args": recorded_args, "outcome": outcome})
                messages.append(Message(role="tool", content=outcome, tool_call_id=call.id, name=call.name))
                result.audit_findings = list(self._audit_findings)
                result.proposal_request_id = self._proposal_request_id
        result.status = "MAX_ITERATIONS"
        result.audit_findings = list(self._audit_findings)
        result.proposal_request_id = self._proposal_request_id
        return result


__all__ = ["AgentRunResult", "GovernanceAgent"]
