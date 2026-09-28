"""Scientific Governance Gate——CandidateResult → Evidence 的一等裁决（GovernanceDecision）。

六查不变（provenance/input lineage/method constraints/sensitivity/execution
governance/阻断 warning + canonical 资格）；产出从裸 dict 升级为可持久化、
可回放的 GovernanceDecision 账本对象：record_evidence 只认 decision_id，
由 Workspace 账本六验自行核验（存在/同候选/指纹一致/allow/未失效/lineage）。
canonical_eligible 只代表"允许进入 canonical 决策"，非自动 canonical。
"""
from __future__ import annotations

from typing import Any

from .workspace import CandidateResult, GovernanceDecision, digest

REQUIRED_PROVENANCE_KEYS = ("capability_id", "implementation_id",
                            "capability_version", "implementation_version",
                            "input_fingerprint")


def evaluate_candidate(candidate: CandidateResult, *,
                       candidate_event_seq: int,
                       method_rules_applied: list[str] | None = None,
                       sensitivity_status: str = "none_required",
                       execution_governance: dict[str, Any] | None = None,
                       decision_id: str = "",
                       actor: str = "unknown", client: str = "unknown",
                       model: str = "",
                       governance_event_id: str | None = None,
                       supersedes_decision_id: str | None = None) -> GovernanceDecision:
    """裁决并返回 GovernanceDecision（调用方负责 append 入 Scientific Ledger）。"""
    checks: list[dict[str, Any]] = []
    blocking: list[str] = []

    def _check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "pass": bool(ok), "detail": detail})
        if not ok:
            blocking.append(f"{name}: {detail}" if detail else name)

    _check("provenance_complete",
           all(getattr(candidate, k, None) for k in REQUIRED_PROVENANCE_KEYS)
           and bool(candidate.provenance),
           "CandidateResult 五要素与 provenance 字典须齐")
    _check("input_lineage", bool(candidate.input_fingerprint)
           and bool(candidate.analysis_id))
    _check("method_constraints", bool(method_rules_applied),
           "须引用至少一条 METHOD_KNOWLEDGE 规则（无适用规则=人工评审另案）")
    _check("sensitivity_required",
           sensitivity_status in ("none_required", "passed"),
           f"sensitivity_status={sensitivity_status}")
    _check("execution_governance",
           bool(execution_governance and execution_governance.get("verdicts")),
           "执行侧治理 verdict（审计账本）须在场")
    blocking_warnings = [w for w in candidate.warnings if w.get("level") == "blocking"]
    _check("no_blocking_warnings", not blocking_warnings,
           "; ".join(w.get("message", "") for w in blocking_warnings))

    allow = not blocking
    canonical_eligible = (allow and candidate.deterministic
                          and not candidate.warnings
                          and sensitivity_status in ("none_required", "passed"))
    return GovernanceDecision(
        decision_id=decision_id or f"GD-{candidate.analysis_id}-{candidate_event_seq}",
        analysis_id=candidate.analysis_id,
        candidate_event_seq=candidate_event_seq,
        candidate_hash=digest(candidate.model_dump()),
        checks=checks, allow_evidence=allow, canonical_eligible=canonical_eligible,
        blocking_reasons=blocking,
        governance_event_id=governance_event_id,
        actor=actor, client=client, model=model,
        supersedes_decision_id=supersedes_decision_id)


__all__ = ["REQUIRED_PROVENANCE_KEYS", "evaluate_candidate"]
