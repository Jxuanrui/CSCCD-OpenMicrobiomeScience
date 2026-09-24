"""B5 Multi-omics Extension——代谢组/蛋白组经同一架构端到端处理。

H5 B类缺口第5项：此前全部验证基于哈尔滨队列（species/pathway/fungal/viral），
其他组学（代谢组/蛋白组）未触及。验证架构主张：**新增组学 = 数据契约条目 +
方法规则适配，零 Scientific Core 改动**（Capability Registry / 治理 /
Workspace / Evidence 链对组学类型无感知，组学身份由数据契约与指纹承载）。

Case A：合成代谢组/蛋白组表入数据契约 → 既有 capability（diversity）直接产出
        候选；input_fingerprint 可复算地绑定组学身份，假设注释如实随行
Case B：方法知识库的组学适配——真实 16 规则入临时 KB，代谢组适用规则
        （log 变换/伪计数）可召回且作为 method_constraints 通过治理；
        空规则引用被治理门拦截
Case C：同一 ResearchTask 双组学证据并存（各自候选→裁决→证据链互不混淆），
        canonical 单侧置位不串组学
负路径×4：契约外表名硬失败（无静默回退）/ 契约有名文件缺失响亮失败 /
        跨组学裁决错配拦截 / 双组学复用 evidence_id 的冲突由 replay 去重暴露。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.knowledge.method_rules import ingest_method_dir, search_method_rules
from mra.research import datasources as ds
from mra.workspace import (CandidateResult, ResearchTask, Workspace, digest)

METHODS_DIR = Path(__file__).resolve().parents[1] / "knowledge" / "methods"
N_SAMPLES = 12


def _write_omics_table(path: Path, prefix: str, n_feats: int = 8) -> None:
    """确定性合成特征表（特征行×样本列，全正值；load_table 转置为样本×特征）。"""
    cols = [f"S{j}" for j in range(N_SAMPLES)]
    lines = ["feature\t" + "\t".join(cols)]
    for i in range(n_feats):
        vals = [f"{0.01 * ((i * 7 + j * 3) % 9 + 1):.6f}" for j in range(N_SAMPLES)]
        lines.append(f"{prefix}{i}\t" + "\t".join(vals))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _use_contract(monkeypatch, tmp_path: Path, features: dict) -> None:
    """临时数据契约（零核心改动的新组学入口）+ 重置配置缓存。"""
    cfg = tmp_path / "cohort.json"
    cfg.write_text(json.dumps({"exposures": {}, "features": features,
                               "metadata": "", "default_covariates": []}),
                   encoding="utf-8")
    monkeypatch.setenv("COHORT_CONFIG", str(cfg))
    monkeypatch.setattr(ds, "_CONFIG_CACHE", None)


@pytest.fixture()
def omics_contract(tmp_path, monkeypatch):
    met = tmp_path / "metabolome.tsv"
    pro = tmp_path / "proteome.tsv"
    _write_omics_table(met, "metab_")
    _write_omics_table(pro, "prot_", n_feats=6)
    _use_contract(monkeypatch, tmp_path,
                  {"metabolome": str(met), "proteome": str(pro)})
    return tmp_path


# ---- Case A：数据契约扩展零核心改动 ----

def test_case_a_new_omics_via_contract_only(omics_contract):
    """代谢组/蛋白组仅凭契约条目即可被既有 capability 处理，指纹绑定组学身份。"""
    reg = build_default_registry()
    out_met = reg.invoke("diversity.alpha_shannon",
                         {"features": "metabolome", "analysis_id": "B5-MET"},
                         context={})
    out_pro = reg.invoke("diversity.alpha_shannon",
                         {"features": "proteome", "analysis_id": "B5-PRO"},
                         context={})
    met, pro = out_met["candidate"], out_pro["candidate"]
    # 组学身份由指纹可复算地承载（digest(features名, 样本数)）
    assert met["input_fingerprint"] == digest({"features": "metabolome",
                                               "n_samples": N_SAMPLES})
    assert pro["input_fingerprint"] == digest({"features": "proteome",
                                               "n_samples": N_SAMPLES})
    assert met["input_fingerprint"] != pro["input_fingerprint"]
    # 同一 capability、同一信封；假设注释如实随行（TSS 相对化假设不因组学而隐藏）
    assert met["result_type"] == pro["result_type"] == "diversity_alpha"
    assert met["assumptions_checked"] and met["provenance"]


# ---- Case B：方法知识库的组学适配 ----

def test_case_b_method_rules_for_omics(tmp_path, omics_contract):
    """真实规则入临时 KB：代谢组适用规则可召回并通过治理；空规则被拦。"""
    db = tmp_path / "knowledge.db"
    assert ingest_method_dir(METHODS_DIR, db_path=db) >= 16
    # 代谢组管线适用的规则（log 变换/伪计数/低丰度）可被检索召回
    hits = search_method_rules("伪计数 低丰度 特征 系数", k=5, db_path=db)
    rule = next(h for h in hits if "pseudocount" in h["rule_id"])
    assert rule["structured"] and rule["applicable_data"]  # 七键结构 + 适用域随行
    # 微生物组专属规则同样可召回，适用域明示边界（组学适配是策展知识而非硬编码）
    batch = next(h for h in search_method_rules("批次 校正 矩阵", k=5, db_path=db)
                 if "batch-dual-track" in h["rule_id"])
    assert "MetaPhlAn" in batch["applicable_data"]
    # 组学身份的候选引用代谢组适用规则 → 治理放行
    reg = build_default_registry()
    met = CandidateResult(**reg.invoke(
        "diversity.alpha_shannon",
        {"features": "metabolome", "analysis_id": "B5-METB"}, context={})["candidate"])
    ws = Workspace("b5-b", root=tmp_path)
    ws.append(met)
    d = evaluate_candidate(met,
                           candidate_event_seq=ws.events()[-1]["seq"],
                           method_rules_applied=[rule["rule_id"]],
                           execution_governance={"verdicts": ["v"]})
    assert d.allow_evidence
    # 对照：空方法规则 → 治理门拦截（组学分析同样不许裸奔）
    d_bare = evaluate_candidate(met,
                                candidate_event_seq=ws.events()[-1]["seq"],
                                method_rules_applied=[],
                                execution_governance={"verdicts": ["v"]})
    assert not d_bare.allow_evidence


# ---- Case C：同一任务双组学证据并存 ----

def test_case_c_dual_omics_evidence_coexist(tmp_path, omics_contract):
    """代谢组/蛋白组各自成链：候选→裁决→证据，canonical 单侧置位不串组学。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws = Workspace("b5-c", root=tmp_path)
    ws.append(ResearchTask(task_id="T-B5C", question="多组学联合分析",
                           client="b5-test"))
    decisions = {}
    omics_cn = {"metabolome": "代谢组", "proteome": "蛋白组"}
    for aid, feats, ev_id in [("B5-MET", "metabolome", "EV-MET"),
                              ("B5-PRO", "proteome", "EV-PRO")]:
        cand = CandidateResult(**reg.invoke(
            "diversity.alpha_shannon",
            {"features": feats, "analysis_id": aid}, context={})["candidate"])
        ws.append(cand)
        d = evaluate_candidate(cand, candidate_event_seq=ws.events()[-1]["seq"],
                               method_rules_applied=["method-pseudocount-floor-001"],
                               execution_governance={"verdicts": ["v"]})
        ws.append(d)
        decisions[aid] = d
        out = reg.invoke("workspace.record_evidence",
                         {"study_id": "b5-c", "decision_id": d.decision_id,
                          "record": {"evidence_id": ev_id, "task_id": "T-B5C",
                                     "claim": f"{omics_cn[feats]}组学发现",
                                     "candidate_id": aid}}, context=ctx)
        assert out["committed"]
    st = ws.replay()
    assert len(st.evidence) == 2  # 双组学证据并存，互不覆盖
    assert {e["candidate_id"] for e in st.evidence} == {"B5-MET", "B5-PRO"}
    assert {e["governance"]["decision"]["decision_id"] for e in st.evidence} == \
        {d.decision_id for d in decisions.values()}
    # canonical 只置位代谢组侧；蛋白组侧不受牵连
    reg.invoke("workspace.set_canonical",
               {"study_id": "b5-c", "evidence_id": "EV-MET",
                "supporting_lineage": ["EV-MET"], "reason": "代谢组侧定版",
                "decision_id": decisions["B5-MET"].decision_id}, context=ctx)
    st2 = ws.replay()
    by_id = {e["evidence_id"]: e for e in st2.evidence}
    assert by_id["EV-MET"]["canonical"] and not by_id["EV-PRO"]["canonical"]


# ---- 负路径×4 ----

def test_neg1_unregistered_omics_hard_fail(tmp_path, monkeypatch, omics_contract):
    """契约外组学名 → KeyError 硬失败，不静默回退到 species。"""
    met = tmp_path / "metabolome.tsv"
    _use_contract(monkeypatch, tmp_path, {"proteome": str(tmp_path / "proteome.tsv")})
    reg = build_default_registry()
    with pytest.raises(KeyError, match="未知特征表"):
        reg.invoke("diversity.alpha_shannon",
                   {"features": "metabolome", "analysis_id": "B5-N1"}, context={})
    assert met.exists()  # 真因是契约缺项而非文件问题


def test_neg2_registered_but_file_missing(tmp_path, monkeypatch):
    """契约有名但文件缺失 → 响亮失败（部署错误不产生空组学结果）。"""
    _use_contract(monkeypatch, tmp_path,
                  {"metabolome": str(tmp_path / "ghost.tsv")})
    reg = build_default_registry()
    with pytest.raises(FileNotFoundError, match="数据表缺失"):
        reg.invoke("diversity.alpha_shannon",
                   {"features": "metabolome", "analysis_id": "B5-N2"}, context={})


def test_neg3_cross_omics_decision_mismatch(tmp_path, omics_contract):
    """代谢组候选的裁决用于蛋白组证据提交 → 六验"不对应"拦截。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws = Workspace("b5-n3", root=tmp_path)
    met = CandidateResult(**reg.invoke(
        "diversity.alpha_shannon",
        {"features": "metabolome", "analysis_id": "B5-METN"}, context={})["candidate"])
    ws.append(met)
    d = evaluate_candidate(met, candidate_event_seq=ws.events()[-1]["seq"],
                           method_rules_applied=["method-pseudocount-floor-001"],
                           execution_governance={"verdicts": ["v"]})
    ws.append(d)
    with pytest.raises(ValueError, match="不对应"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "b5-n3", "decision_id": d.decision_id,
                    "record": {"evidence_id": "EV-PRO-N3", "task_id": "T-N3",
                               "claim": "冒用代谢组裁决", "candidate_id": "B5-PRON"}},
                   context=ctx)


def test_neg4_duplicate_evidence_id_across_omics(tmp_path, omics_contract):
    """双组学复用同一 evidence_id → replay 去重暴露冲突（多证据任务必须显式
    分配 ID；账本双事件仍在、可审计，但状态视图只剩最后一条）。"""
    reg = build_default_registry()
    ctx = {"workspace_root": tmp_path}
    ws = Workspace("b5-n4", root=tmp_path)
    for aid, feats in [("B5-MET4", "metabolome"), ("B5-PRO4", "proteome")]:
        cand = CandidateResult(**reg.invoke(
            "diversity.alpha_shannon",
            {"features": feats, "analysis_id": aid}, context={})["candidate"])
        ws.append(cand)
        d = evaluate_candidate(cand, candidate_event_seq=ws.events()[-1]["seq"],
                               method_rules_applied=["method-pseudocount-floor-001"],
                               execution_governance={"verdicts": ["v"]})
        ws.append(d)
        reg.invoke("workspace.record_evidence",
                   {"study_id": "b5-n4", "decision_id": d.decision_id,
                    "record": {"evidence_id": "EV-DUP", "task_id": "T-N4",
                               "claim": f"{feats} 发现", "candidate_id": aid}},
                   context=ctx)
    st = ws.replay()
    assert len(st.evidence) == 1  # 冲突可见：后写覆盖
    assert st.evidence[0]["candidate_id"] == "B5-PRO4"
    ev_events = [e for e in ws.events() if e["record_type"] == "Evidence"]
    assert len(ev_events) == 2  # 审计轨迹仍完整（append-only 不丢历史）
