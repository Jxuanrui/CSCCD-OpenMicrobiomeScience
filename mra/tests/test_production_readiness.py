"""Production-readiness Hardening——生产化负路径与故障注入（评审第 4/5/6/10/12 节）。

覆盖：
- 凭据纪律（结构性）：疑似凭据字段在任何账本记录中被硬拒（3 负路径）
- 磁盘故障注入：write failure（目录只读）→ append 响亮失败、账本不被污染
- 账本损坏 fail-closed：半行损坏后 append 也拒绝（恢复不得猜测成功）
- 外部知识不稳定 A–E（litread 故障注入）：timeout≠no-evidence、
  unavailable≠negative、malformed 不入 provenance/缓存、缓存命中
  from_cache 明示且保留原始 retrieved_at/raw identity
- 血缘报告层（observability 最小版）：按 task 查全链 + 版本随行
- Benchmark Case 005：证据不足 → 诚实停止（evidence_insufficient），
  不硬产出结论
"""
from __future__ import annotations

import os
import stat
import urllib.error

import pytest

from mra.capability import build_default_registry
from mra.governance import evaluate_candidate
from mra.research import litread
from mra.research.scientific_loop import LoopStopped, ScientificLoop
from mra.workspace import (CandidateResult, Evidence, PlanStep, ResearchPlan,
                           ResearchTask, Workspace)

RULE = "method-zero-variance-guard-001"


def _cand(analysis_id, task_id="T-PRD", warnings=None):
    return CandidateResult(
        analysis_id=analysis_id, capability_id="diversity.alpha_shannon",
        implementation_id="mra.numpy", capability_version="1.0.0",
        implementation_version="1.0.0",
        input_fingerprint=f"sha256:{analysis_id}", output_summary="n=10",
        research_task_id=task_id, metrics={"n_samples": 10},
        provenance={"task": task_id}, warnings=warnings or [])


# ---- 凭据纪律（结构性拦截） ----

def test_secret1_credential_in_candidate_provenance_rejected(tmp_path):
    """CandidateResult.provenance 携带 api_key → append 硬拒。"""
    ws = Workspace("prd-sec", root=tmp_path)
    cand = _cand("PRD-S1")
    cand = cand.model_copy(update={"provenance": {
        "task": "T-PRD", "api_key": "sk-LEAKED-123"}})
    with pytest.raises(ValueError, match="凭据纪律"):
        ws.append(cand)
    assert Workspace("prd-sec", root=tmp_path).replay().candidate_results == 0


def test_secret2_nested_credential_in_effect_rejected(tmp_path):
    """Evidence.effect 深层嵌套 token → 经 record_evidence 提交被拦。"""
    reg = build_default_registry()
    ws = Workspace("prd-sec2", root=tmp_path)
    cand = _cand("PRD-S2")
    ws.append(cand)
    d = evaluate_candidate(cand, candidate_event_seq=ws.events()[-1]["seq"],
                           method_rules_applied=[RULE],
                           execution_governance={"verdicts": ["v"]})
    ws.append(d)
    with pytest.raises(ValueError, match="凭据纪律"):
        reg.invoke("workspace.record_evidence",
                   {"study_id": "prd-sec2", "decision_id": d.decision_id,
                    "record": {"evidence_id": "EV-S2", "task_id": "T-PRD",
                               "claim": "带密", "candidate_id": "PRD-S2",
                               "effect": {"rho": 0.1, "meta": {"token": "abc"}}}},
                   context={"workspace_root": tmp_path})


def test_secret3_business_fields_not_false_positive(tmp_path):
    """业务字段（api_key_used / n_tokens / raw_sha256）不受误伤。"""
    ws = Workspace("prd-sec3", root=tmp_path)
    ws.append(_cand("PRD-S3"))
    ws.append(Evidence(evidence_id="EV-S3", task_id="T-PRD", claim="正常",
                       candidate_id="PRD-S3",
                       lineage=[], effect={"n_tokens": 42, "api_key_used": True}))
    st = ws.replay()
    assert st.candidate_results == 1 and len(st.evidence) == 1


# ---- 磁盘故障注入 ----

@pytest.mark.skipif(os.geteuid() == 0, reason="root 绕过文件权限，无法注入 write failure")
def test_disk_write_failure_fails_loud_ledger_intact(tmp_path):
    """目录+账本文件只读模拟 write failure（disk full 同类）→ append 响亮失败，
    账本内容不被污染，恢复后状态一致。"""
    ws = Workspace("prd-disk", root=tmp_path)
    ws.append(ResearchTask(task_id="T-PRD", question="磁盘故障注入", client="prd"))
    n_before = len(ws.events())
    os.chmod(ws.study_dir, stat.S_IRUSR | stat.S_IXUSR)      # 目录去写
    os.chmod(ws.events_path, stat.S_IRUSR)                   # 已存在文件同样去写
    try:
        with pytest.raises(OSError):
            ws.append(_cand("PRD-DISK"))
    finally:
        os.chmod(ws.events_path, stat.S_IRUSR | stat.S_IWUSR)  # 恢复，便于清理
        os.chmod(ws.study_dir, stat.S_IRWXU)
    # fail-loud + 无部分写入：账本仍完整可读、事件数不变、无半行
    ws2 = Workspace("prd-disk", root=tmp_path)
    assert len(ws2.events()) == n_before
    assert ws2.replay().candidate_results == 0


def test_corrupt_ledger_append_also_fails_closed(tmp_path):
    """半行损坏后：不只读取 fail-loud，append 也拒绝（_next_seq 读账本）——
    恢复逻辑不得在不确定状态上猜测成功。"""
    ws = Workspace("prd-corrupt", root=tmp_path)
    ws.append(ResearchTask(task_id="T-PRD", question="损坏", client="prd"))
    with ws.events_path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 99, "record_type": "Candi')
    with pytest.raises(Exception):  # JSONDecodeError：读或写均 fail closed
        ws.append(_cand("PRD-CORRUPT"))


# ---- 外部知识不稳定 A–E（litread 故障注入，零真实网络） ----

_ESEARCH_JSON = b'{"esearchresult": {"idlist": ["12345"]}}'
_EFETCH_XML = (b"<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345"
               b"</PMID><Article><ArticleTitle>Stability probe</ArticleTitle>"
               b"<Abstract><AbstractText>abs text</AbstractText></Abstract>"
               b"<Journal><PubDate><Year>2024</Year></PubDate></Journal>"
               b"</Article><DateCreated><Year>2024</Year></DateCreated>"
               b"</MedlineCitation></PubmedArticle></PubmedArticleSet>")


def _fake_fetch(url: str, timeout: float) -> bytes:
    return _ESEARCH_JSON if "esearch" in url else _EFETCH_XML


def _no_notes(papers, question, model_name=None, max_calls=4):
    return []


def test_ext_a_timeout_is_not_no_evidence(tmp_path, monkeypatch):
    """A：API timeout 必须抛出（fail-loud），不得伪装成 no evidence；且不落缓存。"""
    def _timeout(url, timeout):
        raise TimeoutError("simulated timeout")
    monkeypatch.setattr(litread, "_fetch", _timeout)
    monkeypatch.setattr(litread, "quickread_notes", _no_notes)
    with pytest.raises(TimeoutError):
        litread.search_and_read("q", "question", cache_dir=tmp_path)
    assert not list(tmp_path.glob("*.json"))  # 失败结果不进缓存


def test_ext_b_unavailable_vs_negative_result(tmp_path, monkeypatch):
    """B：rate-limit/unavailable（异常）与 negative result（空命中）结构性分离。"""
    monkeypatch.setattr(litread, "quickread_notes", _no_notes)
    # unavailable：HTTP 429 → URLError → 抛出
    def _rate_limited(url, timeout):
        raise urllib.error.URLError("HTTP Error 429: Too Many Requests")
    monkeypatch.setattr(litread, "_fetch", _rate_limited)
    with pytest.raises(urllib.error.URLError):
        litread.search_and_read("q", "question", cache_dir=tmp_path / "a")
    # negative：合法空命中是正当科研答案（非错误）
    monkeypatch.setattr(litread, "_fetch",
                        lambda url, timeout: b'{"esearchresult": {"idlist": []}}')
    out = litread.search_and_read("q", "question", cache_dir=tmp_path / "b")
    assert out["n_papers"] == 0 and not out["from_cache"]
    assert out["provenance"]["source_id"] == "ncbi-eutils-pubmed"


def test_ext_c_malformed_never_enters_provenance(tmp_path, monkeypatch):
    """C：malformed response → 抛出；不入 provenance、不落缓存。"""
    monkeypatch.setattr(litread, "quickread_notes", _no_notes)
    monkeypatch.setattr(litread, "_fetch", lambda url, timeout: b"<html>gateway error</html>")
    with pytest.raises(Exception):  # JSONDecodeError
        litread.search_and_read("q", "question", cache_dir=tmp_path)
    assert not list(tmp_path.glob("*.json"))


def test_ext_d_e_cache_labeled_not_live(tmp_path, monkeypatch):
    """D+E：缓存命中 from_cache=True 明示、保留原始 retrieved_at 与 raw identity；
    命中后零网络（_fetch 直接抛错以证明）。"""
    monkeypatch.setattr(litread, "quickread_notes", _no_notes)
    monkeypatch.setattr(litread, "_fetch", _fake_fetch)
    live = litread.search_and_read("q", "question", cache_dir=tmp_path)
    assert not live["from_cache"] and live["n_papers"] == 1
    retrieved_at = live["provenance"]["retrieved_at"]
    raw_sha = live["provenance"]["raw_response_sha256"]
    assert raw_sha  # source changed 可审计：raw identity 在场
    # 第二次：缓存命中——把 _fetch 换成必炸，证明没走网络
    monkeypatch.setattr(litread, "_fetch",
                        lambda url, timeout: (_ for _ in ()).throw(AssertionError("不该联网")))
    cached = litread.search_and_read("q", "question", cache_dir=tmp_path)
    assert cached["from_cache"]                                   # 明示是缓存
    assert cached["provenance"]["retrieved_at"] == retrieved_at   # 原始时间
    assert cached["provenance"]["raw_response_sha256"] == raw_sha


# ---- 血缘报告层（observability 最小版） ----

def test_lineage_report_full_chain_with_versions(tmp_path):
    reg = build_default_registry()

    def _exec(capability_id, inputs, context=None):
        return {"candidate": _cand(inputs["analysis_id"], "T-LIN").model_dump(),
                "execution_verdicts": [{"rule": "audit", "verdict": "PASS"}]}

    loop = ScientificLoop("prd-lin", registry=reg, workspace_root=tmp_path,
                          executor=_exec)
    task = ResearchTask(task_id="T-LIN", question="血缘查询验证", client="prd-test")
    plan = ResearchPlan(
        plan_id="PRD-P", research_task_id="T-LIN", plan_version=1,
        steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": "PRD-L1"})],
        method_constraints=[RULE], stopping_conditions=["insufficient_data"])
    loop.open_task(task)
    loop.adopt_plan(task, plan)
    out = loop.execute_step(task, plan, plan.steps[0])
    d = loop.evaluate_and_commit(task, out["candidate"], rules=[RULE])["decision"]
    loop.commit_evidence(task, out["candidate"], d, claim="血缘证据",
                         evidence_id="EV-LIN")
    reg.invoke("workspace.mark_downgraded",
               {"study_id": "prd-lin", "evidence_id": "EV-LIN",
                "reason": "复审降级", "actor": "T-LIN"},
               context={"workspace_root": tmp_path})
    loop.complete("T-LIN")
    lin = Workspace("prd-lin", root=tmp_path).lineage("T-LIN")
    assert lin["task"]["task_id"] == "T-LIN"
    assert len(lin["plans"]) == 1 and lin["plans"][0]["plan_id"] == "PRD-P"
    assert lin["candidates"][0]["capability_version"] == "1.0.0"
    assert lin["candidates"][0]["input_fingerprint"].startswith("sha256:")
    assert lin["decisions"][0]["policy_version"] == d.policy_version
    assert lin["decisions"][0]["client"] == "prd-test"
    assert lin["evidence"][0]["evidence_id"] == "EV-LIN"
    assert lin["evidence"][0]["falsification"] == "downgraded"
    assert lin["mutations"] >= 1
    assert lin["terminal"]["verdict"] == "task_completed"
    # 无关任务不串线
    assert Workspace("prd-lin", root=tmp_path).lineage("T-OTHER")["task"] is None


# ---- Benchmark Case 005：No-valid-conclusion（诚实停止） ----

def test_case005_honest_stop_no_forced_conclusion(tmp_path):
    """证据不足 → evidence_insufficient 合法终止；绝不硬产出 Evidence。"""
    reg = build_default_registry()

    def _exec(capability_id, inputs, context=None):
        return {"candidate": _cand(inputs["analysis_id"], warnings=[
            {"level": "blocking", "message": "样本错位嫌疑"}]).model_dump(),
            "execution_verdicts": [{"rule": "audit", "verdict": "PASS"}]}

    loop = ScientificLoop("prd-c5", registry=reg, workspace_root=tmp_path,
                          executor=_exec)
    task = ResearchTask(task_id="T-C5", question="无有效结论场景", client="prd-test")
    plan = ResearchPlan(
        plan_id="C5-P", research_task_id="T-C5", plan_version=1,
        steps=[PlanStep(step_id="s1", capability_id="diversity.alpha_shannon",
                        inputs={"features": "species", "analysis_id": "C5-A1"})],
        method_constraints=[RULE], stopping_conditions=["evidence_insufficient"])
    loop.open_task(task)
    loop.adopt_plan(task, plan)
    out = loop.execute_step(task, plan, plan.steps[0])
    with pytest.raises(LoopStopped) as ei:
        loop.evaluate_and_commit(task, out["candidate"], rules=[RULE])
    assert ei.value.state == "evidence_insufficient"  # 诚实停止而非硬结论
    st = Workspace("prd-c5", root=tmp_path).replay()
    assert st.evidence == []                          # 无强产证据
    terminals = [e["record"] for e in Workspace("prd-c5", root=tmp_path).events()
                 if e["record_type"] == "LoopEvent" and e["record"]["kind"] == "terminal"]
    assert terminals and terminals[-1]["verdict"] == "evidence_insufficient"
