"""explain 层测试（轨A2）：标签 fail-closed、模板模式、红线隔离。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from kg_tool import explain

BANNED = ("llm", "openai", "anthropic", "gpt", "http", "requests",
          "urllib", "socket", "sklearn")


def _mk_sg(tmp_path, labels=True):
    d = tmp_path / "sg"
    d.mkdir()
    head = ("chain_id\tmicrobe_name\tmetabolite_name\thop2_gene_name"
            "\thop3_pathway\tvotes_total\tvote1_ext_cohort"
            "\tvote2_kg_depth\tvote3_gutmgene_cross\tvote4_measurability"
            "\tkg_coi_rate\tstep1_support\tkg_version"
            "\tevidence_granularity\tk5_gate_status\n")
    row = ("CH-1\t菌A\t丁酸\tIL10\thsa04630\t7\t1\t3\t1\t2\t0.666667"
           "\tsubgraph_edge:abc|pmids:123"
           + ("" if labels else "\t")
           + ("\tv2+8b6a\t名称级\tgate_pass=False(disgenet_0/28)\n"
              if labels else
              "名称级\tgate_pass=False(disgenet_0/28)\n"))
    (d / "top_hypotheses.tsv").write_text(head + row, encoding="utf-8")
    return d


def test_missing_label_fails_closed(tmp_path):
    with pytest.raises(SystemExit):
        explain.load_disclosure_labels(_mk_sg(tmp_path, labels=False))


def test_template_mode_and_labels(tmp_path, capsys):
    d = _mk_sg(tmp_path)
    rc = explain.main(["--subgraph-dir", str(d), "--top-n", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "kg_version=v2+8b6a" in out and "名称级" in out
    assert "菌A → 丁酸 → IL10" in out and "四票合计 7" in out


def test_llm_without_env_fails_closed(tmp_path, monkeypatch):
    for k in ("KG_LLM_BASE_URL", "KG_LLM_API_KEY", "KG_LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        explain.llm_narrate("ctx", {"kg_version": "v", "evidence_granularity":
                                    "n", "k5_gate_status": "g"})


def test_redline_isolation():
<<<<<<< HEAD
    """评审护栏：06_tool 主模块禁 LLM/联网红线——explain 层不得引入泄漏。
=======
    """监工护栏：06_tool 主模块禁 LLM/联网红线——explain 层不得引入泄漏。
>>>>>>> origin/main

    只查真实 import/调用行：FORBIDDEN_PATTERNS 正则定义行与注释天然含
    禁用词（那是门禁本身），不算泄漏（与 hypothesize 原门禁同口径）。
    """
    tool_dir = Path(__file__).resolve().parents[1] / "06_tool"
    # 红线归属 hypothesize 本尊（四票打分模块禁 LLM/联网）；subgraph 的
    # requests=tier1 查文献合法依赖（W1.1 起钉扎），不在禁令范围
    for f in ["hypothesize.py"]:
        src = (tool_dir / f).read_text(encoding="utf-8")
        in_pattern_block = False
        for line in src.splitlines():
            t = line.strip()
            if "FORBIDDEN_PATTERNS" in t:
                in_pattern_block = True
            if in_pattern_block:
                if t.endswith(")") or t.endswith('")'):
                    in_pattern_block = False
                continue
            if t.startswith("#"):
                continue
            low = t.lower()
            leak = any(b in low for b in BANNED) and (
                "import" in low or "requests." in low or "urllib" in low)
            assert not leak, f"{f} 红线泄漏: {line}"
