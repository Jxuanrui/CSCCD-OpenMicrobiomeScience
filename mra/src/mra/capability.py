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
    from .workspace import ToolExecution, Workspace
    ws = Workspace(payload["study_id"], root=ctx.get("workspace_root"))
    ws.append(ToolExecution(**payload["record"]))
    return {"study_id": payload["study_id"], "committed": True}


def _fn_workspace_record_evidence(payload, ctx):
    from .workspace import Evidence, Workspace
    ws = Workspace(payload["study_id"], root=ctx.get("workspace_root"))
    ws.append(Evidence(**payload["record"]))
    return {"study_id": payload["study_id"], "committed": True}


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
    for cap, fn, in_s in [
        ("workspace.record_execution", _fn_workspace_record_execution,
         {"study_id": "string", "record": "ToolExecution-dict"}),
        ("workspace.record_evidence", _fn_workspace_record_evidence,
         {"study_id": "string", "record": "Evidence-dict"}),
    ]:
        reg.register(*_impl(cap, "mra.workspace", "python-inproc", 10, fn, in_s,
                            {"study_id": "string", "committed": "bool"}, _ws_prov,
                            side_effect="WORKSPACE_WRITE", governance_level="guarded"))
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
