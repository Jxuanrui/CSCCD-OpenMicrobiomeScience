"""Contract CI 层（无 dsh 依赖，每次 CI 必跑）+ Upstream Integration Slice（opt-in）。

Contract CI 覆盖：金丝雀 fixture 确定性生成 / Registry 契约（见 test_capability）/
零 dsh-import（见 test_boundary）/ 钉版（见 test_boundary）。
Upstream Integration Slice：需 Node + MRA_UPSTREAM_SLICE=1 时真实执行
dsh→MCP→ScientificCore→双账本；否则保留为 manual/nightly/release-gate
（不得因 CI 环境差异删除复现资产——用户裁决 2026-09-24）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parent / "assets"
TEMPLATE = ASSETS / "upstream_template.session.v3.jsonl"


def test_canary_fixtures_deterministic_from_vendored_template(tmp_path):
    """三条金丝雀 fixture 可由仓库固化模板离线确定性生成（结构契约）。"""
    from mra.dsh_adapter.s1_slice import CANARIES, build_fixture
    assert TEMPLATE.is_file(), "upstream 模板资产缺失（勿删复现资产）"
    (ASSETS / "THIRD_PARTY_NOTICE.md").read_text(encoding="utf-8")  # 署名在场
    for cap, (tool, args) in CANARIES.items():
        out = tmp_path / f"{cap}.jsonl"
        build_fixture(TEMPLATE, out, "/tmp/x", tool=tool, args=args)
        types = [json.loads(l)["type"] for l in out.read_text().splitlines()]
        assert types.count("assistant/message") == 3 and types.count("step/end") == 3
        assert types[-1] == "turn/end"
        calls = [json.loads(l) for l in out.read_text().splitlines()
                 if json.loads(l)["type"] == "tool/call"]
        assert calls[0]["data"]["name"] == tool


def test_registry_contract_ci():
    """Registry 契约（CI 快检版）：三黄金能力 × 双实现 + EXTERNAL_WRITE 治理门。"""
    from mra.capability import SIDE_EFFECTS, build_default_registry
    reg = build_default_registry()
    for cap in ("method.query", "knowledge.route", "gap.check"):
        impls = reg.implementations(cap)
        assert {i.transport for i in impls} == {"python-inproc", "mcp"}
        assert all(i.side_effect == "READ_ONLY" for i in impls)
    assert set(SIDE_EFFECTS) == {"READ_ONLY", "COMPUTE_ONLY", "WORKSPACE_WRITE", "EXTERNAL_WRITE"}


@pytest.mark.skipif(not (shutil.which("node") and __import__("os").environ.get("MRA_UPSTREAM_SLICE")),
                    reason="Upstream Integration Slice 需 Node 且 MRA_UPSTREAM_SLICE=1（manual/nightly/release-gate）")
def test_upstream_integration_slice_method_query(tmp_path):
    """真实 dsh→MCP→ScientificCore→双账本（钉版 0.1.7-rc.1；canary 之一）。"""
    import subprocess
    from mra.dsh_adapter.s1_slice import CANARIES, build_fixture, build_patch
    mra_root = Path(__file__).resolve().parents[2]
    fixture = tmp_path / "fx.jsonl"
    tool, args = CANARIES["method.query"]
    build_fixture(TEMPLATE, fixture, str(tmp_path), tool=tool, args=args)
    patch = tmp_path / "p.cordis.yml"
    build_patch(patch, mra_root, fixture)
    env = {**__import__("os").environ,
           "DSH_HOME": str(tmp_path / "home"), "DEEPSEEK_API_KEY": "dummy"}
    # profile 初始化+插件安装（首次）后运行；此处仅运行（假设 profile 已就绪或初始化）
    subprocess.run(["npx", "-y", "@deepseek-ai/dsh@0.1.7-rc.1", "s1head",
                    "--from-default-profile", "headless", "--dump-default-config"],
                   env=env, capture_output=True, timeout=300)
    subprocess.run(["npx", "-y", "@deepseek-ai/dsh@0.1.7-rc.1", "plugin", "--profile",
                    "s1head", "add", "@deepseek-ai/dsh-llm-replay@0.1.7-rc.1"],
                   env=env, capture_output=True, timeout=300)
    proc = subprocess.run(["npx", "-y", "@deepseek-ai/dsh@0.1.7-rc.1", "--profile",
                           "s1head", "--patch", str(patch), "canary"],
                          env=env, capture_output=True, text=True, timeout=300, cwd=tmp_path)
    assert "DONE" in proc.stdout, proc.stdout[-400:] + proc.stderr[-400:]
