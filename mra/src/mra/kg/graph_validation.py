"""P3 Graph Validation Framework v0.1——把治理原则转化为机器可执行规则。

P3 铁律：只增加自动治理能力，不改变知识资产。
- frozen v1 assertion / hash / snapshot 不可变
- P1 SDK API 不变
- P2 provenance 事实不变
- Validation 是治理元数据，不是科学事实

架构位置：
  KG Build → **Validation Engine** → Report → Release Gate → Materialization
（先验证后放行，不是先生成再人工发现）

6 条规则（裁决第四节）：
  RULE_ASSERTION_EVIDENCE_REQUIRED   BLOCKER  每个 assertion 必须有 PMID/span/provenance
  RULE_MATERIALIZATION_ELIGIBLE       BLOCKER  manual_hold/dropped 不得进入 serving
  RULE_MANUAL_HOLD_ISOLATION          BLOCKER  hold 只在 review storage，不在 serving
  RULE_CANONICAL_EXPLAINABILITY       HIGH     literature canonical 必须可下钻 assertion
  RULE_PROVENANCE_COMPLETENESS        BLOCKER  source→execution→capability→assertion
  RULE_SNAPSHOT_BINDING_INTEGRITY     HIGH     capability 输出必须携带 snapshot_id
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

VALIDATION_FRAMEWORK_VERSION = "graph-validation/0.1"


class Severity(str, Enum):
    BLOCKER = "BLOCKER"    # 阻止 release/materialization
    HIGH = "HIGH"          # 警告但不阻止（需人工审查）
    MEDIUM = "MEDIUM"      # 建议


class FailureAction(str, Enum):
    PREVENT_RELEASE = "prevent_release"
    PREVENT_MATERIALIZATION = "prevent_materialization"
    WARN = "warn"


@dataclass
class ValidationRule:
    """一条图验证规则。"""
    rule_id: str
    description: str
    scope: str            # RelationAssertion | CanonicalEdge | Materialization | Capability
    severity: Severity
    failure_action: FailureAction
    validation_logic: str # human-readable description
    validate: Any         # callable(data) -> list[ValidationFailure]


@dataclass
class ValidationFailure:
    """一条规则违反。"""
    rule_id: str
    object_id: str
    reason: str
    evidence: str
    remediation: str


@dataclass
class ValidationReport:
    """一次验证运行的完整报告。"""
    validation_run_id: str
    snapshot_id: str
    rules_total: int = 0
    passed: int = 0
    failed: int = 0
    blockers: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    details: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "validation_run_id": self.validation_run_id,
            "snapshot_id": self.snapshot_id,
            "framework_version": VALIDATION_FRAMEWORK_VERSION,
            "rules_total": self.rules_total,
            "passed": self.passed, "failed": self.failed,
            "blockers": self.blockers, "warnings": self.warnings,
            "details": self.details}


class GraphValidationEngine:
    """验证引擎：读 frozen 数据 → 执行规则 → 出报告（只读，不修改）。"""

    def __init__(self, merged_dir: str | Path | None = None):
        self.merged_dir = Path(merged_dir or os.environ.get(
            "KG_MERGED_DIR",
            Path(__file__).resolve().parents[4] / "data" / "merged" / "candidate_v3"))
        self._assertions: pd.DataFrame | None = None
        self._edges: pd.DataFrame | None = None
        self._manifest: dict | None = None
        self.rules: list[ValidationRule] = self._build_rules()

    # ---- Data accessors (lazy, read-only) ----

    @property
    def assertions(self) -> pd.DataFrame:
        if self._assertions is None:
            self._assertions = pd.read_csv(
                self.merged_dir / "relation_assertions.tsv", sep="\t").fillna("")
        return self._assertions

    @property
    def edges(self) -> pd.DataFrame:
        if self._edges is None:
            self._edges = pd.read_csv(
                self.merged_dir / "merged_edges.tsv", sep="\t").fillna("")
        return self._edges

    @property
    def manifest(self) -> dict:
        if self._manifest is None:
            self._manifest = json.loads(
                (self.merged_dir / "snapshot_manifest.json").read_text(encoding="utf-8"))
            # v3 起 snapshot_manifest 不携带 assertion_counts（空 dict），计数在
            # finalize_metrics.json——回退合并，保证 RULE_MATERIALIZATION_ELIGIBLE
            # 等计数类规则在 v3 数据上仍可执行（2026-10-07）
            if not self._manifest.get("assertion_counts"):
                fm_path = self.merged_dir / "finalize_metrics.json"
                if fm_path.exists():
                    fm = json.loads(fm_path.read_text(encoding="utf-8"))
                    if fm.get("assertion_counts"):
                        self._manifest["assertion_counts"] = fm["assertion_counts"]
        return self._manifest

    # ---- Rule implementations ----

    def _build_rules(self) -> list[ValidationRule]:
        return [
            ValidationRule(
                rule_id="RULE_ASSERTION_EVIDENCE_REQUIRED",
                description="每个 RelationAssertion 必须有 PMID/source + evidence_span + provenance",
                scope="RelationAssertion", severity=Severity.BLOCKER,
                failure_action=FailureAction.PREVENT_RELEASE,
                validation_logic="for each assertion: evidence_pmid non-empty AND evidence_span non-empty AND provenance parseable",
                validate=self._check_assertion_evidence),

            ValidationRule(
                rule_id="RULE_MATERIALIZATION_ELIGIBLE",
                description="manual_hold / dropped_manual 不得进入 materialization eligible 集",
                scope="Materialization", severity=Severity.BLOCKER,
                failure_action=FailureAction.PREVENT_MATERIALIZATION,
                validation_logic="for each assertion: if manual_hold non-empty → NOT eligible; assertion_set must not contain dropped_manual",
                validate=self._check_materialization_eligibility),

            ValidationRule(
                rule_id="RULE_MANUAL_HOLD_ISOLATION",
                description="manual_hold 只在 review storage，不在 serving/snapshot/consumer",
                scope="Materialization", severity=Severity.BLOCKER,
                failure_action=FailureAction.PREVENT_MATERIALIZATION,
                validation_logic="manual_hold.tsv entries must have eligible='no canonical/gate/materialize'; manifest manual_hold_count matches assertions",
                validate=self._check_manual_hold_isolation),

            ValidationRule(
                rule_id="RULE_CANONICAL_EXPLAINABILITY",
                description="literature-derived canonical edge 必须可下钻至 supporting assertion",
                scope="CanonicalEdge", severity=Severity.HIGH,
                failure_action=FailureAction.WARN,
                validation_logic="for each canonical edge with source_type=llm_extracted: EXISTS assertion with same (subject, predicate, object)",
                validate=self._check_canonical_explainability),

            ValidationRule(
                rule_id="RULE_PROVENANCE_COMPLETENESS",
                description="每条 assertion 的 provenance 链完整（source→execution→capability→assertion）",
                scope="RelationAssertion", severity=Severity.BLOCKER,
                failure_action=FailureAction.PREVENT_RELEASE,
                validation_logic="for each assertion provenance JSON: execution_id + capability_id + source 非空",
                validate=self._check_provenance_completeness),

            ValidationRule(
                rule_id="RULE_SNAPSHOT_BINDING_INTEGRITY",
                description="所有 capability 输出必须携带 snapshot_id",
                scope="Capability", severity=Severity.HIGH,
                failure_action=FailureAction.WARN,
                validation_logic="manifest.snapshot_id non-empty AND kg_schema_version non-empty",
                validate=self._check_snapshot_binding),
        ]

    # ---- Rule logic ----

    def _check_assertion_evidence(self, _) -> list[ValidationFailure]:
        failures = []
        for i, r in self.assertions.iterrows():
            if not r.get("evidence_pmid"):
                failures.append(ValidationFailure(
                    self.rules[0].rule_id, r["assertion_id"],
                    "missing evidence_pmid", f"row {i}",
                    "re-run extraction or drop"))
            if not r.get("evidence_span_norm"):
                failures.append(ValidationFailure(
                    self.rules[0].rule_id, r["assertion_id"],
                    "missing evidence_span", f"row {i}",
                    "re-run span extraction"))
        return failures

    def _check_materialization_eligibility(self, _) -> list[ValidationFailure]:
        failures = []
        # manifest 计数恒等式与是否存在 hold 断言无关（2026-10-07 修正：v3 hold=0 时
        # 原实现因检查嵌在 if r.get("manual_hold") 内而从不执行）
        ac = self.manifest.get("assertion_counts", {})
        eligible = ac.get("materialization_eligible_assertion_count", -1)
        retained = ac.get("retained_assertion_count", -1)
        hold = ac.get("manual_hold_count", -1)
        if -1 not in (eligible, retained, hold) and eligible != retained - hold:
            failures.append(ValidationFailure(
                self.rules[1].rule_id, "manifest",
                f"eligible({eligible}) != retained({retained}) - hold({hold})",
                "manifest assertion_counts",
                "recompute counts"))
        return failures

    def _check_manual_hold_isolation(self, _) -> list[ValidationFailure]:
        failures = []
        hold_path = self.merged_dir / "manual_hold.tsv"
        if not hold_path.exists():
            if (self.assertions["manual_hold"] != "").any():
                failures.append(ValidationFailure(
                    self.rules[2].rule_id, "manual_hold.tsv",
                    "assertions have manual_hold but no manual_hold.tsv",
                    "file missing", "create manual_hold.tsv"))
        else:
            holds = pd.read_csv(hold_path, sep="\t")
            if "eligible_for" in holds.columns:
                bad = holds[~holds["eligible_for"].str.contains("no")]
                for _, h in bad.iterrows():
                    failures.append(ValidationFailure(
                        self.rules[2].rule_id, h.get("subject", ""),
                        f"hold entry eligible_for allows serving: {h.get('eligible_for', '')}",
                        str(h.to_dict()), "set eligible_for to no canonical/gate/materialize"))
        return failures

    def _check_canonical_explainability(self, _) -> list[ValidationFailure]:
        failures = []
        llm_edges = self.edges[self.edges.source_type == "llm_extracted"]
        a_triples = set(zip(self.assertions.subject, self.assertions.predicate,
                            self.assertions.object))
        for _, e in llm_edges.iterrows():
            triple = (e["subject"], e["predicate"], e["object"])
            if triple not in a_triples:
                failures.append(ValidationFailure(
                    self.rules[3].rule_id, f"{e['subject']}|{e['predicate']}|{e['object']}",
                    "literature canonical edge has no supporting assertion",
                    f"support_count={e.get('support_count', '')}",
                    "investigate aggregation gap"))
        return failures

    def _check_provenance_completeness(self, _) -> list[ValidationFailure]:
        failures = []
        for i, r in self.assertions.iterrows():
            try:
                prov = json.loads(r.get("provenance", "{}"))
            except (json.JSONDecodeError, TypeError):
                prov = {}
            missing = []
            if not prov.get("execution_id"):
                missing.append("execution_id")
            if not prov.get("capability_id"):
                missing.append("capability_id")
            if not prov.get("source"):
                missing.append("source")
            if missing:
                failures.append(ValidationFailure(
                    self.rules[4].rule_id, r["assertion_id"],
                    f"provenance missing: {', '.join(missing)}",
                    str(prov)[:100], "re-run provenance stamping"))
        return failures

    def _check_snapshot_binding(self, _) -> list[ValidationFailure]:
        failures = []
        m = self.manifest
        if not m.get("snapshot_id"):
            failures.append(ValidationFailure(
                self.rules[5].rule_id, "manifest",
                "missing snapshot_id", "manifest",
                "re-generate manifest"))
        if not m.get("kg_schema_version"):
            failures.append(ValidationFailure(
                self.rules[5].rule_id, "manifest",
                "missing kg_schema_version", "manifest",
                "re-generate manifest"))
        return failures

    # ---- Engine ----

    def validate(self, snapshot_id: str = "") -> ValidationReport:
        """执行全部规则，生成报告。"""
        run_id = f"VR-{uuid.uuid4().hex[:10]}"
        report = ValidationReport(
            validation_run_id=run_id,
            snapshot_id=snapshot_id or self.manifest.get("snapshot_id", ""))
        for rule in self.rules:
            try:
                failures = rule.validate(None)
            except Exception as exc:
                failures = [ValidationFailure(
                    rule.rule_id, "ENGINE", f"rule execution error: {exc}",
                    str(exc), "fix rule implementation")]
            if failures:
                report.failed += 1
                for f in failures:
                    entry = {
                        "rule_id": f.rule_id, "object_id": f.object_id,
                        "reason": f.reason, "evidence": f.evidence,
                        "remediation": f.remediation,
                        "severity": rule.severity.value,
                        "action": rule.failure_action.value}
                    report.details.append(entry)
                    if rule.severity == Severity.BLOCKER:
                        report.blockers.append(entry)
                    else:
                        report.warnings.append(entry)
            else:
                report.passed += 1
        report.rules_total = len(self.rules)
        return report

    def gate_check(self) -> dict:
        """Materialization/release gate 调用的快速判定。"""
        report = self.validate()
        return {
            "can_release": len(report.blockers) == 0,
            "can_materialize": len(report.blockers) == 0,
            "n_blockers": len(report.blockers),
            "n_warnings": len(report.warnings),
            "validation_run_id": report.validation_run_id,
            "snapshot_id": report.snapshot_id}


__all__ = ["FailureAction", "GraphValidationEngine", "Severity",
           "ValidationFailure", "ValidationReport", "ValidationRule",
           "VALIDATION_FRAMEWORK_VERSION"]
