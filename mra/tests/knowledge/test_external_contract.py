"""P2 External Knowledge A–E Contract——对抗场景与验收指标（零真实网络）。

第一原则：外部知识源数量增加、可靠性下降、结果冲突时，系统仍守证据纪律。

Cases（用户裁决规格）：
  A — Source A 有结果 / Source B timeout：不得下降为"无证据"（partial failure
      不拖垮整体，也不静默丢弃失败源）
  B — Cache 有旧结果 / live 失败：明示 cached evidence（from_cache +
      cache_fallback_after + 原始 retrieved_at/raw_hash），不伪装 live
  C — 两源冲突结论：进入 conflict state（manual_review_required），
      保留全部 provenance，不自动选择/平均/隐藏
  D — Malformed 注入：零进入 cache / provenance / evidence
  E — Rate limit 后恢复：不重复污染缓存，lineage/provenance 完整

验收指标（全锁死）：
  External provenance completeness = 100%；Timeout-as-negative-error = 0；
  Malformed evidence ingestion = 0；Cache/live confusion = 0；
  Source failure silent masking = 0；Unauthorized evidence promotion = 0。
"""
from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from mra.knowledge.sources.contract import (RESULT_OK, CachedSource,
                                            ExternalQueryResult,
                                            detect_conflicts, multi_query,
                                            query)
from mra.knowledge.sources.europepmc import EuropePmcAdapter
from mra.knowledge.sources.omnipath import OmniPathAdapter
from mra.knowledge.types import CandidateEvidence

_EPMC_OK = json.dumps({"resultList": {"result": [
    {"id": "111", "source": "MED", "title": "F.prausnitzii improves colitis",
     "pmid": "111", "doi": "10.1/aaa", "abstractText": "abstract text"}]}}).encode()
_EPMC_EMPTY = json.dumps({"resultList": {"result": []}}).encode()
_EPMC_ERR_200 = json.dumps({"errCode": "SOME", "errMsg": "bad"}).encode()
_OMNI_OK = json.dumps([
    {"source": "P1", "target": "P2", "is_directed": True, "is_stimulation": True,
     "is_inhibition": False, "sources": ["Reactome"], "references": ["Reactome:1"]}]).encode()


def _epmc(fetcher=None):
    return EuropePmcAdapter(fetcher=fetcher or (lambda url: _EPMC_OK))


def _omni(fetcher=None):
    return OmniPathAdapter(fetcher=fetcher or (lambda url: _OMNI_OK))


def _http_429(url):
    raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)


# ---- Case A：部分源失败 ≠ 无证据 ----

def test_case_a_partial_failure_not_no_evidence():
    ok = _epmc()
    down = _epmc(fetcher=lambda url: (_ for _ in ()).throw(TimeoutError("down")))
    report = multi_query([ok, down], "F. prausnitzii")
    assert report["has_usable_evidence"]                 # A 的结果仍在
    assert report["sources_ok"] == ["europe-pmc"]
    assert report["sources_failed"] == {"europe-pmc": "timeout"}  # 不静默丢弃
    # Timeout-as-negative-error = 0：timeout ≠ empty（系统状态 vs 知识结果）
    failed = next(r for r in report["results"] if r.source_id == "europe-pmc"
                  and r.status == "timeout")
    assert failed.status not in RESULT_OK and not failed.evidence_items
    assert failed.error  # 失败有事实描述


def test_case_a_empty_vs_unavailable_distinction():
    empty = query(_epmc(fetcher=lambda url: _EPMC_EMPTY), "rare term")
    down = query(_epmc(fetcher=lambda url: _EPMC_ERR_200), "any")
    assert empty.status == "empty" and empty.status in RESULT_OK     # 知识结果
    assert down.status == "unavailable" and down.status not in RESULT_OK  # 系统状态
    assert not empty.evidence_items and not down.evidence_items


# ---- Case B：缓存兜底明示 ----

def test_case_b_cache_fallback_labeled_not_live(tmp_path):
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return _EPMC_OK
        raise TimeoutError("source down")

    cached = CachedSource(_epmc(fetcher=flaky), tmp_path / "cache")
    live = cached.query("fiber and microbiome")
    assert live.status == "success" and not live.from_cache
    fallback = cached.query("fiber and microbiome")
    assert fallback.from_cache                                  # 明示缓存
    assert fallback.cache_fallback_after == "timeout"           # 明示覆盖的失败态
    assert fallback.retrieved_at == live.retrieved_at           # 原始时间保留
    assert fallback.raw_hash == live.raw_hash                   # raw identity 保留
    assert fallback.status == "success"                         # 缓存内容是正当结果


# ---- Case C：跨源冲突 → conflict state ----

def _fake_source(source_id: str, direction: str, claim_key: str = "IL10"):
    ev = CandidateEvidence(
        evidence_id=f"{source_id}:1", source_id=source_id, source_version="1",
        source_record_id="1",
        retrieved_at=datetime.now(timezone.utc).isoformat(),
        query=f"{claim_key} direction", claim_summary=f"{claim_key};direction={direction}",
        claim_type="association", evidence_status="candidate",
        review_status="pending", license_status="pending", pmid=None, pmcid=None,
        doi=None, raw_excerpt=None, source_url=None, raw_response_hash=f"sha256:{source_id}")
    desc = SimpleNamespace(source_id=source_id, version="1", source_type="rest_api",
                           evidence_origin="literature", default_use="candidate",
                           provenance_fields=("pmid",), transport="https",
                           endpoint="https://fake")
    return SimpleNamespace(describe=lambda: desc, search=lambda q, p=25: [ev])


def test_case_c_conflict_state_not_auto_resolution():
    a = _fake_source("src-a", "positive")
    b = _fake_source("src-b", "negative")
    report = multi_query([a, b], "IL10 direction")
    conflicts = detect_conflicts(
        report,
        key_fn=lambda ev: ev.claim_summary.split(";")[0],
        value_fn=lambda ev: ev.claim_summary.split("direction=")[1])
    assert conflicts["conflict_detected"]
    assert conflicts["auto_resolution"] == "disabled"          # 不自动裁决
    assert conflicts["items_retained"] == 2                    # 双方证据全保留
    c = conflicts["conflicts"][0]
    assert {p["source_id"] for p in c["positions"]} == {"src-a", "src-b"}
    assert c["resolution"] == "manual_review_required"         # 裁决权在人工
    # 无冲突时不误报
    agree = detect_conflicts(multi_query([_fake_source("src-a", "positive"),
                                          _fake_source("src-b", "positive")], "q"),
                             key_fn=lambda ev: ev.claim_summary.split(";")[0],
                             value_fn=lambda ev: ev.claim_summary.split("direction=")[1])
    assert not agree["conflict_detected"]


# ---- Case D：malformed 零进入 cache/provenance/evidence ----

def test_case_d_malformed_zero_ingestion(tmp_path):
    adapter = _epmc(fetcher=lambda url: b"<html>gateway error</html>")
    cached = CachedSource(adapter, tmp_path / "cache")
    r = cached.query("anything")
    assert r.status == "malformed" and not r.evidence_items
    assert r.error and r.status not in RESULT_OK
    assert not any((tmp_path / "cache").glob("*.json"))         # 不入缓存
    # 恢复后正常查询不受污染（缓存无脏数据）
    fixed = CachedSource(_epmc(), tmp_path / "cache")
    ok = fixed.query("anything")
    assert ok.status == "success" and ok.evidence_items
    assert all(i.raw_response_hash.startswith("sha256:") for i in ok.evidence_items)


# ---- Case E：rate limit 后恢复 ----

def test_case_e_rate_limit_recovery_no_pollution(tmp_path):
    seq = {"n": 0}

    def flaky(url):
        seq["n"] += 1
        if seq["n"] == 1:
            _http_429(url)
        return _EPMC_OK

    cached = CachedSource(_epmc(fetcher=flaky), tmp_path / "cache")
    limited = cached.query("recovery probe")
    assert limited.status == "rate_limited"                     # access restricted
    assert limited.status not in RESULT_OK and not limited.evidence_items
    assert not any((tmp_path / "cache").glob("*.json"))         # 失败零入缓存
    recovered = cached.query("recovery probe")                  # 恢复重试
    assert recovered.status == "success" and not recovered.from_cache
    assert recovered.provenance["source_version"]               # lineage 完整
    assert all(i.evidence_status == "candidate" for i in recovered.evidence_items)


# ---- 验收指标 ----

def test_metric_provenance_completeness():
    for result in [query(_epmc(), "q1"),
                   query(_omni(), "P1"),
                   query(_epmc(fetcher=lambda url: _EPMC_EMPTY), "q2")]:
        assert result.source_id and result.query and result.retrieved_at
        assert result.provenance.get("retrieval_path")
        assert result.provenance.get("source_version")
        for ev in result.evidence_items:
            assert ev.raw_response_hash.startswith("sha256:")
            assert ev.retrieved_at and ev.source_id and ev.query
    assert query(_epmc(), "q1").raw_hash.startswith("sha256:")


def test_metric_unauthorized_promotion_zero():
    report = multi_query([_epmc(), _omni(), _fake_source("src-x", "positive")], "q")
    for r in report["results"]:
        for ev in r.evidence_items:
            assert ev.evidence_status == "candidate"            # 升级只走 curated ingestion
            assert ev.review_status == "pending"
    assert report["evidence_status"] == "candidate"


def test_contract_envelope_invariants():
    ok_ev = _fake_source("s", "positive").search("q")[0]
    with pytest.raises(ValueError):                              # 失败态不得带证据
        ExternalQueryResult(status="timeout", source_id="s", query="q",
                            evidence_items=[ok_ev])
    with pytest.raises(ValueError):                              # 正当结果不得带 error
        ExternalQueryResult(status="success", source_id="s", query="q", error="x")
    with pytest.raises(ValueError):                              # 状态分类法封闭
        ExternalQueryResult(status="kind_of_working", source_id="s", query="q")
    # round-trip（缓存序列化保真）
    r = query(_epmc(), "roundtrip")
    rt = ExternalQueryResult.from_dict(json.loads(json.dumps(r.to_dict(),
                                                            ensure_ascii=False)))
    assert rt.status == r.status and rt.raw_hash == r.raw_hash
    assert rt.evidence_items[0].evidence_id == r.evidence_items[0].evidence_id


def test_metric_source_failure_silent_masking_zero():
    report = multi_query(
        [_epmc(), _omni(fetcher=lambda url: (_ for _ in ()).throw(TimeoutError())),
         _epmc(fetcher=lambda url: _EPMC_ERR_200)], "q")
    assert set(report["sources_failed"]) == {"omnipath", "europe-pmc"}
    assert report["sources_failed"]["omnipath"] == "timeout"
    assert report["sources_failed"]["europe-pmc"] == "unavailable"
    assert len(report["results"]) == 3                           # 逐源在场，零静默丢弃
