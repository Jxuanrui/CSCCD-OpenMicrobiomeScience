#!/usr/bin/env python3
"""P0-6 构造性回归测试：写入准入控制（fail-closed + 审计账本 + 越权拒绝）。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import write_guard as wg  # noqa: E402


@pytest.fixture
def guard_env(tmp_path, monkeypatch):
    reg = tmp_path / "registry"
    reg.mkdir()
    (reg / "write_authorizations.tsv").write_text(
        "authorization_key\ttargets\tgranted_by\tstatus\tnote\n"
        "key-a\tdata/merged;neo4j_materialize\ttester\tactive\t测试\n"
        "key-inactive\tdata/merged\ttester\trevoked\t已撤销\n", encoding="utf-8")
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(wg, "AUTH_TSV", reg / "write_authorizations.tsv")
    monkeypatch.setattr(wg, "AUDIT", logs / "write_audit.jsonl")
    return logs / "write_audit.jsonl"


def test_reject_bad_execution_id(guard_env):
    with pytest.raises(wg.WriteGuardError, match="execution_id"):
        wg.guard_write("data/merged", "", "key-a")
    with pytest.raises(wg.WriteGuardError, match="execution_id"):
        wg.guard_write("data/merged", "not-an-exec", "key-a")


def test_reject_unknown_authorization(guard_env):
    with pytest.raises(wg.WriteGuardError, match="未登记"):
        wg.guard_write("data/merged", "EX-test-1", "no-such-key")


def test_reject_revoked_and_out_of_scope(guard_env):
    with pytest.raises(wg.WriteGuardError):
        wg.guard_write("data/merged", "EX-test-2", "key-inactive")  # status=revoked
    with pytest.raises(wg.WriteGuardError, match="不覆盖"):
        wg.guard_write("data/pubtator", "EX-test-3", "key-a")  # 未授权目标


def test_allow_and_audit(guard_env):
    rec = wg.guard_write("data/merged", "EX-test-4", "key-a")
    assert rec["execution_id"] == "EX-test-4" and rec["granted_by"] == "tester"
    lines = guard_env.read_text().strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["target"] == "data/merged"
    wg.guard_write("neo4j_materialize", "EX-test-5", "key-a")
    assert len(guard_env.read_text().strip().splitlines()) == 2  # append-only 累积
