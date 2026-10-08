"""研究循环执行闸门：R 沙箱执行纳入治理底盘（资源限制 + 审计账本 + 统计审计）。

三层职责（MVP 接线，对应 MRA"执行不可绕过治理"的架构承诺）：
1. 资源限制：systemd user scope（MemoryMax/CPUQuota），systemd 不可用时回退普通
   subprocess 并在审计事件 constraints 标注 fallback——降级可见，不静默；
2. 审计账本：每次 R 执行前写入 mra.pep.AuditLedger（action=execute_task），
   执行后回填 output_digest（结果哈希），账本路径默认 var/audit/research.db；
3. 统计审计：结果过 mra.audit 规则（BH 重算校验/批次结构/成分性声明），
   verdict 附在返回值上；FAIL 时 raise AuditGateError（按"审计 FAIL 不得晋级"纪律）。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..audit.rules import audit_batch, audit_comp, audit_mult
from ..audit.types import AnalysisSpec
from ..pep.ledger import AuditLedger
from ..pep.types import AuditEvent
from .rtools import RSCRIPT, _R_SCRIPT

DEFAULT_LEDGER_PATH = Path(__file__).resolve().parents[3] / "var" / "audit" / "research.db"


class AuditGateError(RuntimeError):
    pass


def _digest(frame: pd.DataFrame) -> str:
    return hashlib.sha256(frame.to_csv(index=False).encode("utf-8")).hexdigest()


def _audit_verdicts(result: pd.DataFrame, exposure: pd.Series, covariates: pd.DataFrame,
                    family_id: str = "") -> list[dict]:
    spec = AnalysisSpec(
        pvalues=[float(p) for p in result["p"]],
        method="BH",
        alpha=0.05,
        claimed_adjusted=[float(q) for q in result["q"]],
        family_id=family_id or str(exposure.name),
        exposure=[float(v) if pd.notna(v) else None for v in exposure.tolist()[:2000]],
        batch=covariates["Batch"].astype(str).tolist()[:2000] if "Batch" in covariates.columns else None,
        # 成分性审计声明为不适用：秩残差化偏 Spearman 不做 CLR/ILR 变换，
        # 不存在需复现的对数比矩阵（此前喂暴露行占位属伪声明，负值暴露会误伤 FAIL）。
    )
    findings = [audit_mult(spec), audit_batch(spec), audit_comp(spec)]
    return [{"rule": f.rule_id, "verdict": f.verdict.value, "details": f.details}
            for f in findings]


def run_gated_association(
    exposure: pd.Series,
    features: pd.DataFrame,
    covariates: pd.DataFrame,
    *,
    run_id: str,
    ledger_path: Path | None = None,
    max_memory_mb: int = 2048,
    cpu_quota_percent: int = 100,
    timeout_seconds: int = 900,
    tmp_builder=None,
) -> tuple[pd.DataFrame, list[dict]]:
    """受治理的 R 关联执行：返回 (结果表, 审计verdicts)。FAIL 抛 AuditGateError。

    常数（零方差）暴露在 R 执行前即拒绝并入账 deny——伪相关守卫第二道闸，
    与 rtools 的 ValueError 守卫互为冗余（执行不可绕过治理）。"""
    import tempfile

    ledger = AuditLedger(str(ledger_path or DEFAULT_LEDGER_PATH))
    event_id, request_id = uuid.uuid4().hex, uuid.uuid4().hex
    constraints = {"max_memory_mb": max_memory_mb, "cpu_quota_percent": cpu_quota_percent}
    numeric = pd.to_numeric(exposure, errors="coerce")
    if numeric.nunique() < 2:
        ledger.record_event(AuditEvent(
            id=event_id, ts=datetime.now(timezone.utc).isoformat(),
            principal_type="agent", principal_id="research-loop",
            action="execute_task", resource_kind="r_analysis",
            resource_id=f"{exposure.name}", request_id=request_id,
            subject_hash=hashlib.sha256(str(exposure.name).encode()).hexdigest()[:16],
            decision="deny", approval_required=False,
            reason_codes=("constant-exposure",), constraints=constraints,
        ))
        raise AuditGateError(
            f"暴露列 {exposure.name} 为常数（非缺失唯一值 {int(numeric.nunique())} 个），审计 FAIL 拒绝执行")
    event = AuditEvent(
        id=event_id, ts=datetime.now(timezone.utc).isoformat(),
        principal_type="agent", principal_id="research-loop",
        action="execute_task", resource_kind="r_analysis",
        resource_id=f"{exposure.name}", request_id=request_id,
        subject_hash=hashlib.sha256(str(exposure.name).encode()).hexdigest()[:16],
        decision="allow", approval_required=False,
        reason_codes=("research-mvp-gate",), constraints=constraints,
    )
    ledger.record_event(event)

    # R 可用性检查置于零方差守卫之后：无 R 环境下常数暴露仍须先入账 constant-exposure
    # deny（"无 R 也可回归"契约，监工 G3 复审 C1，2026-10-07）
    if not RSCRIPT.is_file():
        raise AuditGateError(
            f"R 可执行文件不可用（RSCRIPT_BIN 未设且 PATH 无 Rscript，解析为 {RSCRIPT}）"
            "——受治理的关联执行需要 R")
    with tempfile.TemporaryDirectory(prefix="mra_gate_") as tmp:
        tmp = Path(tmp)
        exposure.rename("exposure").to_frame().to_csv(tmp / "exp.tsv", sep="\t")
        features.to_csv(tmp / "feat.tsv", sep="\t")
        covariates.to_csv(tmp / "cov.tsv", sep="\t")
        script = tmp / "assoc.R"
        script.write_text(_R_SCRIPT, encoding="utf-8")
        out = tmp / "res.tsv"
        argv = [str(RSCRIPT), str(script), str(tmp / "exp.tsv"), str(tmp / "feat.tsv"),
                str(tmp / "cov.tsv"), str(out)]
        from ..pep.systemd_runner import systemd_scope_runner
        try:
            proc = systemd_scope_runner(argv[0], argv[1:], constraints, timeout_seconds)
        except OSError:
            constraints["fallback"] = "plain-subprocess"
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_seconds)
        if proc.returncode != 0 or not out.is_file():
            from dataclasses import replace
            ledger.record_event(replace(event, id=uuid.uuid4().hex, decision="deny",
                                        reason_codes=("r-failed",)))
            raise AuditGateError(f"R 分析失败：{getattr(proc, 'stderr', '')[-500:]}")
        result = pd.read_csv(out, sep="\t")

    verdicts = _audit_verdicts(result, exposure, covariates,
                               family_id=f"assoc:{exposure.name}:{len(result)}")
    ledger.set_output_digest(event_id, _digest(result))
    fails = [v for v in verdicts if v["verdict"].upper().startswith("FAIL")]
    if fails:
        raise AuditGateError(f"统计审计 FAIL：{fails}")
    return result, verdicts
