"""HTML 报告测试（轨B 三钉）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from kg_tool import report

# 三钉之三：源码红线——禁统计/禁 LLM/禁联网/禁任何聚合计算
BANNED_TOKENS = ("statistics", "sum(", "len(", "mean(", "median(",
                 "openai", "anthropic", "http", "requests", "urllib",
                 "llm", "sorted(")


def test_source_redline_no_compute_no_llm():
    src = Path(report.__file__).read_text(encoding="utf-8")
    # 剥掉模块文档字符串（说明书里写着"禁 statistics"不算泄漏——与
    # hypothesize 正则定义行同口径）
    end = src.index('"""', src.index('"""') + 3) + 3
    src = src[end:]
    for line in src.splitlines():
        t = line.strip()
        if t.startswith("#") or t.startswith('"""') or "白名单" in t:
            continue
        low = t.lower()
        for b in BANNED_TOKENS:
            assert b not in low, f"报告模块红线泄漏 '{b}': {line}"


def test_missing_top_fails_closed(tmp_path):
    with pytest.raises(SystemExit):
        report.main(["--subgraph-dir", str(tmp_path), "--out",
                     str(tmp_path / "r.html")])


def test_renders_labels_and_preserves_order(tmp_path):
    d = tmp_path / "sg"
    d.mkdir()
    head = ("chain_id\tmicrobe_name\tmetabolite_name\thop2_gene_name"
            "\thop3_pathway\tvotes_total\tkg_coi_rate"
            "\tvote4_measurability\tkg_version\tevidence_granularity"
            "\tk5_gate_status\tconfound_warning\n")
    # 故意乱序票数（7 在前 5 在后）——报告必须保持文件顺序不重排
    rows = ("CH-1\t菌A\t丁酸\tIL10\thsa04630\t7\t0.666667\t2\tv2\t名称级"
            "\tgate=False\t警示句X\n"
            "CH-2\t菌B\t丙酸\tIL6\thsa05167\t5\t0.333333\t1\tv2\t名称级"
            "\tgate=False\t警示句X\n")
    (d / "top_hypotheses.tsv").write_text(head + rows, encoding="utf-8")
    # 真实嵌套结构（轨B 审 P0-1：断言在 red_lines 之下）
    (d / "hypothesis_manifest.json").write_text(
        '{"spec":"测试","counts":{"n_cards":2},"red_lines":'
        '{"null_control_assert":{"evidence_votes_all_zero":true}}}',
        encoding="utf-8")
    (d / "chains_manifest.json").write_text('{"counts":{"总链":2}}',
                                            encoding="utf-8")
    (d / "interaction_manifest.json").write_text('{"counts":{"extend":2}}',
                                                 encoding="utf-8")
    out = tmp_path / "r.html"
    assert report.main(["--subgraph-dir", str(d), "--out", str(out)]) == 0
    h = out.read_text(encoding="utf-8")
    # 三列披露转印 + 顺序保持（菌A 在菌B 前）+ 零计算（counts 原文）
    assert "kg_version=v2" in h and "名称级" in h
    assert h.index("菌A") < h.index("菌B")
    assert "n_cards: 2" in h and "evidence_votes_all_zero" in h


def test_whitelist_source_assertion():
    """钉①执行版：report.py 全部文件读取目标 ∈ 白名单四件（防假门禁）。"""
    src = Path(report.__file__).read_text(encoding="utf-8")
    end = src.index('"""', src.index('"""') + 3) + 3
    src = src[end:]
    import re as _re
    for m in _re.finditer(r'(?:open|read_text)\(([^)]*)\)', src):
        target = m.group(1)
        # 只约束"含 .tsv/.json 文件名字面量"的参数——路径常在调用外层
        # 括号（如 json.loads((…).read_text(encoding=…))），encoding 类
        # 无文件名参数不属白名单对象
        import re as _r
        if _r.search(r'"[^"]*\.(?:tsv|json)"', target):
            assert any(w in target for w in report.INPUT_WHITELIST), \
                f"白名单外读取: {target}"
