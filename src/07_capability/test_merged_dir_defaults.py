#!/usr/bin/env python3
"""KG_MERGED_DIR 默认值一致性契约（监工路径规整 P0-1，2026-10-07）。

背景：5 个管线脚本曾默认根层 data/merged（v2 旧数据），与 graph_analysis/
lightrag_qa 的 candidate_v3 默认不一致——不设环境变量运行 neo4j_materialize
会用 v2 数据覆盖 v3 活库。本测试锁定：全仓所有 KG_MERGED_DIR 读取点的默认值
必须统一为 data/merged/candidate_v3，且 src 内不得再出现指向根层
data/merged 的裸默认或硬编码 TSV 路径。
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
EXPECTED_DEFAULT = "data/merged/candidate_v3"

# 读取 KG_MERGED_DIR 的合法写法（environ.get / getenv 两种）
READERS = re.compile(
    r'(?:os\.environ\.get|_?os\.environ\.get|os\.getenv|_?os\.getenv)\(\s*["\']KG_MERGED_DIR["\']\s*,?\s*([^)]*)\)')

# 根层 data/merged 下的管线数据文件（禁止 src 硬编码引用——一律走 KG_MERGED_DIR）
ROOT_MERGED_FILES = re.compile(r'["\']data/merged/(merged_nodes|merged_edges|relation_assertions|graph_metrics|link_predictions|snapshot_manifest)[^"\']*["\']')


def test_all_kg_merged_dir_defaults_point_to_candidate_v3():
    offenders = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(errors="ignore")
        for m in READERS.finditer(text):
            default = m.group(1).strip()
            if not default:
                continue  # 无默认值=强制显式设置，合法
            if "candidate_v3" not in default.replace("'", "").replace('"', ""):
                offenders.append(f"{py.relative_to(SRC)}: 默认值 {default}")
    assert not offenders, "KG_MERGED_DIR 默认值不统一（应为 candidate_v3）:\n" + "\n".join(offenders)


def test_no_hardcoded_root_merged_paths_in_src():
    offenders = []
    for py in SRC.rglob("*.py"):
        if py.name.startswith("test_"):
            continue
        text = py.read_text(errors="ignore")
        for m in ROOT_MERGED_FILES.finditer(text):
            # candidate_v3 / candidate_v2 内的引用合法（测试固定数据）
            span = text[max(0, m.start() - 40):m.end()]
            if "candidate_" in span:
                continue
            offenders.append(f"{py.relative_to(SRC)}: {m.group(0)}")
    assert not offenders, "src 内存在根层 data/merged 硬编码（应走 KG_MERGED_DIR）:\n" + "\n".join(offenders)


if __name__ == "__main__":
    test_all_kg_merged_dir_defaults_point_to_candidate_v3()
    test_no_hardcoded_root_merged_paths_in_src()
    print("PASS: KG_MERGED_DIR 默认值全仓一致（candidate_v3），无根层硬编码")
