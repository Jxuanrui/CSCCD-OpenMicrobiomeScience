"""Scientific Governance Gate——CandidateResult 进入 Evidence 前的显式裁决。

六查（用户裁决 2026-09-24）：provenance 完整 / input lineage 完整 /
method constraints 满足 / required sensitivity 完成 / governance verdict /
阻断级 warning；另裁 canonical 资格。**未通过（allow_evidence=False）时
不得调用 workspace.record_evidence**——该铁律由 record_evidence 实现侧
强制（无 allow 裁决即拒绝提交）。

语义边界：计算能力说"算出了什么"；本门与 workspace.* 变更能力共同决定
"什么可以成为正式科研证据"。
"""
from __future__ import annotations

from typing import Any

from .workspace import CandidateResult


class GovernanceVerdict(dict):
    """裁决信封：{allow_evidence, canonical_eligible, checks, blocking}。"""


REQUIRED_PROVENANCE_KEYS = ("capability_id", "implementation_id",
                            "capability_version", "implementation_version",
                            "input_fingerprint")


def evaluate_candidate(candidate: CandidateResult, *,
                       method_rules_applied: list[str] | None = None,
                       sensitivity_status: str = "none_required",
                       execution_governance: dict[str, Any] | None = None,
                       ) -> dict[str, Any]:
    """裁决 CandidateResult 是否可成为 Evidence。"""
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
    _check("method_constraints",
           bool(method_rules_applied),
           "须引用至少一条 METHOD_KNOWLEDGE 规则（无适用规则=人工评审另案）")
    _check("sensitivity_required",
           sensitivity_status in ("none_required", "passed"),
           f"sensitivity_status={sensitivity_status}")
    _check("execution_governance",
           bool(execution_governance and execution_governance.get("verdicts")),
           "执行侧治理 verdict（审计账本）须在场")
    blocking_warnings = [w for w in candidate.warnings
                         if w.get("level") == "blocking"]
    _check("no_blocking_warnings", not blocking_warnings,
           "; ".join(w.get("message", "") for w in blocking_warnings))

    allow = not blocking
    canonical_eligible = (allow and candidate.deterministic
                          and not candidate.warnings
                          and sensitivity_status in ("none_required", "passed"))
    return {"allow_evidence": allow,
            "canonical_eligible": canonical_eligible,
            "checks": checks,
            "blocking": blocking}


__all__ = ["GovernanceVerdict", "evaluate_candidate", "REQUIRED_PROVENANCE_KEYS"]
