"""Scientific Capability Registry（G3 / S2）——能力语义与实现的正式分离。

职责（用户裁决边界）：capability definition / implementation mapping /
scientific IO contract / provenance contract / governance metadata /
permission & side-effect metadata / availability & version。
**不负责**：tool dispatch、session runtime、streaming、通用重试编排、
通用客户端协议、通用 agent loop——全部由 upstream Harness/runtime 承担。

设计约束：
- capability_id 稳定（Python↔MCP、PubMed↔EuropePMC、standalone↔dsh 切换不变）；
- Planner/调用方只面向 capability_id，不感知 implementation_id；
- side_effect 三值枚举 READ_ONLY / WORKSPACE_WRITE / EXTERNAL_WRITE，
  EXTERNAL_WRITE 注册即要求 governance_level="governed"（更高权限治理路径）；
- Registry 本体零 dsh import（Scientific Core）；MCP/loop 面经 invoke() 调用。
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 四级语义（用户裁决 2026-09-24）：查询已有状态 / 计算产生临时结果但不改科研状态 /
# 改变 Scientific Workspace 或 Evidence 状态 / 改变 Harness 外部系统状态。
# 纯科学计算不得因产生输出即被视为 WORKSPACE_WRITE。
SIDE_EFFECTS = ("READ_ONLY", "COMPUTE_ONLY", "WORKSPACE_WRITE", "EXTERNAL_WRITE")
GOVERNANCE_LEVELS = ("open", "guarded", "governed")
TRANSPORTS = ("python-inproc", "mcp", "cli", "http")

ImplFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]


class CapabilityImplementation(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    capability_id: str = Field(min_length=3, pattern=r"^[a-z][a-z0-9_.-]*$")
    capability_version: str = Field(min_length=1)
    implementation_id: str = Field(min_length=3, pattern=r"^[a-z][a-z0-9_.-]*$")
    implementation_version: str = Field(min_length=1)
    transport: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    provenance_contract: dict[str, Any]
    governance_level: str = "open"
    side_effect: str = "READ_ONLY"
    deterministic: bool = True
    auth_scope: list[str] = Field(default_factory=list)
    availability: str = "available"   # available / degraded / unavailable
    timeout_policy: str = "default"
    retry_policy: str = "none"
    validation_status: str = "validated"   # validated / experimental / draft
    priority: int = 100                    # 同能力多实现时的默认选择序（小者优先）
    _fn: ImplFn | None = None              # python-inproc 实现的可调用体（非序列化字段）

    @field_validator("transport")
    @classmethod
    def _tr(cls, v: str) -> str:
        if v not in TRANSPORTS:
            raise ValueError(f"transport 须为 {TRANSPORTS}")
        return v

    @field_validator("side_effect", "governance_level", "availability", "validation_status")
    @classmethod
    def _enums(cls, v: str, info) -> str:
        allowed = {"side_effect": SIDE_EFFECTS, "governance_level": GOVERNANCE_LEVELS,
                   "availability": ("available", "degraded", "unavailable"),
                   "validation_status": ("validated", "experimental", "draft")}[info.field_name]
        if v not in allowed:
            raise ValueError(f"{info.field_name} 须为 {allowed}")
        return v

    @field_validator("governance_level")
    @classmethod
    def _external_write_gate(cls, v: str, info) -> str:
        # 模型级校验拿不到兄弟字段，External 写入的强治理约束在 Registry.register 再拦
        return v


class CapabilityRegistry:
    """能力注册表：capability → implementations（可替换），调用方只给 capability_id。"""

    def __init__(self) -> None:
        self._by_capability: dict[str, list[CapabilityImplementation]] = {}

    def register(self, impl: CapabilityImplementation, fn: ImplFn | None = None) -> None:
        if impl.side_effect == "EXTERNAL_WRITE" and impl.governance_level != "governed":
            raise ValueError("EXTERNAL_WRITE 实现必须 governance_level=governed（更高权限治理路径）")
        if impl.transport == "python-inproc" and fn is None:
            raise ValueError("python-inproc 实现必须提供可调用体")
        object.__setattr__(impl, "_fn", fn)
        self._by_capability.setdefault(impl.capability_id, []).append(impl)
        self._by_capability[impl.capability_id].sort(key=lambda i: (i.priority, i.implementation_id))

    def list_capabilities(self) -> list[str]:
        return sorted(self._by_capability)

    def implementations(self, capability_id: str) -> list[CapabilityImplementation]:
        return list(self._by_capability.get(capability_id, []))

    def resolve(self, capability_id: str, implementation_id: str | None = None) -> CapabilityImplementation:
        impls = [i for i in self.implementations(capability_id)
                 if i.availability == "available"]
        if not impls:
            raise KeyError(f"capability {capability_id} 无可用实现")
        if implementation_id is None:
            return impls[0]
        for i in impls:
            if i.implementation_id == implementation_id:
                return i
        raise KeyError(f"capability {capability_id} 无实现 {implementation_id}")

    def invoke(self, capability_id: str, payload: dict[str, Any], *,
               implementation_id: str | None = None,
               context: dict[str, Any] | None = None) -> dict[str, Any]:
        """按能力调用；implementation 可替换而 capability_id 不变。"""
        impl = self.resolve(capability_id, implementation_id)
        if getattr(impl, "_fn", None) is None:
            raise RuntimeError(f"实现 {impl.implementation_id} 非进程内可调用"
                               f"（transport={impl.transport}，经对应通道调度）")
        result = impl._fn(payload, context or {})
        if not isinstance(result, dict):
            raise TypeError("capability 实现必须返回 dict（output contract）")
        return result

    def catalog(self) -> list[dict[str, Any]]:
        """导出目录（不含可调用体）——供 dsh adapter / 客户端发现。"""
        return [{k: getattr(i, k) for k in CapabilityImplementation.model_fields}
                for cid in self.list_capabilities() for i in self.implementations(cid)]


def _fn_method_query(payload: dict, ctx: dict) -> dict:
    from .knowledge.method_rules import search_method_rules
    rules = search_method_rules(payload["query"], k=int(payload.get("k", 5)))
    return {"source_type": "METHOD_KNOWLEDGE", "n_rules": len(rules), "rules": rules}


def _fn_knowledge_route(payload: dict, ctx: dict) -> dict:
    from .knowledge.router import route
    graph = ctx.get("graph")
    if graph is None:
        raise ValueError("knowledge.route 需要 context.graph")
    return route(graph, payload["term"], payload.get("question", payload["term"]),
                 knowledge_type=payload.get("knowledge_type", "auto"),
                 hops=int(payload.get("hops", 1)),
                 max_results=int(payload.get("max_results", 10)))


def _fn_gap_check(payload: dict, ctx: dict) -> dict:
    from .knowledge.gap import detect_gaps
    graph = ctx.get("graph")
    if graph is None:
        raise ValueError("gap.check 需要 context.graph")
    return detect_gaps(graph, entities=list(payload.get("entities", [])),
                       analysis_types=list(payload.get("analysis_types", [])))


def _fn_kg_resolve(payload, ctx):
    from .kg.tools import build_tool_functions
    return build_tool_functions(ctx["graph"])["kg_resolve"]({"term": payload["term"]})


def _fn_kg_neighbors(payload, ctx):
    from .kg.tools import build_tool_functions
    return build_tool_functions(ctx["graph"])["kg_neighbors"](
        {"term": payload["term"], "hops": int(payload.get("hops", 1)),
         "categories": payload.get("categories")})


def _fn_kg_edge_evidence(payload, ctx):
    from .kg.tools import build_tool_functions
    return build_tool_functions(ctx["graph"])["kg_edge_evidence"](
        {"subject": payload["subject"], "object": payload["object"]})


def _fn_vec_query(payload, ctx):
    from . import vecstore
    hits = vecstore.query(payload["text"], payload.get("table", "kg_entities"),
                          k=int(payload.get("k", 8)))
    return {"table": payload.get("table", "kg_entities"), "hits": hits}


def _fn_literature_search(payload, ctx):
    from .research.litread import search_and_read
    return search_and_read(payload["query"], payload.get("question", payload["query"]),
                           max_results=int(payload.get("max_results", 20)),
                           max_calls=int(payload.get("max_calls", 0)))  # 默认纯元数据(零LLM)


def _fn_workspace_record_execution(payload, ctx):
    from .isolation import guard_workspace_write
    from .workspace import ToolExecution, Workspace
    guard_workspace_write(payload["study_id"], ctx, scope="write_evidence")
    ws = Workspace(payload["study_id"], root=ctx.get("workspace_root"))
    ws.append(ToolExecution(**payload["record"]))
    return {"study_id": payload["study_id"], "committed": True}


def _fn_workspace_record_evidence(payload, ctx):
    from .workspace import Evidence, Workspace
    ws = Workspace(payload["study_id"], root=ctx.get("workspace_root"))
    ws.append(Evidence(**payload["record"]))
    return {"study_id": payload["study_id"], "committed": True}


def _fn_association_partial_spearman(payload, ctx):
    """COMPUTE_ONLY：治理门内的偏 Spearman 计算 → CandidateResult（不写 Evidence）。"""
    import pandas as pd
    from .research import datasources as ds
    from .research.gate import run_gated_association
    from .workspace import CandidateResult, digest

    exposure = payload["exposure"]
    features = payload.get("features", "species")
    exposure_table = payload.get("exposure_table") or next(iter(ds.load_config()["exposures"]))
    max_features = int(payload.get("max_features", 50))
    q_threshold = float(payload.get("q_threshold", 0.05))
    exp = ds.load_exposures(exposure_table)
    ft = ds.load_features(features)
    meta = ds.load_metadata()
    covs = meta[[c for c in ds.default_covariates() if c in meta.columns]]
    ids = ds.intersect_ids(exp, ft, covs)
    keep = (pd.to_numeric(exp.loc[ids, exposure], errors="coerce").notna()
            & covs.loc[ids].notna().all(axis=1))
    ids = [i for i, k in zip(ids, keep) if k]
    sel = ds.top_features_by_prevalence(ft.loc[ids], max_features=max_features)
    result, verdicts = run_gated_association(
        pd.to_numeric(exp.loc[ids, exposure]), ft.loc[ids, sel], covs.loc[ids],
        run_id=payload.get("run_id", "compute"))
    hits = result[result["q"] < q_threshold]
    candidate = CandidateResult(
        analysis_id=payload.get("analysis_id") or f"assoc-{exposure}-{features}",
        capability_id="association.partial_spearman", implementation_id="mra.r",
        capability_version="1.0.0", implementation_version="1.0.0",
        input_fingerprint=digest({"exposure_table": exposure_table, "exposure": exposure,
                                  "features": features, "n": len(ids),
                                  "max_features": max_features}),
        output_summary=f"n={len(ids)}; tested={len(result)}; significant(q<{q_threshold})={len(hits)}",
        effect_estimate={"n_significant": int(len(hits)),
                         "top": [{"feature": r["feature"][:80], "rho": round(float(r["rho"]), 4),
                                  "q": float(r["q"])} for _, r in hits.head(5).iterrows()]},
        uncertainty={"bh_family_size": int(len(result)), "q_threshold": q_threshold},
        assumptions_checked=["零方差守卫(四道闸)", "样本对齐(AUDIT-BATCH-001)", "BH族内校正"],
        warnings=[],
        provenance={"execution_verdicts": [f"{v['rule']}:{v['verdict']}" for v in verdicts],
                    "n_samples": int(result["n"].iloc[0]) if len(result) else None,
                    "exposure_table": exposure_table, "features": features},
        deterministic=True)
    return {"candidate": candidate.model_dump(), "execution_verdicts": verdicts}


def _latest_evidence_event(ws, evidence_id):
    latest = None
    for ev in ws.events():
        if ev["record_type"] == "Evidence" and ev["record"]["evidence_id"] == evidence_id:
            latest = ev
    return latest


def _mutate_evidence(payload, ctx, *, falsification=None, canonical=None):
    from .isolation import guard_workspace_write
    from .workspace import Workspace
    guard_workspace_write(payload["study_id"], ctx, scope="mutate_evidence")
    ws = Workspace(payload["study_id"], root=ctx.get("workspace_root"))
    prev = _latest_evidence_event(ws, payload["evidence_id"])
    if prev is None:
        raise ValueError(f"Evidence {payload['evidence_id']} 不存在，无法变更")
    if not payload.get("reason"):
        raise ValueError("Scientific State Mutation 必须携带 reason")
    rec = dict(prev["record"])
    # B4.1 同义变更去重：目标状态与理由均未变化的重复请求不是新科研事件
    # （恢复重试/ACK 丢失）→ already_applied；新 rationale/新裁决 → 允许新事件。
    target_fals = falsification if falsification is not None \
        else rec.get("falsification", "none")
    target_canon = canonical if canonical is not None else rec.get("canonical", False)
    if rec.get("falsification", "none") == target_fals and \
            rec.get("canonical", False) == target_canon and \
            rec.get("reason") == payload["reason"]:
        return {"study_id": payload["study_id"], "evidence_id": payload["evidence_id"],
                "supersedes_seq": prev["seq"], "committed": False,
                "already_applied": True}
    gov = dict(rec.get("governance") or {})
    gov["last_mutation"] = {"actor": payload.get("actor", "unknown"),
                            "reason": payload["reason"],
                            "supersedes_seq": prev["seq"],
                            "at": __import__("datetime").datetime.now(
                                __import__("datetime").timezone.utc).isoformat()}
    rec.update(governance=gov, supersedes_seq=prev["seq"],
               reason=payload["reason"], falsification=falsification
               if falsification is not None else rec.get("falsification", "none"),
               canonical=canonical if canonical is not None else rec.get("canonical", False))
    ws.append(__import__("mra.workspace", fromlist=["Evidence"]).Evidence(**rec))
    return {"study_id": payload["study_id"], "evidence_id": payload["evidence_id"],
            "supersedes_seq": prev["seq"], "committed": True,
            "already_applied": False}


def _fn_ws_revise(payload, ctx):
    return _mutate_evidence(payload, ctx)


def _fn_ws_mark_downgraded(payload, ctx):
    return _mutate_evidence(payload, ctx, falsification="downgraded")


def _fn_ws_mark_refuted(payload, ctx):
    return _mutate_evidence(payload, ctx, falsification="refuted")


def _fn_ws_set_canonical(payload, ctx):
    caller = ctx.get("caller_capability", "")
    if caller.startswith("association.") or ctx.get("caller_side_effect") == "COMPUTE_ONLY":
        raise ValueError("铁律4：compute capability 不得直接调用 set_canonical")
    if not payload.get("supporting_lineage"):
        raise ValueError("铁律3：set_canonical 必须引用 supporting evidence lineage")
    from .workspace import Workspace
    ws = Workspace(payload["study_id"], root=ctx.get("workspace_root"))
    if payload.get("decision_id"):  # 升级为账本可验证 canonical 裁决
        _verify = None
        for ev in ws.events():
            if ev["record_type"] == "GovernanceDecision" and \
                    ev["record"]["decision_id"] == payload["decision_id"]:
                _verify = ev["record"]
        if _verify is None or not _verify.get("canonical_eligible"):
            raise ValueError("set_canonical 须提供 canonical_eligible=true 的有效裁决")
    elif not payload.get("revalidation_governance_event"):
        raise ValueError("set_canonical 须提供 decision_id（canonical 裁决）或再验证治理事件")
    prev = _latest_evidence_event(ws, payload["evidence_id"])
    if prev is None:
        raise ValueError(f"Evidence {payload['evidence_id']} 不存在")
    current = prev["record"].get("falsification", "none")
    if current == "refuted" and not payload.get("revalidation_governance_event"):
        raise ValueError("铁律1/2：refuted 不得静默恢复 canonical；再升级必须生成新治理事件")
    return _mutate_evidence({**payload, "reason": payload.get("reason") or "set canonical"},
                            ctx, canonical=True)


def _fn_atlas_single_exposure_scan(payload, ctx):
    """COMPUTE_ONLY：单暴露 × 多特征表全景扫描（多重检验输出，多结果候选）。"""
    import pandas as pd
    from .research import datasources as ds
    from .research.atlas import grade_hit
    from .research.gate import run_gated_association
    from .workspace import CandidateResult, digest

    exposure = payload["exposure"]
    exposure_table = payload.get("exposure_table") or next(iter(ds.load_config()["exposures"]))
    feature_tables = tuple(payload.get("feature_tables", ("species", "pathway", "fungal", "viral")))
    max_features = int(payload.get("max_features", 100))
    q_threshold = float(payload.get("q_threshold", 0.05))
    graph = ctx.get("graph")

    exp = ds.load_exposures(exposure_table)
    metadata = ds.load_metadata()
    covs = metadata[[c for c in ds.default_covariates() if c in metadata.columns]]
    all_verdicts: list[str] = []
    payload_hits: dict[str, list] = {}
    metrics: dict[str, int] = {}
    for feat_name in feature_tables:
        feats = ds.load_features(feat_name)
        if feat_name in ("species", "fungal", "viral"):
            feats = feats.loc[:, [c for c in feats.columns
                                  if c.split("|")[-1].startswith("s__")]]
        ids = ds.intersect_ids(exp, feats, covs)
        keep = (pd.to_numeric(exp.loc[ids, exposure], errors="coerce").notna()
                & covs.loc[ids].notna().all(axis=1))
        ids = [i for i, k in zip(ids, keep) if k]
        sel = ds.top_features_by_prevalence(feats.loc[ids], max_features=max_features)
        try:
            result, verdicts = run_gated_association(
                pd.to_numeric(exp.loc[ids, exposure]), feats.loc[ids, sel],
                covs.loc[ids], run_id=payload.get("run_id", "atlas-scan"))
        except RuntimeError as exc:
            all_verdicts.append(f"{feat_name}:DENIED({str(exc)[:40]})")
            metrics[f"{feat_name}_tested"] = 0
            payload_hits[feat_name] = []
            continue
        all_verdicts += [f"{feat_name}:{v['rule']}:{v['verdict']}" for v in verdicts]
        hits = result[result["q"] < q_threshold]
        metrics[f"{feat_name}_tested"] = int(len(result))
        metrics[f"{feat_name}_hits"] = int(len(hits))
        rows = []
        for _, r in hits.iterrows():
            rows.append({"feature": r["feature"][:100], "rho": round(float(r["rho"]), 4),
                         "q": float(r["q"]), "n": int(r["n"]),
                         "grade": (grade_hit(graph, r["feature"], exposure_table,
                                             exposure, float(r["rho"]))
                                   if graph is not None else "")})
        payload_hits[feat_name] = rows
    candidate = CandidateResult(
        analysis_id=payload.get("analysis_id") or f"atlas-{exposure}",
        capability_id="atlas.single_exposure_scan", implementation_id="mra.r",
        capability_version="1.0.0", implementation_version="1.0.0",
        input_fingerprint=digest({"exposure_table": exposure_table, "exposure": exposure,
                                  "feature_tables": feature_tables,
                                  "max_features": max_features, "q": q_threshold}),
        output_summary="; ".join(f"{k}={v}" for k, v in metrics.items()),
        result_type="atlas_scan",
        result_schema="payload.{table}[]= {feature,rho,q,n,grade}",
        result_payload=payload_hits, metrics=metrics,
        assumptions_checked=["零方差守卫", "样本对齐", "BH族内校正(每暴露×表族)"],
        provenance={"execution_verdicts": all_verdicts,
                    "multiple_testing": "BH within (exposure × feature_table) family"},
        deterministic=True)
    return {"candidate": candidate.model_dump(), "execution_verdicts": all_verdicts}


def _fn_diversity_alpha_shannon(payload, ctx):
    """COMPUTE_ONLY：α 多样性（Shannon）按样本计算 → CandidateResult（diversity 形态）。"""
    import numpy as np
    from .research import datasources as ds
    from .workspace import CandidateResult, digest

    features = payload.get("features", "species")
    ft = ds.load_features(features)
    if features in ("species", "fungal", "viral"):
        ft = ft.loc[:, [c for c in ft.columns if c.split("|")[-1].startswith("s__")]]
    rel = ft.div(ft.sum(axis=1).replace(0, np.nan), axis=0)
    shannon = -(rel * np.log(rel)).sum(axis=1, skipna=True)
    shannon = shannon.dropna()
    q = float(np.quantile(shannon, [0.25, 0.5, 0.75])[1])
    candidate = CandidateResult(
        analysis_id=payload.get("analysis_id") or f"alpha-{features}",
        capability_id="diversity.alpha_shannon", implementation_id="mra.numpy",
        capability_version="1.0.0", implementation_version="1.0.0",
        input_fingerprint=digest({"features": features, "n_samples": int(ft.shape[0])}),
        output_summary=f"n={len(shannon)}; median_shannon={q:.4f}",
        result_type="diversity_alpha",
        result_payload={"median": float(q),
                        "iqr": [float(np.quantile(shannon, 0.25)),
                                float(np.quantile(shannon, 0.75))]},
        metrics={"n_samples": int(len(shannon)), "median_shannon": round(float(q), 4)},
        assumptions_checked=["按样本TSS相对化", "skipna"],
        provenance={"features": features, "n_samples": int(ft.shape[0])},
        deterministic=True)
    return {"candidate": candidate.model_dump(), "execution_verdicts": []}


def build_default_registry() -> CapabilityRegistry:
    """S2 第一批 Golden×3 + Track B 只读批 + workspace.* 高治理变更能力。"""
    reg = CapabilityRegistry()

    def _impl(cap: str, impl: str, transport: str, priority: int, fn,
              input_s: dict, output_s: dict, prov: dict, **kw):
        fields = dict(side_effect="READ_ONLY", deterministic=True,
                      auth_scope=[], availability="available",
                      timeout_policy="default", retry_policy="none",
                      validation_status="validated")
        fields.update(kw)  # 调用侧可覆盖任意治理/行为字段
        return CapabilityImplementation(
            capability_id=cap, capability_version="1.0.0",
            implementation_id=impl, implementation_version="1.0.0",
            transport=transport, input_schema=input_s, output_schema=output_s,
            provenance_contract=prov, priority=priority, **fields), fn

    # A. method.query —— METHOD_KNOWLEDGE 基准能力（S1 切片已验）
    reg.register(*_impl(
        "method.query", "mra.method_rules", "python-inproc", 10, _fn_method_query,
        {"query": "string", "k": "int=5"},
        {"source_type": "METHOD_KNOWLEDGE", "n_rules": "int", "rules": "list[dict]"},
        {"source_type_field": "source_type",
         "required": ["rule_id", "validation_status", "provenance"]}))
    reg.register(*_impl(
        "method.query", "mcp.mra", "mcp", 20, _fn_method_query,
        {"query": "string", "k": "int=5"},
        {"source_type": "METHOD_KNOWLEDGE", "n_rules": "int", "rules": "list[dict]"},
        {"source_type_field": "source_type",
         "required": ["rule_id", "validation_status", "provenance"]}))

    # B. knowledge.route —— LOCAL_KG 优先 / miss 受控降级 EXTERNAL_LIVE（S2 切片）
    _route_in = {"term": "string", "question": "string", "knowledge_type":
                 "auto|phage_host|gene_protein_function|taxonomy|pathway_annotation|literature|method",
                 "hops": "int=1", "max_results": "int=10"}
    _route_out = {"status": "OK|LIVE_UNAVAILABLE", "source_type":
                  "LOCAL_KG|EXTERNAL_LIVE|LITERATURE|METHOD_KNOWLEDGE",
                  "provenance": "dict", "evidence_status": "string"}
    _route_prov = {"source_type_field": "source_type",
                   "required": ["provenance"], "live_note":
                   "miss→LITERATURE 降级须携带 retrieved_at/query/source_id"}
    reg.register(*_impl("knowledge.route", "mra.router", "python-inproc", 10,
                        _fn_knowledge_route, _route_in, _route_out, _route_prov))
    reg.register(*_impl("knowledge.route", "mcp.mra", "mcp", 20,
                        _fn_knowledge_route, _route_in, _route_out, _route_prov))

    # C. gap.check —— 实体/方法双探测，只建议不动手（S2 切片）
    _gap_in = {"entities": "list[string]", "analysis_types": "list[string]"}
    _gap_out = {"overall": "string", "entities": "list[dict]",
                "analysis_types": "list[dict]"}
    _gap_prov = {"source_type_field": None,
                 "guarantees": ["不写 KG", "不修改 Workspace Evidence", "只读探测"]}
    reg.register(*_impl("gap.check", "mra.gap", "python-inproc", 10,
                        _fn_gap_check, _gap_in, _gap_out, _gap_prov))
    reg.register(*_impl("gap.check", "mcp.mra", "mcp", 20,
                        _fn_gap_check, _gap_in, _gap_out, _gap_prov))

    # ---- Track B：READ_ONLY 批量迁移（零 Registry schema 改动） ----
    _kg_prov = {"source_type_field": "evidence_tier",
                "required": ["evidence_tier", "pmids"], "note": "结果强制携带证据分级"}
    for cap, impl_id, fn, in_s, out_s, prio in [
        ("kg.resolve", "mra.kg", _fn_kg_resolve,
         {"term": "string"}, {"candidates": "list"}, 10),
        ("kg.neighbors", "mra.kg", _fn_kg_neighbors,
         {"term": "string", "hops": "int=1", "categories": "list[str]?"},
         {"center": "string", "neighbors": "dict", "counts_by_category": "dict"}, 10),
        ("kg.edge_evidence", "mra.kg", _fn_kg_edge_evidence,
         {"subject": "string", "object": "string"},
         {"edges": "list[dict]"}, 10),
        ("vec.query", "mra.vecstore", _fn_vec_query,
         {"text": "string", "table": "str=kg_entities", "k": "int=8"},
         {"table": "string", "hits": "list"}, 10),
        ("literature.search", "mra.litread", _fn_literature_search,
         {"query": "string", "question": "string", "max_results": "int=20",
          "max_calls": "int=0"},
         {"papers": "list", "provenance": "dict", "n_papers": "int"}, 10),
    ]:
        prov = (_kg_prov if cap.startswith("kg.")
                else {"source_type_field": "provenance.source_type",
                      "required": ["provenance"],
                      "note": "EXTERNAL_LIVE 缓存≠入库"})
        extra = ({"deterministic": False,
                  "auth_scope": ["NCBI_API_KEY(可选)", "ARK_API_KEY(速读)"]}
                 if cap == "literature.search" else {})
        reg.register(*_impl(cap, impl_id, "python-inproc", prio, fn, in_s, out_s,
                            prov, **extra))

    # ---- workspace.* 高治理变更能力：计算工具说"算出了什么"，
    #      governance 决定"什么成为正式证据"（WORKSPACE_WRITE + guarded） ----
    _ws_prov = {"lineage_required": True,
                "note": "commit 须携带 provenance 与（证据类）lineage/falsification"}
    # record_*：提交通道（record_evidence 强制治理门裁决——不可绕过）
    reg.register(*_impl("workspace.record_execution", "mra.workspace", "python-inproc",
                        10, _fn_workspace_record_execution,
                        {"study_id": "string", "record": "ToolExecution-dict"},
                        {"study_id": "string", "committed": "bool"}, _ws_prov,
                        side_effect="WORKSPACE_WRITE", governance_level="guarded"))

    def _verify_decision(ws, decision_id, candidate_id=None, need_canonical=False):
        """账本六验：存在/同候选/指纹一致/allow/未失效/lineage。

        B4.1：裁决带 task scope 时按 (task, analysis_id) 精确解析候选；
        无域裁决遇到跨 task 同名候选时拒绝歧义解析（身份域铁律）。
        """
        events = ws.events()
        decision = None
        for ev in events:
            if ev["record_type"] == "GovernanceDecision" and \
                    ev["record"]["decision_id"] == decision_id:
                decision = ev["record"]
        if decision is None:
            raise ValueError(f"裁决 {decision_id} 不在 Scientific Ledger（裸 allow_evidence 不再被接受）")
        scope = decision.get("research_task_id", "")
        matches = [e for e in events
                   if e["record_type"] == "CandidateResult"
                   and e["record"]["analysis_id"] == decision["analysis_id"]]
        if scope:
            cand_ev = next((e for e in matches
                            if e["record"].get("research_task_id", "") == scope), None)
        elif len(matches) > 1:
            raise ValueError(
                f"候选 {decision['analysis_id']} 跨 task 重复而裁决未带 task scope——"
                f"拒绝歧义解析（B4.1：裁决与候选须同域）")
        else:
            cand_ev = matches[0] if matches else None
        if cand_ev is None:
            raise ValueError(f"裁决对应候选 {decision['analysis_id']} 不在账本"
                             f"（task scope={scope or '无'}）")
        if decision["candidate_event_seq"] != cand_ev["seq"]:
            raise ValueError("裁决引用的候选事件 seq 不符")
        from .workspace import digest as _digest
        if decision["candidate_hash"] != _digest(cand_ev["record"]):
            raise ValueError("候选指纹与裁决时不一致（候选被篡改或版本错位）")
        if candidate_id is not None and decision["analysis_id"] != candidate_id:
            raise ValueError("裁决与提交的 candidate_id 不对应")
        later = [e for e in events if e["record_type"] == "GovernanceDecision"
                 and e["record"].get("supersedes_decision_id") == decision_id]
        if later or not decision.get("valid", True):
            raise ValueError(f"裁决 {decision_id} 已失效（被再裁决取代）")
        if not decision["allow_evidence"]:
            raise ValueError("裁决 allow_evidence=False——拒绝提交")
        if need_canonical and not decision["canonical_eligible"]:
            raise ValueError("canonical 裁决缺失（canonical_eligible≠true）")
        return decision

    def _fn_record_evidence_gated(payload, ctx):
        from .isolation import guard_workspace_write
        from .workspace import Workspace
        guard_workspace_write(payload["study_id"], ctx, scope="write_evidence")
        ws = Workspace(payload["study_id"], root=ctx.get("workspace_root"))
        if not payload.get("decision_id"):
            raise ValueError("record_evidence 须提供 decision_id（账本六验；"
                             "裸 allow_evidence 不再被接受）")
        record = dict(payload["record"])
        decision = _verify_decision(ws, payload["decision_id"],
                                    candidate_id=record.get("candidate_id"))
        # B4.1 幂等提交：同 (evidence_id, 提交内容, decision) 的重试不是新科研事件
        # （网络重试/进程恢复/ACK 丢失/caller 不确定上一次是否成功）→ already_committed。
        # 只有新 GovernanceDecision 或新科学内容（claim/effect 变化）才允许新事件。
        # 两侧内容须经同一 Evidence 模型归一化（默认字段填充）并剥离易变字段后比较。
        from .workspace import Evidence as _Evidence
        submitted = _Evidence(**record).model_dump()
        volatile = ("governance", "created_at")
        idem_body = {k: v for k, v in submitted.items() if k not in volatile}
        for ev in ws.events():
            if ev["record_type"] != "Evidence":
                continue
            stored = ev["record"]
            if stored.get("evidence_id") != record.get("evidence_id"):
                continue
            stored_body = {k: v for k, v in stored.items() if k not in volatile}
            same_decision = (stored.get("governance") or {}).get(
                "decision", {}).get("decision_id") == decision["decision_id"]
            if same_decision and stored_body == idem_body:
                return {"study_id": payload["study_id"], "committed": False,
                        "already_committed": True,
                        "evidence_id": record.get("evidence_id"),
                        "event_seq": ev["seq"]}
        rec_gov = dict(record.get("governance") or {})
        rec_gov["decision"] = {"decision_id": decision["decision_id"],
                               "policy_id": decision["policy_id"],
                               "policy_version": decision["policy_version"],
                               "checks": decision["checks"]}
        record["governance"] = rec_gov
        out = _fn_workspace_record_evidence({"study_id": payload["study_id"],
                                             "record": record}, ctx)
        return {**out, "already_committed": False}

    reg.register(*_impl("workspace.record_evidence", "mra.workspace", "python-inproc",
                        10, _fn_record_evidence_gated,
                        {"study_id": "string", "record": "Evidence-dict",
                         "decision_id": "string(账本裁决ID,六验必需)"},
                        {"study_id": "string", "committed": "bool"}, _ws_prov,
                        side_effect="WORKSPACE_WRITE", governance_level="guarded"))

    # Scientific State Mutation：revise / downgrade / refute / canonical
    for cap, fn in [("workspace.revise_evidence", _fn_ws_revise),
                    ("workspace.mark_downgraded", _fn_ws_mark_downgraded),
                    ("workspace.mark_refuted", _fn_ws_mark_refuted)]:
        reg.register(*_impl(cap, "mra.workspace", "python-inproc", 10, fn,
                            {"study_id": "string", "evidence_id": "string",
                             "reason": "string(必填)", "actor": "string"},
                            {"study_id": "string", "supersedes_seq": "int",
                             "committed": "bool"}, _ws_prov,
                            side_effect="WORKSPACE_WRITE", governance_level="guarded"))
    reg.register(*_impl("workspace.set_canonical", "mra.workspace", "python-inproc",
                        10, _fn_ws_set_canonical,
                        {"study_id": "string", "evidence_id": "string",
                         "supporting_lineage": "list[string](必填)",
                         "reason": "string", "revalidation_governance_event": "string?"},
                        {"study_id": "string", "supersedes_seq": "int",
                         "committed": "bool"}, _ws_prov,
                        side_effect="WORKSPACE_WRITE", governance_level="governed"))

    # ---- 第二个 COMPUTE_ONLY：atlas 单暴露扫描（多结果/多重检验/图谱定级） ----
    reg.register(*_impl("atlas.single_exposure_scan", "mra.r", "python-inproc", 10,
                        _fn_atlas_single_exposure_scan,
                        {"exposure": "string", "exposure_table": "string?",
                         "feature_tables": "list[str]", "max_features": "int=100",
                         "q_threshold": "float=0.05", "analysis_id": "string?"},
                        {"candidate": "CandidateResult(result_type=atlas_scan)",
                         "execution_verdicts": "list"}, _ws_prov,
                        side_effect="COMPUTE_ONLY", governance_level="guarded"))

    reg.register(*_impl("diversity.alpha_shannon", "mra.numpy", "python-inproc", 10,
                        _fn_diversity_alpha_shannon,
                        {"features": "str=species", "analysis_id": "string?"},
                        {"candidate": "CandidateResult(result_type=diversity_alpha)"},
                        _ws_prov, side_effect="COMPUTE_ONLY", governance_level="guarded"))

    # ---- 首个 COMPUTE_ONLY：Scientific Compute 基准（不写 Evidence） ----
    reg.register(*_impl("association.partial_spearman", "mra.r", "python-inproc", 10,
                        _fn_association_partial_spearman,
                        {"exposure": "string", "features": "str=species",
                         "exposure_table": "string?", "max_features": "int=50",
                         "q_threshold": "float=0.05", "analysis_id": "string?"},
                        {"candidate": "CandidateResult-dict",
                         "execution_verdicts": "list"}, _ws_prov,
                        side_effect="COMPUTE_ONLY", governance_level="guarded"))
    return reg


_DEFAULT: CapabilityRegistry | None = None


def default_registry() -> CapabilityRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = build_default_registry()
    return _DEFAULT


__all__ = ["CapabilityImplementation", "CapabilityRegistry",
           "GOVERNANCE_LEVELS", "SIDE_EFFECTS", "TRANSPORTS",
           "build_default_registry", "default_registry"]
