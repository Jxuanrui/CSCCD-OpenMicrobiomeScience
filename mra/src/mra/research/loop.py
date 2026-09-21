"""研究循环编排：在受控工具面上迭代，报告从知识状态编译（不从对话历史）。

工具面（参数白名单，M2 首版）：
  kg_neighbors(term, hops, categories)      —— 图谱机制先验（含证据分级）
  kg_edge_evidence(subject, object)         —— 两实体关系证据
  r_association(exposure, features, max_features, q_threshold) —— R 沙箱关联分析
  record_finding(claim, evidence)           —— 显式登记发现
  submit_report(summary)                    —— 收口出报告

LLM planner 接口：planner_fn(state_digest) -> action dict；离线确定性模式用同一
派发器执行预排计划（零 API 回归路径）。每次 LLM 调用经 session.record_llm_call()
计预算（默认上限 50，Q2 决策）。
"""
from __future__ import annotations

import json
from typing import Callable

import pandas as pd

from ..kg.graph import KGGraph
from ..kg.tools import build_tool_functions
from . import datasources as ds
from .rtools import run_partial_spearman
from .session import Finding, ResearchSession

ACTION_NAMES = {"kg_neighbors", "kg_edge_evidence", "r_association",
                "record_finding", "submit_report"}


class ResearchContext:
    """数据 + 图 + 会话的绑定容器，派发器唯一依赖。"""

    def __init__(self, graph: KGGraph, session: ResearchSession):
        self.graph = graph
        self.session = session
        self.kg_tools = build_tool_functions(graph)
        self._association_cache: dict[str, pd.DataFrame] = {}

    # ---------- 工具实现 ----------
    def r_association(self, exposure: str, features: str = None,
                      max_features: int = 100, q_threshold: float = 0.05,
                      exposure_table: str | None = None) -> dict:
        cache_key = f"{exposure_table}:{exposure}|{features}|{max_features}"
        if cache_key not in self._association_cache:
            if exposure_table is None:
                exposure_table = next(iter(ds.load_config()["exposures"]))
            if features is None:
                features = next(iter(ds.load_config()["features"]))
            exposures = ds.load_exposures(exposure_table)
            feature_table = ds.load_features(features)  # 样本×特征
            metadata = ds.load_metadata()
            cov_cols = [c for c in ds.default_covariates() if c in metadata.columns]
            covariates = metadata[cov_cols]
            if exposure not in exposures.columns:
                raise KeyError(f"暴露 {exposure} 不在 {exposure_table}，可选 {list(exposures.columns)[:8]}...")
            ids = ds.intersect_ids(exposures, feature_table, covariates)
            # 完整案例过滤（暴露与协变量 NA 行剔除；特征内 NA 由 R 端逐特征案例剔除）
            keep = exposures.loc[ids, exposure].notna() & covariates.loc[ids].notna().all(axis=1)
            ids = [i for i, k in zip(ids, keep) if k]
            feats = ds.top_features_by_prevalence(
                feature_table.loc[ids], max_features=max_features)
            result = run_partial_spearman(
                exposures.loc[ids, exposure],
                feature_table.loc[ids, feats],
                covariates.loc[ids],
            )
            self._association_cache[cache_key] = result
            self.session.add_artifact(
                f"assoc_{exposure_table}_{exposure}_{features}.tsv",
                result.to_csv(sep="\t", index=False))
        result = self._association_cache[cache_key]
        hits = result[result["q"] < q_threshold].sort_values("q")
        return {
            "exposure": exposure, "features_table": features,
            "n_features_tested": int(len(result)), "n_samples": int(result["n"].iloc[0]),
            "n_significant_q": int(len(hits)),
            "top_hits": hits.head(15).to_dict(orient="records"),
        }


def dispatch(action: dict, ctx: ResearchContext) -> dict:
    """执行单个受控动作；非法动作返回结构化错误（回填给 planner 学习边界）。"""
    name = action.get("tool") or action.get("action")
    if name not in ACTION_NAMES:
        return {"error": f"未知动作 {name}，可选：{sorted(ACTION_NAMES)}"}
    args = action.get("args") or {}
    try:
        if name == "kg_neighbors":
            return ctx.kg_tools["kg_neighbors"](args)
        if name == "kg_edge_evidence":
            return ctx.kg_tools["kg_edge_evidence"](args)
        if name == "r_association":
            return ctx.r_association(**args)
        if name == "record_finding":
            ctx.session.add_finding(Finding(
                claim=args["claim"], tool="manual", inputs=args,
                evidence=args.get("evidence", {})))
            return {"recorded": True, "n_findings": len(ctx.session.state["findings"])}
        if name == "submit_report":
            report = {"summary": args.get("summary", ""),
                      "n_findings": len(ctx.session.state["findings"])}
            ctx.session.finish(report)
            return {"done": True, **report}
    except Exception as exc:  # noqa: BLE001 —— 派发层隔离单动作失败
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"error": "unreachable"}


def state_digest(session: ResearchSession, max_findings: int = 8) -> str:
    """给 planner 的紧凑状态摘要（防上下文膨胀：计数 + 最近发现及其结果片段）。"""
    state = session.state
    recent = state["findings"][-max_findings:]
    lines = [
        f"question: {state['question']}", f"target: {state['target']}",
        f"iterations: {state['iterations']}  llm_calls: {state['llm_calls']}/{state['llm_call_cap']}",
        f"n_findings: {len(state['findings'])}",
    ]
    for f in recent:
        evidence = f.get("evidence") or {}
        result_snippet = str(evidence.get("result", ""))[:400]
        suffix = f" | result: {result_snippet}" if result_snippet else ""
        lines.append(f"- [{f['tool']}] {str(f['claim'])[:100]}{suffix}")
    return "\n".join(lines)


def run_session(
    question: str,
    target: str,
    graph: KGGraph,
    offline_plan: list[dict] | None = None,
    planner_fn: Callable[[str], dict] | None = None,
    max_iterations: int = 12,
    session: ResearchSession | None = None,
) -> ResearchSession:
    """运行研究循环。offline_plan（确定性）与 planner_fn（LLM）二选一。"""
    session = session or ResearchSession(question=question, target=target)
    ctx = ResearchContext(graph=graph, session=session)
    session.set_plan(offline_plan or [{"tool": "planner", "args": {"mode": "llm"}}])

    if offline_plan is not None:
        for action in offline_plan:
            result = dispatch(action, ctx)
            if "error" in result:
                ctx.session.add_finding(Finding(
                    claim=f"动作失败：{action.get('tool')} -> {result['error'][:200]}",
                    tool="planner_step_error", inputs=action))
            session.state["iterations"] += 1
            session.save()
        if session.state["status"] != "done":
            session.finish({"summary": "离线计划执行完毕", "n_findings": len(session.state["findings"])})
        return session

    if planner_fn is None:
        raise ValueError("需要 offline_plan 或 planner_fn 之一")
    while session.state["status"] == "running" and session.state["iterations"] < max_iterations:
        session.state["iterations"] += 1
        session.record_llm_call()  # BudgetExceeded 会被抛出并置 budget_stopped
        action = planner_fn(state_digest(session))
        if not isinstance(action, dict):
            action = {"error": "planner 返回非 dict"}
        result = dispatch(action, ctx)
        session.add_finding(Finding(
            claim=f"{action.get('tool')} -> "
                  f"{'ok' if 'error' not in result else result['error'][:120]}",
            tool="planner_step", inputs=action,
            evidence={"rationale": action.get("rationale", ""),
                      "result": json.dumps(result, ensure_ascii=False)[:1200]}))
        session.save()
    if session.state["status"] == "running":
        session.finish({"summary": "达到迭代上限", "n_findings": len(session.state["findings"])})
    return session
